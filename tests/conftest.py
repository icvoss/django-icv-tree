"""Shared pytest fixtures and configuration for icv-tree tests."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _clean_tree_testapp_tables(django_db_setup, django_db_blocker) -> None:  # type: ignore[no-untyped-def]
    """Clean tree_testapp tables at session start.

    With --reuse-db, the sandbox DB persists between pytest sessions. Prior
    sessions may have committed data (e.g. from transaction=True tests) that
    bleeds into subsequent sessions. Cleaning here ensures a clean slate
    for the current session.

    Uses TRUNCATE ... CASCADE on PostgreSQL (fast, resets sequences) and
    DELETE FROM on other databases (SQLite, etc.).
    """
    with django_db_blocker.unblock():
        from django.db import connection

        tables = ("tree_testapp_optouttree", "tree_testapp_simpletree")
        is_pg = connection.vendor == "postgresql"

        with connection.cursor() as cursor:
            for table in tables:
                try:
                    if is_pg:
                        cursor.execute(
                            f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"  # noqa: S608
                        )
                    else:
                        cursor.execute(f"DELETE FROM {table}")  # noqa: S608
                except Exception:  # noqa: BLE001
                    # Table may not exist yet (first run before syncdb).
                    pass


def pytest_configure(config) -> None:  # type: ignore[no-untyped-def]
    """Ensure icv_tree and tree_testapp are in INSTALLED_APPS."""
    from django.conf import settings

    if not settings.configured:
        return

    for app in ("icv_tree", "tree_testapp"):
        if app not in settings.INSTALLED_APPS:
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, app]

    if not hasattr(settings, "MIGRATION_MODULES"):
        settings.MIGRATION_MODULES = {}
    settings.MIGRATION_MODULES.setdefault("icv_tree", None)
    settings.MIGRATION_MODULES.setdefault("tree_testapp", None)

    # Ensure icv-tree settings have sensible test defaults.
    defaults = {
        "ICV_TREE_PATH_SEPARATOR": "/",
        "ICV_TREE_STEP_LENGTH": 4,
        "ICV_TREE_MAX_PATH_LENGTH": 255,
        "ICV_TREE_ENABLE_CTE": False,
        "ICV_TREE_REBUILD_BATCH_SIZE": 1000,
        "ICV_TREE_CHECK_ON_SAVE": False,
    }
    for key, value in defaults.items():
        if not hasattr(settings, key):
            setattr(settings, key, value)


@pytest.fixture
def simple_tree_model():
    """Return the SimpleTree model class."""
    from tree_testapp.models import SimpleTree

    return SimpleTree


@pytest.fixture
def make_node(db, simple_tree_model):
    """Factory function for creating SimpleTree nodes.

    Usage::

        root = make_node("Root")
        child = make_node("Child", parent=root)
    """

    def _make(name: str, parent=None, **kwargs):  # type: ignore[no-untyped-def]
        node = simple_tree_model(name=name, parent=parent, **kwargs)
        node.save()
        node.refresh_from_db()
        return node

    return _make


@pytest.fixture
def tree_nodes(db, make_node):
    """Create a standard 3-level test tree and return a dict of nodes.

    Structure::

        root1
        ├── child1
        │   ├── grandchild1
        │   └── grandchild2
        └── child2
        root2
    """
    root1 = make_node("root1")
    child1 = make_node("child1", parent=root1)
    grandchild1 = make_node("grandchild1", parent=child1)
    grandchild2 = make_node("grandchild2", parent=child1)
    child2 = make_node("child2", parent=root1)
    root2 = make_node("root2")

    return {
        "root1": root1,
        "child1": child1,
        "grandchild1": grandchild1,
        "grandchild2": grandchild2,
        "child2": child2,
        "root2": root2,
    }
