"""Tests for the PostgreSQL recursive-CTE rebuild fast path (_rebuild_cte).

Only runs against PostgreSQL: _rebuild_cte() is a Postgres-only optimisation
(ICV_TREE_ENABLE_CTE = True, connection.vendor == "postgresql"). Under the
default SQLite test backend, rebuild() silently falls through to the
standard BFS path, so these tests skip themselves rather than pass for the
wrong reason.
"""

from __future__ import annotations

import pytest
from django.db import connection

from icv_tree.services import rebuild

pytestmark = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="_rebuild_cte is PostgreSQL-only (ICV_TREE_ENABLE_CTE requires postgresql).",
)


@pytest.fixture
def scoped_tree_model():
    from tree_testapp.models import ScopedTree

    return ScopedTree


@pytest.fixture
def scope_model():
    from tree_testapp.models import Scope

    return Scope


@pytest.fixture
def two_scopes(db, scope_model):
    s1 = scope_model.objects.create(name="Scope A")
    s2 = scope_model.objects.create(name="Scope B")
    return s1, s2


@pytest.mark.django_db
class TestRebuildCteUnscoped:
    """CTE fast path on an unscoped model behaves like the standard path."""

    def test_cte_reconstructs_all_paths(self, settings, tree_nodes, simple_tree_model):
        settings.ICV_TREE_ENABLE_CTE = True

        for node in simple_tree_model.objects.all():
            simple_tree_model.objects.filter(pk=node.pk).update(path=f"CORRUPT_{node.pk}", depth=99, order=99)

        result = rebuild(simple_tree_model)

        from icv_tree.services import check_tree_integrity

        assert check_tree_integrity(simple_tree_model)["total_issues"] == 0
        assert result["nodes_updated"] > 0

    def test_cte_is_idempotent(self, settings, tree_nodes, simple_tree_model):
        settings.ICV_TREE_ENABLE_CTE = True

        rebuild(simple_tree_model)
        result2 = rebuild(simple_tree_model)
        assert result2["nodes_updated"] == 0


@pytest.mark.django_db
class TestRebuildCteScoped:
    """CTE fast path threads the scope filter through the recursive query."""

    def test_cte_scoped_rebuild_fixes_only_target_scope(self, settings, two_scopes, scoped_tree_model):
        settings.ICV_TREE_ENABLE_CTE = True

        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        a2 = scoped_tree_model.objects.create(name="A-2", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)

        for node in scoped_tree_model.objects.all():
            scoped_tree_model.objects.filter(pk=node.pk).update(path=f"CORRUPT_{node.pk}", depth=99, order=99)

        result = rebuild(scoped_tree_model, scope=s1)
        assert result["nodes_updated"] == 2

        a1.refresh_from_db()
        a2.refresh_from_db()
        assert sorted([a1.path, a2.path]) == ["0001", "0002"]

        # Scope B must be left exactly as corrupted, proving the CTE path's
        # scope filter reached both the SQL query and the Python-side load.
        b1.refresh_from_db()
        assert b1.path == f"CORRUPT_{b1.pk}"
        assert b1.depth == 99
        assert b1.order == 99

    def test_cte_scoped_rebuild_collision_safe_with_shared_paths(self, settings, two_scopes, scoped_tree_model):
        """Scope A's rebuild must not collide with scope B's untouched, identical path."""
        settings.ICV_TREE_ENABLE_CTE = True

        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)
        assert a1.path == b1.path == "0001"

        scoped_tree_model.objects.filter(pk=a1.pk).update(path=f"CORRUPT_{a1.pk}", depth=99, order=99)

        result = rebuild(scoped_tree_model, scope=s1)
        assert result["nodes_updated"] == 1

        a1.refresh_from_db()
        b1.refresh_from_db()
        assert a1.path == "0001"
        assert b1.path == "0001"

    def test_cte_scoped_rebuild_on_unscoped_model_raises(self, settings, tree_nodes, simple_tree_model):
        from django.core.exceptions import ImproperlyConfigured

        settings.ICV_TREE_ENABLE_CTE = True

        with pytest.raises(ImproperlyConfigured, match="tree_scope_field"):
            rebuild(simple_tree_model, scope="anything")

    def test_cte_signal_carries_scope(self, settings, two_scopes, scoped_tree_model, mocker):
        from icv_tree.signals import tree_rebuilt

        settings.ICV_TREE_ENABLE_CTE = True
        mocker.patch(
            "icv_tree.services.integrity.transaction.on_commit",
            side_effect=lambda fn: fn(),
        )

        s1, s2 = two_scopes
        scoped_tree_model.objects.create(name="A-1", scope=s1)
        scoped_tree_model.objects.filter(scope=s1).update(path="CORRUPT", depth=99, order=99)

        received = []

        def handler(sender, scope, **kwargs):  # type: ignore[no-untyped-def]
            received.append(scope)

        tree_rebuilt.connect(handler)
        try:
            rebuild(scoped_tree_model, scope=s1)
        finally:
            tree_rebuilt.disconnect(handler)

        assert len(received) == 1
        assert received[0] == s1
