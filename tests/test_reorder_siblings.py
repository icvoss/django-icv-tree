"""Tests for reorder_siblings(): permute-within-occupied-slots.

reorder_siblings() permutes a named set of sibling rows across the
(order, path) slots those same rows already occupy. It never inserts,
removes, or renumbers a sibling list, so it never needs a rebuild(). Any
sibling not named is left byte-for-byte untouched.
"""

from __future__ import annotations

import pytest

from icv_tree.exceptions import TreeStructureError
from icv_tree.services import reorder_siblings


@pytest.mark.django_db
class TestReorderFullSiblingSet:
    """Permuting every sibling of a parent."""

    def test_full_set_reversed(self, make_node):
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)

        reorder_siblings(type(c1), ordered_ids=[c3.pk, c2.pk, c1.pk])

        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()

        # c3 now occupies the lowest slot (order 0), c1 the highest.
        assert (c3.order, c2.order, c1.order) == (0, 1, 2)
        assert c3.path == root.path + "/0001"
        assert c2.path == root.path + "/0002"
        assert c1.path == root.path + "/0003"
        # Depth is unchanged by a same-parent permutation.
        assert c1.depth == c2.depth == c3.depth == root.depth + 1

    def test_full_set_rotation(self, make_node):
        """A 3-cycle (rotation) is the case with no monotonic-safe write order."""
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)

        # c2 -> slot 0, c3 -> slot 1, c1 -> slot 2.
        reorder_siblings(type(c1), ordered_ids=[c2.pk, c3.pk, c1.pk])

        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()
        assert (c2.order, c3.order, c1.order) == (0, 1, 2)

    def test_roots_can_be_reordered(self, make_node):
        """parent=None (root-level siblings) is supported."""
        r1 = make_node("r1")
        r2 = make_node("r2")
        r3 = make_node("r3")

        reorder_siblings(type(r1), ordered_ids=[r3.pk, r1.pk, r2.pk])

        r1.refresh_from_db()
        r2.refresh_from_db()
        r3.refresh_from_db()
        assert (r3.order, r1.order, r2.order) == (0, 1, 2)
        assert r3.path == "0001"
        assert r1.path == "0002"
        assert r2.path == "0003"


@pytest.mark.django_db
class TestReorderStrictSubset:
    """Permuting a strict subset of a parent's children."""

    def test_subset_permuted_unlisted_siblings_untouched(self, make_node):
        """Unlisted siblings must be byte-identical: path, order, and depth."""
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)
        c4 = make_node("c4", parent=root)

        # Snapshot c2 and c4 (not listed) before the reorder.
        c2_before = (c2.path, c2.order, c2.depth)
        c4_before = (c4.path, c4.order, c4.depth)

        # Only c1 and c3 are named; they swap slots (order 0 and order 2).
        reorder_siblings(type(c1), ordered_ids=[c3.pk, c1.pk])

        c1.refresh_from_db()
        c3.refresh_from_db()
        assert c3.order == 0
        assert c1.order == 2
        assert c3.path == root.path + "/0001"
        assert c1.path == root.path + "/0003"

        # c2 and c4 were never named: untouched, byte-identical.
        c2.refresh_from_db()
        c4.refresh_from_db()
        assert (c2.path, c2.order, c2.depth) == c2_before
        assert (c4.path, c4.order, c4.depth) == c4_before

    def test_pure_two_sibling_swap_does_not_trip_unique_constraint(self, make_node):
        """Two siblings exchanging slots is the minimal case with no safe single pass."""
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)

        c1_path_before, c2_path_before = c1.path, c2.path

        # Must not raise IntegrityError even though c1 and c2's target paths
        # are each other's current, still-occupied, real path.
        reorder_siblings(type(c1), ordered_ids=[c2.pk, c1.pk])

        c1.refresh_from_db()
        c2.refresh_from_db()
        assert c2.path == c1_path_before
        assert c1.path == c2_path_before
        assert c2.order == 0
        assert c1.order == 1


@pytest.mark.django_db
class TestReorderDescendants:
    """Descendants of a permuted sibling must follow their ancestor's new path."""

    def test_descendant_paths_follow_permuted_ancestor(self, make_node):
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        gc1 = make_node("gc1", parent=c1)
        gc2 = make_node("gc2", parent=gc1)

        gc1_old_path = gc1.path
        gc2_old_path = gc2.path

        reorder_siblings(type(c1), ordered_ids=[c2.pk, c1.pk])

        c1.refresh_from_db()
        c2.refresh_from_db()
        gc1.refresh_from_db()
        gc2.refresh_from_db()

        assert c2.order == 0
        assert c1.order == 1
        # c1 moved from slot 0 to slot 1: its descendants' prefixes follow.
        assert gc1.path.startswith(c1.path + "/")
        assert gc2.path.startswith(gc1.path + "/")
        assert gc1.path != gc1_old_path
        assert gc2.path != gc2_old_path
        # Depth is unaffected: only the path prefix changed.
        assert gc1.depth == c1.depth + 1
        assert gc2.depth == gc1.depth + 1

    def test_unlisted_sibling_descendants_are_untouched(self, make_node):
        """Descendants of a sibling NOT named in ordered_ids must be untouched too."""
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)
        gc = make_node("gc", parent=c2)  # descendant of the UNLISTED sibling

        gc_before = (gc.path, gc.order, gc.depth)

        reorder_siblings(type(c1), ordered_ids=[c3.pk, c1.pk])

        gc.refresh_from_db()
        assert (gc.path, gc.order, gc.depth) == gc_before


