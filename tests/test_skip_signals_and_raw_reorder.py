"""Tests for skip_tree_signals() context manager and raw-SQL _reorder_siblings_after_removal()."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# skip_tree_signals()
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestSkipTreeSignals:
    """Test that skip_tree_signals() suppresses the pre_save/post_delete handlers."""

    def test_skip_prevents_path_computation_on_new_node(self, simple_tree_model):
        """Saving a new node inside skip_tree_signals() must NOT auto-compute path.

        Without the skip, the handler would overwrite path with a computed value
        like '0001'.  With the skip, the value we set manually is preserved.
        """
        from icv_tree.handlers import skip_tree_signals

        sentinel_path = "MANUAL"
        with skip_tree_signals():
            # The handler would normally overwrite path; with the skip it must not.
            node = simple_tree_model(name="skipped", path=sentinel_path, depth=99, order=99)
            node.save()

        node.refresh_from_db()
        # Path and other tree fields remain at our manually set values.
        assert node.path == sentinel_path
        assert node.depth == 99
        assert node.order == 99

    def test_skip_prevents_post_delete_reorder(self, make_node, simple_tree_model):
        """Deleting inside skip_tree_signals() must NOT reorder siblings."""
        from icv_tree.handlers import skip_tree_signals

        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)

        # c1 is at order=0, c2 at order=1, c3 at order=2.
        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()
        assert c1.order == 0
        assert c2.order == 1
        assert c3.order == 2

        with skip_tree_signals():
            c1.delete()

        # With signals skipped, c2 and c3 must NOT have been reordered.
        c2.refresh_from_db()
        c3.refresh_from_db()
        assert c2.order == 1  # unchanged
        assert c3.order == 2  # unchanged

    def test_handler_resumes_after_context_manager_exits(self, make_node, simple_tree_model):
        """After skip_tree_signals() exits, the handler must run normally again."""
        from icv_tree.handlers import skip_tree_signals

        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)

        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()
        assert c1.order == 0
        assert c2.order == 1
        assert c3.order == 2

        # Skip block — reordering suppressed.
        with skip_tree_signals():
            pass  # nothing deleted here, just verifying exit behaviour

        # After the block, normal deletion must still reorder siblings.
        c1.delete()

        c2.refresh_from_db()
        c3.refresh_from_db()
        assert c2.order == 0  # decremented by handler
        assert c3.order == 1  # decremented by handler

    def test_skip_is_nestable(self, make_node, simple_tree_model):
        """Nested skip_tree_signals() blocks must keep the outer skip active."""
        from icv_tree.handlers import skip_tree_signals

        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)

        c1.refresh_from_db()
        c2.refresh_from_db()

        with skip_tree_signals():
            with skip_tree_signals():
                c1.delete()
            # Inner block exited — outer skip must still be active.
            # Reorder should still NOT have run.
            c2.refresh_from_db()
            assert c2.order == 1  # unchanged while outer skip is active

        # Both blocks exited — reorder should run on the next normal delete.
        c2.refresh_from_db()
        assert c2.order == 1  # c1 was deleted inside skip; order not repaired

    def test_skip_is_thread_local(self, make_node, simple_tree_model):
        """skip_tree_signals() flag must be thread-local (not bleed across threads)."""
        import threading

        from icv_tree.handlers import _skip_signals, skip_tree_signals

        results: dict[str, bool] = {}

        def check_in_thread() -> None:
            # In this thread, skip must be False even while the main thread is in the block.
            results["thread_skip"] = getattr(_skip_signals, "skip", False)

        with skip_tree_signals():
            t = threading.Thread(target=check_in_thread)
            t.start()
            t.join()

        assert results["thread_skip"] is False


# ---------------------------------------------------------------------------
# _reorder_siblings_after_removal() — raw SQL replacement
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReorderSiblingsAfterRemoval:
    """Test the raw-SQL _reorder_siblings_after_removal() directly."""

    def test_siblings_after_removed_position_are_decremented(self, make_node, simple_tree_model):
        """Siblings with order > removed_order must have their order decremented by 1."""
        from icv_tree.services.mutations import _reorder_siblings_after_removal

        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)

        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()
        assert c1.order == 0
        assert c2.order == 1
        assert c3.order == 2

        # Simulate removal of c1 (order=0).
        count = _reorder_siblings_after_removal(simple_tree_model, root.pk, removed_order=0)

        c2.refresh_from_db()
        c3.refresh_from_db()
        assert c2.order == 0  # was 1, decremented
        assert c3.order == 1  # was 2, decremented
        assert count == 2

    def test_siblings_before_removed_position_are_unchanged(self, make_node, simple_tree_model):
        """Siblings with order < removed_order must not be touched."""
        from icv_tree.services.mutations import _reorder_siblings_after_removal

        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)

        c1.refresh_from_db()
        c2.refresh_from_db()
        c3.refresh_from_db()

        # Simulate removal of c3 (order=2) — only nodes after order 2 change.
        count = _reorder_siblings_after_removal(simple_tree_model, root.pk, removed_order=2)

        c1.refresh_from_db()
        c2.refresh_from_db()
        assert c1.order == 0  # unchanged
        assert c2.order == 1  # unchanged
        assert count == 0

    def test_no_siblings_after_returns_zero(self, make_node, simple_tree_model):
        """When no siblings exist after the removed position, return value is 0."""
        from icv_tree.services.mutations import _reorder_siblings_after_removal

        root = make_node("root")
        make_node("only_child", parent=root)

        count = _reorder_siblings_after_removal(simple_tree_model, root.pk, removed_order=0)
        assert count == 0

    def test_root_nodes_handled_with_parent_id_none(self, make_node, simple_tree_model):
        """Passing parent_id=None must correctly target root-level siblings."""
        from icv_tree.services.mutations import _reorder_siblings_after_removal

        r1 = make_node("r1")
        r2 = make_node("r2")
        r3 = make_node("r3")

        r1.refresh_from_db()
        r2.refresh_from_db()
        r3.refresh_from_db()
        assert r1.order == 0
        assert r2.order == 1
        assert r3.order == 2

        # Simulate removal of r1 (order=0) from root level.
        count = _reorder_siblings_after_removal(simple_tree_model, parent_id=None, removed_order=0)

        r2.refresh_from_db()
        r3.refresh_from_db()
        assert r2.order == 0
        assert r3.order == 1
        assert count == 2

    def test_scope_filter_restricts_update_to_matching_scope(self, db):
        """scope_filter must prevent siblings in other scopes from being updated."""
        from tree_testapp.models import Scope, ScopedTree

        from icv_tree.services.mutations import _reorder_siblings_after_removal

        scope_a = Scope.objects.create(name="A")
        scope_b = Scope.objects.create(name="B")

        # Create two roots in scope A and one in scope B — all at root level.
        a1 = ScopedTree.objects.create(name="a1", scope=scope_a)
        a2 = ScopedTree.objects.create(name="a2", scope=scope_a)
        b1 = ScopedTree.objects.create(name="b1", scope=scope_b)

        a1.refresh_from_db()
        a2.refresh_from_db()
        b1.refresh_from_db()
        assert a1.order == 0
        assert a2.order == 1
        assert b1.order == 0  # independent scope, starts at 0

        # Simulate removal of a1 (order=0) within scope A only.
        count = _reorder_siblings_after_removal(
            ScopedTree,
            parent_id=None,
            removed_order=0,
            scope_filter={"scope_id": scope_a.pk},
        )

        a2.refresh_from_db()
        b1.refresh_from_db()
        assert a2.order == 0  # decremented within scope A
        assert b1.order == 0  # unchanged — different scope
        assert count == 1
