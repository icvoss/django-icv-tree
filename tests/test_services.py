"""Tests for move_to, rebuild, and check_tree_integrity services."""

from __future__ import annotations

import pytest
from django.db import connection


@pytest.mark.django_db
class TestMoveToFirstChild:
    """Test move_to with position='first-child'."""

    def test_move_to_first_child_updates_path(self, tree_nodes, make_node):
        """Moving to first-child should update the moved node's path."""
        root2 = tree_nodes["root2"]
        root1 = tree_nodes["root1"]
        root2.move_to(root1, "first-child")
        root2.refresh_from_db()
        assert root2.path.startswith(root1.path + "/")
        assert root2.order == 0

    def test_move_to_first_child_shifts_existing_children(self, tree_nodes):
        """Existing first child should shift to order=1 after first-child insert."""
        root1 = tree_nodes["root1"]
        root2 = tree_nodes["root2"]
        child1 = tree_nodes["child1"]

        root2.move_to(root1, "first-child")
        child1.refresh_from_db()
        assert child1.order == 1

    def test_move_to_last_child_appends(self, tree_nodes):
        """Moving to last-child should place node after existing children."""
        root2 = tree_nodes["root2"]
        root1 = tree_nodes["root1"]
        # root1 already has 2 children (child1, child2)
        root2.move_to(root1, "last-child")
        root2.refresh_from_db()
        assert root2.order == 2  # 0-based; third child

    def test_move_to_recomputes_descendant_paths(self, tree_nodes):
        """All descendants should have their paths updated after a move."""
        child1 = tree_nodes["child1"]
        root2 = tree_nodes["root2"]

        child1.move_to(root2, "first-child")
        child1.refresh_from_db()

        grandchild1 = tree_nodes["grandchild1"]
        grandchild2 = tree_nodes["grandchild2"]
        grandchild1.refresh_from_db()
        grandchild2.refresh_from_db()

        # Both grandchildren must now start with child1's new path.
        assert grandchild1.path.startswith(child1.path + "/")
        assert grandchild2.path.startswith(child1.path + "/")


@pytest.mark.django_db
class TestMoveToLeftRight:
    """Test move_to with position='left' and 'right'."""

    def test_move_to_left_inserts_before_target(self, tree_nodes):
        """'left' should insert the node immediately before the target sibling."""
        root2 = tree_nodes["root2"]
        child2 = tree_nodes["child2"]

        # Move root2 as a child of root1, left of child2.
        root2.move_to(child2, "left")
        root2.refresh_from_db()
        child2.refresh_from_db()

        assert root2.parent_id == child2.parent_id
        assert root2.order < child2.order

    def test_move_to_right_inserts_after_target(self, tree_nodes):
        """'right' should insert the node immediately after the target sibling."""
        root2 = tree_nodes["root2"]
        child1 = tree_nodes["child1"]

        root2.move_to(child1, "right")
        root2.refresh_from_db()
        child1.refresh_from_db()

        assert root2.parent_id == child1.parent_id
        assert root2.order > child1.order


@pytest.mark.django_db
class TestMoveToValidation:
    """Test move_to validation and error cases."""

    def test_move_to_raises_on_invalid_position(self, tree_nodes):
        """Invalid position should raise TreeStructureError."""
        from icv_tree.exceptions import TreeStructureError

        root1 = tree_nodes["root1"]
        root2 = tree_nodes["root2"]
        with pytest.raises(TreeStructureError, match="invalid-position"):
            root1.move_to(root2, "invalid-position")

    def test_move_to_raises_on_cycle_self(self, tree_nodes):
        """Moving a node to itself should raise TreeStructureError."""
        from icv_tree.exceptions import TreeStructureError

        root1 = tree_nodes["root1"]
        with pytest.raises(TreeStructureError):
            root1.move_to(root1, "first-child")

    def test_move_to_raises_on_cycle_descendant(self, tree_nodes):
        """Moving a node under its own descendant should raise TreeStructureError."""
        from icv_tree.exceptions import TreeStructureError

        root1 = tree_nodes["root1"]
        grandchild1 = tree_nodes["grandchild1"]
        with pytest.raises(TreeStructureError):
            root1.move_to(grandchild1, "last-child")

    def test_move_to_noop_when_same_position(self, tree_nodes, simple_tree_model):
        """Moving a node to its current position should be a no-op (no DB writes)."""
        child1 = tree_nodes["child1"]
        root1 = tree_nodes["root1"]

        # child1 is already first-child of root1 (order=0).
        original_path = child1.path
        child1.move_to(root1, "first-child")
        child1.refresh_from_db()
        assert child1.path == original_path

    def test_move_to_reorders_source_siblings(self, tree_nodes):
        """Siblings after the removed position should have decremented order."""
        child1 = tree_nodes["child1"]
        child2 = tree_nodes["child2"]
        root2 = tree_nodes["root2"]

        # child1 is at order=0, child2 at order=1.
        child1.move_to(root2, "last-child")
        child2.refresh_from_db()
        assert child2.order == 0  # was 1, should now be 0


