"""Tests for tree_scope_field — independent path numbering per scope.

Verifies that when a TreeNode subclass sets tree_scope_field, path
auto-assignment and rebuild() scope sibling counts so that each scope
value gets independent path numbering starting at 0001.
"""

from __future__ import annotations

import pytest

from icv_tree.services import check_tree_integrity, rebuild


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
    """Create two scope instances."""
    s1 = scope_model.objects.create(name="Scope A")
    s2 = scope_model.objects.create(name="Scope B")
    return s1, s2


@pytest.mark.django_db
class TestScopedPathAssignment:
    """Test that save() assigns paths independently per scope."""

    def test_roots_in_different_scopes_get_same_path(self, two_scopes, scoped_tree_model):
        """Root nodes in different scopes should both get path '0001'."""
        s1, s2 = two_scopes
        n1 = scoped_tree_model.objects.create(name="A-root", scope=s1)
        n2 = scoped_tree_model.objects.create(name="B-root", scope=s2)
        n1.refresh_from_db()
        n2.refresh_from_db()
        assert n1.path == "0001"
        assert n2.path == "0001"

    def test_multiple_roots_per_scope_numbered_independently(self, two_scopes, scoped_tree_model):
        """Each scope's roots should be numbered sequentially within that scope."""
        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        a2 = scoped_tree_model.objects.create(name="A-2", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)
        a1.refresh_from_db()
        a2.refresh_from_db()
        b1.refresh_from_db()
        assert a1.path == "0001"
        assert a2.path == "0002"
        assert b1.path == "0001"

    def test_children_get_correct_paths_across_scopes(self, two_scopes, scoped_tree_model):
        """Children within each scope should have correct nested paths."""
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)
        a_child = scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_child = scoped_tree_model.objects.create(name="B-child", scope=s2, parent=b_root)
        a_child.refresh_from_db()
        b_child.refresh_from_db()
        assert a_child.path == "0001/0001"
        assert b_child.path == "0001/0001"


@pytest.mark.django_db
class TestScopedRebuild:
    """Test that rebuild() numbers paths independently per scope."""

    def test_rebuild_assigns_independent_paths_per_scope(self, two_scopes, scoped_tree_model):
        """After corrupting paths, rebuild should restore independent numbering per scope."""
        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        a2 = scoped_tree_model.objects.create(name="A-2", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)
        b2 = scoped_tree_model.objects.create(name="B-2", scope=s2)

        # Corrupt all paths with unique values.
        for node in scoped_tree_model.objects.all():
            scoped_tree_model.objects.filter(pk=node.pk).update(
                path=f"CORRUPT_{node.pk}", depth=99, order=99,
            )

        result = rebuild(scoped_tree_model)
        assert result["nodes_updated"] == 4

        a1.refresh_from_db()
        a2.refresh_from_db()
        b1.refresh_from_db()
        b2.refresh_from_db()

        # Each scope should have paths 0001, 0002 independently.
        scope_a_paths = sorted([a1.path, a2.path])
        scope_b_paths = sorted([b1.path, b2.path])
        assert scope_a_paths == ["0001", "0002"]
        assert scope_b_paths == ["0001", "0002"]

    def test_rebuild_is_idempotent_with_scopes(self, two_scopes, scoped_tree_model):
        """Running rebuild twice on scoped trees should produce 0 updates the second time."""
        s1, s2 = two_scopes
        scoped_tree_model.objects.create(name="A-1", scope=s1)
        scoped_tree_model.objects.create(name="B-1", scope=s2)
        rebuild(scoped_tree_model)
        result2 = rebuild(scoped_tree_model)
        assert result2["nodes_updated"] == 0

    def test_rebuild_with_hierarchy_across_scopes(self, two_scopes, scoped_tree_model):
        """Rebuild should handle hierarchical trees correctly within each scope."""
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)
        scoped_tree_model.objects.create(name="B-child", scope=s2, parent=b_root)

        # Corrupt and rebuild.
        for node in scoped_tree_model.objects.all():
            scoped_tree_model.objects.filter(pk=node.pk).update(
                path=f"CORRUPT_{node.pk}", depth=99, order=99,
            )

        rebuild(scoped_tree_model)

        a_root.refresh_from_db()
        a_child = scoped_tree_model.objects.get(name="A-child")
        b_root.refresh_from_db()
        b_child = scoped_tree_model.objects.get(name="B-child")

        assert a_root.path == "0001"
        assert a_child.path == "0001/0001"
        assert b_root.path == "0001"
        assert b_child.path == "0001/0001"


@pytest.mark.django_db
class TestUnscopedBackwardsCompatibility:
    """Ensure models without tree_scope_field continue to work as before."""

    def test_unscoped_model_paths_are_globally_sequential(self, make_node, simple_tree_model):
        """SimpleTree (no scope) should assign paths globally."""
        r1 = make_node("root1")
        r2 = make_node("root2")
        assert r1.path == "0001"
        assert r2.path == "0002"

    def test_unscoped_rebuild_works(self, tree_nodes, simple_tree_model):
        """Rebuild on an unscoped model should work unchanged."""
        result = rebuild(simple_tree_model)
        assert result["nodes_updated"] == 0 or result["nodes_unchanged"] > 0
