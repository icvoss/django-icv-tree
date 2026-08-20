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
                path=f"CORRUPT_{node.pk}",
                depth=99,
                order=99,
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
                path=f"CORRUPT_{node.pk}",
                depth=99,
                order=99,
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
class TestScopedIntegrity:
    """Test that check_tree_integrity() respects tree_scope_field."""

    def test_no_false_positive_duplicates_across_scopes(self, two_scopes, scoped_tree_model):
        """Identical paths in different scopes should NOT be flagged as duplicates."""
        s1, s2 = two_scopes
        scoped_tree_model.objects.create(name="A-root", scope=s1)
        scoped_tree_model.objects.create(name="B-root", scope=s2)

        result = check_tree_integrity(scoped_tree_model)
        assert result["duplicate_paths"] == []
        assert result["total_issues"] == 0

    def test_many_scopes_with_shared_paths_no_false_positives(self, scoped_tree_model, scope_model):
        """Paths reused across many scopes (like vocabulary terms) should be clean."""
        scopes = [scope_model.objects.create(name=f"Scope-{i}") for i in range(10)]
        for s in scopes:
            scoped_tree_model.objects.create(name=f"root-{s.pk}", scope=s)

        result = check_tree_integrity(scoped_tree_model)
        assert result["duplicate_paths"] == []
        assert result["total_issues"] == 0


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


@pytest.mark.django_db
class TestRebuildScopeParameter:
    """Test rebuild(model, scope=...), issue #7.

    A scoped rebuild must repair only the target scope's tree, leaving
    every other scope's rows byte-identical (path, depth, and order all
    untouched), and must reject a scope argument on an unscoped model.
    """

    def test_scoped_rebuild_fixes_only_target_scope(self, two_scopes, scoped_tree_model):
        """rebuild(scope=s1) should repair only scope A's corrupted nodes."""
        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        a2 = scoped_tree_model.objects.create(name="A-2", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)
        b2 = scoped_tree_model.objects.create(name="B-2", scope=s2)

        for node in scoped_tree_model.objects.all():
            scoped_tree_model.objects.filter(pk=node.pk).update(
                path=f"CORRUPT_{node.pk}",
                depth=99,
                order=99,
            )

        result = rebuild(scoped_tree_model, scope=s1)
        assert result["nodes_updated"] == 2

        a1.refresh_from_db()
        a2.refresh_from_db()
        scope_a_paths = sorted([a1.path, a2.path])
        assert scope_a_paths == ["0001", "0002"]
        assert a1.depth == 0
        assert a2.depth == 0

        # Scope B was corrupted too, but not passed to rebuild(): its rows
        # must be byte-identical to the corrupted values, untouched.
        b1.refresh_from_db()
        b2.refresh_from_db()
        assert b1.path == f"CORRUPT_{b1.pk}"
        assert b1.depth == 99
        assert b1.order == 99
        assert b2.path == f"CORRUPT_{b2.pk}"
        assert b2.depth == 99
        assert b2.order == 99

    def test_scoped_rebuild_leaves_other_scope_byte_identical_with_hierarchy(self, two_scopes, scoped_tree_model):
        """A scoped rebuild must not touch another scope's hierarchy, at all."""
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)
        b_child = scoped_tree_model.objects.create(name="B-child", scope=s2, parent=b_root)

        # Snapshot scope B's fields before the scoped rebuild runs.
        b_root_before = (b_root.path, b_root.depth, b_root.order)
        b_child_before = (b_child.path, b_child.depth, b_child.order)

        # Corrupt every row (both scopes) so a leaked write would be visible.
        for node in scoped_tree_model.objects.all():
            scoped_tree_model.objects.filter(pk=node.pk).update(
                path=f"CORRUPT_{node.pk}",
                depth=99,
                order=99,
            )

        rebuild(scoped_tree_model, scope=s1)

        b_root.refresh_from_db()
        b_child.refresh_from_db()
        # Scope B was corrupted above and never repaired: its fields must
        # still match the corrupted snapshot, not the pre-corruption values,
        # proving the scoped rebuild never wrote to scope B at all.
        assert (b_root.path, b_root.depth, b_root.order) == (f"CORRUPT_{b_root.pk}", 99, 99)
        assert (b_child.path, b_child.depth, b_child.order) == (f"CORRUPT_{b_child.pk}", 99, 99)
        assert (b_root.path, b_root.depth, b_root.order) != b_root_before
        assert (b_child.path, b_child.depth, b_child.order) != b_child_before

    def test_scoped_rebuild_is_collision_safe_with_shared_paths(self, two_scopes, scoped_tree_model):
        """Rebuilding scope A must not collide with scope B's identical, untouched paths.

        Regression for the uniqueness-constraint interaction: ScopedTree's
        constraint is (scope, path), not path alone, so scope A's transient
        placeholder values during rebuild can never collide with scope B's
        real, untouched path '0001'.
        """
        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)
        assert a1.path == b1.path == "0001"

        scoped_tree_model.objects.filter(pk=a1.pk).update(path=f"CORRUPT_{a1.pk}", depth=99, order=99)

        # Must not raise IntegrityError even though scope B already holds path '0001'.
        result = rebuild(scoped_tree_model, scope=s1)
        assert result["nodes_updated"] == 1

        a1.refresh_from_db()
        b1.refresh_from_db()
        assert a1.path == "0001"
        assert b1.path == "0001"

    def test_scoped_rebuild_on_unscoped_model_raises(self, tree_nodes, simple_tree_model):
        """Passing scope= to a model without tree_scope_field must raise."""
        from django.core.exceptions import ImproperlyConfigured

        with pytest.raises(ImproperlyConfigured, match="tree_scope_field"):
            rebuild(simple_tree_model, scope="anything")

    def test_default_full_rebuild_unchanged(self, two_scopes, scoped_tree_model):
        """rebuild() with no scope argument must still repair every scope."""
        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)

        for node in scoped_tree_model.objects.all():
            scoped_tree_model.objects.filter(pk=node.pk).update(
                path=f"CORRUPT_{node.pk}",
                depth=99,
                order=99,
            )

        result = rebuild(scoped_tree_model)
        assert result["nodes_updated"] == 2

        a1.refresh_from_db()
        b1.refresh_from_db()
        assert a1.path == "0001"
        assert b1.path == "0001"

    def test_scoped_rebuild_signal_carries_scope(self, two_scopes, scoped_tree_model, mocker):
        """tree_rebuilt should carry the scope value the rebuild was restricted to."""
        from icv_tree.signals import tree_rebuilt

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

    def test_full_rebuild_signal_scope_is_none(self, tree_nodes, simple_tree_model, mocker):
        """tree_rebuilt should carry scope=None for a default, unscoped rebuild."""
        from icv_tree.signals import tree_rebuilt

        mocker.patch(
            "icv_tree.services.integrity.transaction.on_commit",
            side_effect=lambda fn: fn(),
        )

        received = []

        def handler(sender, scope, **kwargs):  # type: ignore[no-untyped-def]
            received.append(scope)

        tree_rebuilt.connect(handler)
        try:
            simple_tree_model.objects.rebuild()
        finally:
            tree_rebuilt.disconnect(handler)

        assert len(received) == 1
        assert received[0] is None