@pytest.mark.django_db
class TestMoveToSignal:
    """Test that move_to emits node_moved signal correctly.

    Patches transaction.on_commit to call the callback immediately so
    signals fire within the non-transactional test boundary.
    """

    def test_move_to_emits_node_moved_signal(self, tree_nodes, mocker):
        """node_moved signal should be emitted after move_to."""
        from icv_tree.signals import node_moved

        # Patch on_commit to call callback immediately.
        mocker.patch(
            "icv_tree.services.mutations.transaction.on_commit",
            side_effect=lambda fn: fn(),
        )

        received = []

        def handler(sender, **kwargs):  # type: ignore[no-untyped-def]
            received.append(kwargs)

        node_moved.connect(handler)
        try:
            child1 = tree_nodes["child1"]
            root2 = tree_nodes["root2"]
            child1.move_to(root2, "last-child")
        finally:
            node_moved.disconnect(handler)

        assert len(received) == 1

    def test_node_moved_signal_carries_correct_kwargs(self, tree_nodes, mocker):
        """node_moved signal should carry instance, old_parent, new_parent, old_path."""
        from icv_tree.signals import node_moved

        mocker.patch(
            "icv_tree.services.mutations.transaction.on_commit",
            side_effect=lambda fn: fn(),
        )

        received = []

        def handler(sender, **kwargs):  # type: ignore[no-untyped-def]
            received.append(kwargs)

        node_moved.connect(handler)
        try:
            child1 = tree_nodes["child1"]
            root1 = tree_nodes["root1"]
            root2 = tree_nodes["root2"]
            old_path = child1.path
            child1.move_to(root2, "last-child")
        finally:
            node_moved.disconnect(handler)

        assert len(received) == 1
        kwargs = received[0]
        assert "instance" in kwargs
        assert "old_parent" in kwargs
        assert "new_parent" in kwargs
        assert "old_path" in kwargs
        assert kwargs["old_path"] == old_path
        assert kwargs["old_parent"].pk == root1.pk
        assert kwargs["new_parent"].pk == root2.pk