@pytest.mark.django_db
class TestReorderValidation:
    """Validation errors, matching move_to's TreeStructureError family."""

    def test_duplicate_ids_raise(self, make_node):
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)

        with pytest.raises(TreeStructureError, match="duplicate"):
            reorder_siblings(type(c1), ordered_ids=[c1.pk, c2.pk, c1.pk])

    def test_unknown_id_raises(self, make_node, simple_tree_model):
        root = make_node("root")
        c1 = make_node("c1", parent=root)

        with pytest.raises(TreeStructureError, match="not find"):
            reorder_siblings(simple_tree_model, ordered_ids=[c1.pk, 999999])

    def test_ids_spanning_two_parents_raise(self, make_node):
        root1 = make_node("root1")
        root2 = make_node("root2")
        c1 = make_node("c1", parent=root1)
        c2 = make_node("c2", parent=root2)

        with pytest.raises(TreeStructureError, match="one parent"):
            reorder_siblings(type(c1), ordered_ids=[c1.pk, c2.pk])

    def test_empty_ordered_ids_raises(self, simple_tree_model):
        with pytest.raises(TreeStructureError, match="at least one"):
            reorder_siblings(simple_tree_model, ordered_ids=[])

    def test_single_id_is_a_noop(self, make_node):
        """A single-row 'permutation' has nothing to permute: succeeds, no-op."""
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        path_before, order_before = c1.path, c1.order

        reorder_siblings(type(c1), ordered_ids=[c1.pk])

        c1.refresh_from_db()
        assert c1.path == path_before
        assert c1.order == order_before

    def test_already_in_requested_order_is_a_noop(self, make_node, mocker):
        """Requesting the current order must not issue any write queries."""
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)

        bulk_update_spy = mocker.spy(type(c1)._default_manager, "bulk_update")
        reorder_siblings(type(c1), ordered_ids=[c1.pk, c2.pk])
        bulk_update_spy.assert_not_called()


@pytest.mark.django_db
class TestReorderScopedSiblings:
    """The motivating use case: reordering only one scope's rows in a shared list."""

    def test_reorders_only_the_named_scope_siblings(self, db):
        """Reordering scope A's roots must not touch scope B's roots at all."""
        from tree_testapp.models import Scope, ScopedTree

        scope_a = Scope.objects.create(name="A")
        scope_b = Scope.objects.create(name="B")

        a1 = ScopedTree.objects.create(name="a1", scope=scope_a)
        a2 = ScopedTree.objects.create(name="a2", scope=scope_a)
        b1 = ScopedTree.objects.create(name="b1", scope=scope_b)

        b1_before = (b1.path, b1.order, b1.depth)

        # a1 and a2 are both root-level (parent=None), sharing that "parent"
        # with b1, but only a1/a2 are named.
        reorder_siblings(ScopedTree, ordered_ids=[a2.pk, a1.pk])

        a1.refresh_from_db()
        a2.refresh_from_db()
        b1.refresh_from_db()
        assert a2.order == 0
        assert a1.order == 1
        assert (b1.path, b1.order, b1.depth) == b1_before


@pytest.mark.django_db
class TestReorderUUIDPrimaryKey:
    """UUID PKs are exercised elsewhere in this area (regression fixture for the

    prior sqlite UUID-bind bug, see UUIDTree's docstring): a raw uuid.UUID
    passed straight to cursor.execute() raised sqlite3.ProgrammingError.
    reorder_siblings() does not use raw SQL (it writes via bulk_update()),
    but the descendant-collection query filters on pk__in with UUID values,
    so it is worth covering directly.
    """

    def test_reorder_uuid_pk_siblings(self):
        from tree_testapp.models import UUIDTree

        root = UUIDTree.objects.create(name="root")
        c1 = UUIDTree.objects.create(name="c1", parent=root)
        c2 = UUIDTree.objects.create(name="c2", parent=root)
        c3 = UUIDTree.objects.create(name="c3", parent=root)

        reorder_siblings(UUIDTree, ordered_ids=[c3.pk, c1.pk, c2.pk])

        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()
        assert (c3.order, c1.order, c2.order) == (0, 1, 2)
        assert c3.path == root.path + "/0001"
        assert c1.path == root.path + "/0002"
        assert c2.path == root.path + "/0003"

    def test_reorder_uuid_pk_unknown_id_raises(self):
        import uuid

        from tree_testapp.models import UUIDTree

        root = UUIDTree.objects.create(name="root")
        c1 = UUIDTree.objects.create(name="c1", parent=root)

        with pytest.raises(TreeStructureError, match="not find"):
            reorder_siblings(UUIDTree, ordered_ids=[c1.pk, uuid.uuid4()])