@pytest.mark.django_db
class TestScopedTraversalIsolation:
    """Regression for icvoss/django-icv-tree#20.

    get_ancestors()/get_descendants() filtered on path alone, with no
    tree_scope_field predicate. Because paths are numbered independently
    per scope (see TestScopedPathAssignment above), two scopes' first
    roots both get path '0001', so an unscoped path__in / path__startswith
    lookup on scope A's node can return scope B's rows. In a multi-tenant
    consumer (icv-cms Page.full_path walks get_ancestors()) this is a
    cross-tenant data leak, not just wrong ordering.
    """

    def test_get_ancestors_never_returns_another_scope(self, two_scopes, scoped_tree_model):
        """A colliding path in scope B must never appear in scope A's ancestors."""
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        a_child = scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)
        scoped_tree_model.objects.create(name="B-child", scope=s2, parent=b_root)

        # Sanity: the collision this bug depends on is real.
        assert a_root.path == b_root.path == "0001"

        ancestors = list(a_child.get_ancestors())
        assert ancestors == [a_root]
        assert b_root not in ancestors

    def test_get_ancestors_include_self_never_returns_another_scope(self, two_scopes, scoped_tree_model):
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        a_child = scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)

        ancestors = list(a_child.get_ancestors(include_self=True))
        assert ancestors == [a_root, a_child]
        assert b_root not in ancestors

    def test_get_descendants_never_returns_another_scope(self, two_scopes, scoped_tree_model):
        """A colliding path prefix in scope B must never appear in scope A's descendants."""
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        a_child = scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)
        b_child = scoped_tree_model.objects.create(name="B-child", scope=s2, parent=b_root)

        assert a_root.path == b_root.path == "0001"
        assert a_child.path == b_child.path == "0001/0001"

        descendants = list(a_root.get_descendants())
        assert descendants == [a_child]
        assert b_child not in descendants

    def test_get_descendants_include_self_never_returns_another_scope(self, two_scopes, scoped_tree_model):
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        a_child = scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)

        descendants = list(a_root.get_descendants(include_self=True))
        assert descendants == [a_root, a_child]
        assert b_root not in descendants

    def test_get_root_never_returns_another_scope(self, two_scopes, scoped_tree_model):
        """get_root() on a non-root node must resolve within its own scope.

        Before the fix this raises MultipleObjectsReturned as soon as two
        scopes' root paths collide, because .get(path=root_path) is
        unscoped against a (scope, path) unique-together constraint.
        """
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        a_child = scoped_tree_model.objects.create(name="A-child", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)

        assert a_root.path == b_root.path == "0001"
        assert a_child.get_root() == a_root

    def test_unscoped_model_traversal_unaffected(self, tree_nodes, simple_tree_model):
        """Models with no tree_scope_field must query exactly as before.

        tree_nodes is the shared fixture used throughout test_models.py; this
        only proves the scope-aware branch never runs for an unscoped model.
        """
        root = simple_tree_model.objects.filter(parent__isnull=True).order_by("path").first()
        assert root is not None
        # Just exercising the traversal paths for a model with no
        # tree_scope_field must not raise and must not filter unexpectedly.
        list(root.get_descendants())
        for child in root.get_children():
            list(child.get_ancestors())
            child.get_root()