@pytest.mark.django_db
class TestRebuild:
    """Test rebuild() service.

    Patches transaction.on_commit to fire signals immediately within tests.
    """

    def test_rebuild_reconstructs_all_paths(self, tree_nodes, simple_tree_model):
        """After corrupting paths, rebuild() should restore them correctly."""
        # Corrupt paths per-node using unique suffixes (unique constraint on path
        # prevents bulk UPDATE to the same string value).
        for node in simple_tree_model.objects.all():
            simple_tree_model.objects.filter(pk=node.pk).update(path=f"CORRUPT_{node.pk}", depth=99, order=99)

        result = simple_tree_model.objects.rebuild()

        from icv_tree.services import check_tree_integrity

        integrity = check_tree_integrity(simple_tree_model)
        assert integrity["total_issues"] == 0
        assert result["nodes_updated"] > 0

    def test_rebuild_is_idempotent(self, tree_nodes, simple_tree_model):
        """Running rebuild() twice on a consistent tree should produce 0 updates."""
        simple_tree_model.objects.rebuild()
        result2 = simple_tree_model.objects.rebuild()
        assert result2["nodes_updated"] == 0

    def test_rebuild_emits_tree_rebuilt_signal(self, tree_nodes, simple_tree_model, mocker):
        """tree_rebuilt signal should be emitted after rebuild()."""
        from icv_tree.signals import tree_rebuilt

        mocker.patch(
            "icv_tree.services.integrity.transaction.on_commit",
            side_effect=lambda fn: fn(),
        )

        received = []

        def handler(sender, **kwargs):  # type: ignore[no-untyped-def]
            received.append(kwargs)

        tree_rebuilt.connect(handler)
        try:
            simple_tree_model.objects.rebuild()
        finally:
            tree_rebuilt.disconnect(handler)

        assert len(received) == 1

    def test_tree_rebuilt_signal_carries_correct_kwargs(self, tree_nodes, simple_tree_model, mocker):
        """tree_rebuilt signal should carry nodes_updated and nodes_unchanged."""
        from icv_tree.signals import tree_rebuilt

        mocker.patch(
            "icv_tree.services.integrity.transaction.on_commit",
            side_effect=lambda fn: fn(),
        )

        # Corrupt one node's depth.
        simple_tree_model.objects.filter(name="child1").update(depth=99)

        received = []

        def handler(sender, nodes_updated, nodes_unchanged, **kwargs):  # type: ignore[no-untyped-def]
            received.append({"nodes_updated": nodes_updated, "nodes_unchanged": nodes_unchanged})

        tree_rebuilt.connect(handler)
        try:
            simple_tree_model.objects.rebuild()
        finally:
            tree_rebuilt.disconnect(handler)

        assert len(received) == 1
        assert received[0]["nodes_updated"] >= 1


@pytest.mark.django_db
class TestCheckTreeIntegrity:
    """Test check_tree_integrity() service."""

    def test_check_tree_integrity_detects_orphans(self, db, simple_tree_model, make_node):
        """Orphaned nodes (parent_id pointing to missing row) should be detected."""

        from icv_tree.services import check_tree_integrity

        root = make_node("root")
        child = make_node("child", parent=root)
        child_pk = child.pk

        # Delete the root via raw SQL to bypass Django's CASCADE,
        # leaving child with a dangling parent_id reference.
        with connection.cursor() as cursor:
            table = simple_tree_model._meta.db_table
            pk_col = simple_tree_model._meta.pk.column
            cursor.execute(
                f"DELETE FROM {table} WHERE {pk_col} = %s",  # noqa: S608
                [root.pk],
            )

        result = check_tree_integrity(simple_tree_model)

        # Clean up the orphan so PostgreSQL FK constraint check at teardown passes.
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {table} WHERE {pk_col} = %s",  # noqa: S608
                [child_pk],
            )

        assert child_pk in result["orphaned_nodes"]

    def test_check_tree_integrity_detects_depth_mismatches(self, db, simple_tree_model, make_node):
        """Nodes with depth inconsistent with path should be detected."""
        from icv_tree.services import check_tree_integrity

        root = make_node("root")
        child = make_node("child", parent=root)

        # Corrupt depth.
        simple_tree_model.objects.filter(pk=child.pk).update(depth=99)
        child.refresh_from_db()

        result = check_tree_integrity(simple_tree_model)
        assert child.pk in result["depth_mismatches"]

    def test_check_tree_integrity_detects_path_prefix_violations(self, db, simple_tree_model, make_node):
        """Nodes where parent.path is not a prefix of node.path should be detected."""
        from icv_tree.services import check_tree_integrity

        root = make_node("root")
        child = make_node("child", parent=root)

        # Corrupt path so it no longer starts with parent's path.
        simple_tree_model.objects.filter(pk=child.pk).update(path="9999/9999")

        result = check_tree_integrity(simple_tree_model)
        assert child.pk in result["path_prefix_violations"]

    def test_check_tree_integrity_healthy_tree_has_no_issues(self, tree_nodes, simple_tree_model):
        """A healthy tree should have total_issues == 0."""
        from icv_tree.services import check_tree_integrity

        result = check_tree_integrity(simple_tree_model)
        assert result["total_issues"] == 0


