"""Regression tests for icv_tree.operations.PathIndex (icvoss/django-icv-tree#37).

PathIndex.database_forwards / database_backwards used to call
schema_editor.execute(sql) unconditionally, never consulting
self.allow_migrate_model(). A router that refuses an app on a given
database alias therefore could not stop PathIndex from running its
CREATE INDEX / DROP INDEX there, unlike every built-in schema operation
(AddIndex, CreateModel, ...), which all honour the router.

These tests call PathIndex.database_forwards/database_backwards directly
against a real schema_editor for the "other" alias (configured in
tests/settings.py) so the guard is proven against actual SQL execution,
not just a mocked call.
"""

from __future__ import annotations

import pytest
from django.apps import apps as global_apps
from django.db import connections, router
from django.db.migrations.state import ProjectState

from icv_tree.operations import PathIndex


def _index_exists(alias: str, index_name: str) -> bool:
    """Return True if a sqlite index with this name exists on the given alias."""
    with connections[alias].cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = %s",
            [index_name],
        )
        return cursor.fetchone() is not None


class _RefuseTreeTestapp:
    """Router that refuses migrations for tree_testapp on a named alias."""

    def __init__(self, refused_alias: str) -> None:
        self.refused_alias = refused_alias

    def allow_migrate(self, db, app_label, model_name=None, **hints):  # type: ignore[no-untyped-def]
        if db == self.refused_alias and app_label == "tree_testapp":
            return False
        return None


class _AllowAll:
    """Router that explicitly allows every migration (baseline control)."""

    def allow_migrate(self, db, app_label, model_name=None, **hints):  # type: ignore[no-untyped-def]
        return True


@pytest.fixture
def _patched_routers():
    """Directly patch django.db.router's cached routers list and restore it.

    override_settings(DATABASE_ROUTERS=...) round-trips through Django's
    setting_changed signal, which is version-sensitive; patching the
    ConnectionRouter's cached `routers` property directly is the documented,
    version-independent way to control routing in one test without
    depending on that signal wiring.
    """
    original = router.routers

    def _set(routers):  # type: ignore[no-untyped-def]
        router.routers = routers

    yield _set

    router.routers = original


@pytest.fixture
def _built_state():
    """A ProjectState built from the real, installed tree_testapp app config."""
    return ProjectState.from_apps(global_apps)


@pytest.mark.django_db(databases=["other"], transaction=True)
class TestPathIndexRouterGuard:
    """Prove PathIndex consults allow_migrate_model before executing SQL."""

    def test_forwards_skips_sql_when_router_refuses(self, _patched_routers, _built_state):
        """With a refusing router, database_forwards must not create the index.

        Fails on unfixed code: the old database_forwards ignores the router
        entirely and calls schema_editor.execute() unconditionally, so the
        index is created regardless of what the router says. This assertion
        (index absent) only holds once allow_migrate_model() is consulted.
        """
        _patched_routers([_RefuseTreeTestapp(refused_alias="other")])

        operation = PathIndex(model_name="simpletree", field_name="path")
        index_name = operation.index_name

        with connections["other"].schema_editor() as schema_editor:
            operation.database_forwards("tree_testapp", schema_editor, _built_state, _built_state)

        assert not _index_exists("other", index_name)

    def test_forwards_creates_sql_when_router_allows(self, _patched_routers, _built_state):
        """With an allowing router, database_forwards must create the index.

        Control for the test above: proves the guard is not simply always
        skipping. Also fails on unfixed code in the opposite way if the
        model resolution itself were broken, since this exercises the same
        to_state.apps.get_model() path added by the fix.
        """
        _patched_routers([_AllowAll()])

        operation = PathIndex(model_name="simpletree", field_name="path")
        index_name = operation.index_name

        with connections["other"].schema_editor() as schema_editor:
            operation.database_forwards("tree_testapp", schema_editor, _built_state, _built_state)

        assert _index_exists("other", index_name)

    def test_backwards_skips_sql_when_router_refuses(self, _patched_routers, _built_state):
        """With a refusing router, database_backwards must not drop the index.

        Fails on unfixed code: the old database_backwards has no guard at
        all, so it always issues DROP INDEX IF EXISTS regardless of the
        router. Proven here by first creating the index while migrations
        are allowed, then asserting it survives a refused database_backwards
        call.
        """
        _patched_routers([_AllowAll()])
        operation = PathIndex(model_name="simpletree", field_name="path")
        index_name = operation.index_name

        with connections["other"].schema_editor() as schema_editor:
            operation.database_forwards("tree_testapp", schema_editor, _built_state, _built_state)
        assert _index_exists("other", index_name)

        _patched_routers([_RefuseTreeTestapp(refused_alias="other")])

        with connections["other"].schema_editor() as schema_editor:
            operation.database_backwards("tree_testapp", schema_editor, _built_state, _built_state)

        assert _index_exists("other", index_name)

    def test_backwards_drops_sql_when_router_allows(self, _patched_routers, _built_state):
        """With an allowing router, database_backwards must drop the index.

        Control for the test above: proves database_backwards still does its
        job once permitted, using the same from_state.apps.get_model() path
        added by the fix.
        """
        _patched_routers([_AllowAll()])
        operation = PathIndex(model_name="simpletree", field_name="path")
        index_name = operation.index_name

        with connections["other"].schema_editor() as schema_editor:
            operation.database_forwards("tree_testapp", schema_editor, _built_state, _built_state)
        assert _index_exists("other", index_name)

        with connections["other"].schema_editor() as schema_editor:
            operation.database_backwards("tree_testapp", schema_editor, _built_state, _built_state)

        assert not _index_exists("other", index_name)


class _FakeConnection:
    """Stand-in connection exposing only what PathIndex reads from it."""

    def __init__(self, alias: str, vendor: str) -> None:
        self.alias = alias
        self.vendor = vendor


class _FakeSchemaEditor:
    """Spy schema_editor recording the SQL it was asked to execute."""

    def __init__(self, connection) -> None:  # type: ignore[no-untyped-def]
        self.connection = connection
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)


@pytest.mark.django_db
class TestPathIndexVendorFromSchemaEditor:
    """PathIndex must read the vendor from schema_editor.connection, not the
    module-level default connection (icvoss/django-icv-tree#37, related).
    """

    def test_uses_postgresql_opclass_for_non_default_postgresql_alias(self, _built_state):
        """A fake schema_editor claiming a postgresql connection must produce
        the text_pattern_ops SQL, even though the real default alias under
        test is sqlite.

        Fails on unfixed code: the old implementation reads the module-level
        `django.db.connection` (the default alias, sqlite here), so it always
        emits the plain-index branch and never the postgresql branch,
        regardless of what schema_editor.connection.vendor says.
        """
        operation = PathIndex(model_name="simpletree", field_name="path")
        fake_connection = _FakeConnection(alias="default", vendor="postgresql")
        fake_schema_editor = _FakeSchemaEditor(fake_connection)

        operation.database_forwards("tree_testapp", fake_schema_editor, _built_state, _built_state)

        assert len(fake_schema_editor.executed) == 1
        assert "text_pattern_ops" in fake_schema_editor.executed[0]