@pytest.mark.django_db
class TestScopedSiblingReorderAfterDeletion:
    """Regression for icvoss/django-icv-tree#20 (audit finding).

    handle_post_delete() calls _reorder_siblings_after_removal() with no
    scope_filter, even though the function supports one and handlers.py's
    own pre_save root-move branch already builds one. Root nodes all share
    parent_id IS NULL regardless of scope, so deleting a root in scope A
    decremented `order` for every scope's root siblings after that
    position, corrupting scope B's sibling ordering (and, on the next
    insert, its path assignment) even though scope B was never touched by
    the delete.
    """

    def test_deleting_a_root_does_not_reorder_another_scopes_roots(self, two_scopes, scoped_tree_model):
        s1, s2 = two_scopes
        a1 = scoped_tree_model.objects.create(name="A-1", scope=s1)
        a2 = scoped_tree_model.objects.create(name="A-2", scope=s1)
        b1 = scoped_tree_model.objects.create(name="B-1", scope=s2)
        b2 = scoped_tree_model.objects.create(name="B-2", scope=s2)
        b3 = scoped_tree_model.objects.create(name="B-3", scope=s2)

        assert (b1.order, b2.order, b3.order) == (0, 1, 2)

        # Delete scope A's first root (order=0). This must never touch
        # scope B's sibling ordering.
        a1.delete()

        b1.refresh_from_db()
        b2.refresh_from_db()
        b3.refresh_from_db()
        assert (b1.order, b2.order, b3.order) == (0, 1, 2)

        # Scope A's own remaining sibling must still have its order closed up.
        a2.refresh_from_db()
        assert a2.order == 0

    def test_deleting_a_non_root_child_does_not_reorder_another_scopes_siblings(self, two_scopes, scoped_tree_model):
        """Non-root deletion is already scope-safe (parent_id pins the scope);
        this pins that behaviour so a future change cannot regress it.
        """
        s1, s2 = two_scopes
        a_root = scoped_tree_model.objects.create(name="A-root", scope=s1)
        a_c1 = scoped_tree_model.objects.create(name="A-c1", scope=s1, parent=a_root)
        a_c2 = scoped_tree_model.objects.create(name="A-c2", scope=s1, parent=a_root)
        b_root = scoped_tree_model.objects.create(name="B-root", scope=s2)
        b_c1 = scoped_tree_model.objects.create(name="B-c1", scope=s2, parent=b_root)

        a_c1.delete()

        a_c2.refresh_from_db()
        assert a_c2.order == 0

        b_c1.refresh_from_db()
        assert b_c1.order == 0