@pytest.mark.django_db
class TestSiblingReorderAfterDeletion:
    """Test that sibling order is repaired correctly after node deletion."""

    def test_sibling_order_repaired_after_deletion(self, tree_nodes, simple_tree_model):
        """Deleting a middle sibling should close the order gap."""
        child1 = tree_nodes["child1"]
        child2 = tree_nodes["child2"]

        # child1 has grandchildren; delete child1 — cascade removes grandchildren too.
        child1.delete()
        child2.refresh_from_db()

        # child2 was order=1; after child1 removed, it should be order=0.
        assert child2.order == 0

    def test_deletion_does_not_trigger_rebuild(self, tree_nodes, simple_tree_model, mocker):
        """Deleting a single node should NOT call rebuild()."""
        from icv_tree import services

        mock_rebuild = mocker.patch.object(services, "rebuild")
        grandchild1 = tree_nodes["grandchild1"]
        grandchild1.delete()
        mock_rebuild.assert_not_called()


@pytest.mark.django_db
class TestMoveToManySiblings:
    """Verify move_to works correctly when there are many siblings.

    This exercises the batch Q-query optimisation introduced to replace the
    per-sibling descendant query pattern.  Creating 20+ siblings ensures the
    batch path covers the multi-sibling code path.
    """

    SIBLING_COUNT = 22  # > 20 as specified

    def test_move_to_first_child_with_many_siblings(self, db, make_node, simple_tree_model):
        """Moving a node to first-child when the parent has many children shifts all correctly."""
        from icv_tree.services import check_tree_integrity

        parent = make_node("parent")
        # Create SIBLING_COUNT children under parent.
        siblings = [make_node(f"child_{i:02d}", parent=parent) for i in range(self.SIBLING_COUNT)]

        # Move an external root node to be the first child of parent.
        new_node = make_node("incoming")
        new_node.move_to(parent, "first-child")
        new_node.refresh_from_db()

        assert new_node.order == 0
        assert new_node.path.startswith(parent.path + "/")

        # All original siblings must have been bumped up by 1.
        for i, sib in enumerate(siblings):
            sib.refresh_from_db()
            assert sib.order == i + 1, f"sibling {i} expected order {i + 1}, got {sib.order}"

        # Tree integrity must be clean.
        result = check_tree_integrity(simple_tree_model)
        assert result["total_issues"] == 0

    def test_move_to_left_with_many_siblings(self, db, make_node, simple_tree_model):
        """Moving a node left of the middle sibling when there are many siblings."""
        from icv_tree.services import check_tree_integrity

        parent = make_node("parent")
        siblings = [make_node(f"child_{i:02d}", parent=parent) for i in range(self.SIBLING_COUNT)]

        # Target the middle sibling.
        mid = self.SIBLING_COUNT // 2
        target = siblings[mid]

        # Move last sibling to the left of the middle one.
        mover = siblings[-1]
        expected_new_order = target.order  # mover will land at target's current order
        mover.move_to(target, "left")
        mover.refresh_from_db()

        assert mover.order == expected_new_order

        # Tree integrity must be clean.
        result = check_tree_integrity(simple_tree_model)
        assert result["total_issues"] == 0

    def test_move_to_right_with_many_siblings_and_descendants(self, db, make_node, simple_tree_model):
        """Moving a node right of the first sibling with each sibling having descendants."""
        from icv_tree.services import check_tree_integrity

        parent = make_node("parent")
        siblings = [make_node(f"child_{i:02d}", parent=parent) for i in range(self.SIBLING_COUNT)]
        # Add one grandchild under each sibling so descendants exist.
        for sib in siblings:
            make_node(f"grandchild_of_{sib.name}", parent=sib)

        # Move the last sibling to the right of the first.
        mover = siblings[-1]
        mover.move_to(siblings[0], "right")
        mover.refresh_from_db()

        assert mover.order == 1  # immediately after siblings[0] which is order=0

        # All grandchildren must still be under their parent.
        for sib in siblings[:-1]:  # exclude mover itself (it moved)
            sib.refresh_from_db()
            children = list(simple_tree_model.objects.filter(parent_id=sib.pk))
            assert len(children) == 1, f"{sib.name} should still have 1 child"
            assert children[0].path.startswith(sib.path + "/")

        # Tree integrity must be clean.
        result = check_tree_integrity(simple_tree_model)
        assert result["total_issues"] == 0
