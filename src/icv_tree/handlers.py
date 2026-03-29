"""
Signal handlers for icv-tree.

Connected in IcvTreeConfig.ready() via import.

handle_pre_save  — computes path/depth/order on new node insert;
                   delegates to move_to when parent changes on update.
handle_post_delete — repairs sibling order after node deletion.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver


def _is_tree_node_subclass(sender) -> bool:  # type: ignore[no-untyped-def]
    """Return True if sender is a concrete subclass of TreeNode."""
    from .models import TreeNode

    return isinstance(sender, type) and issubclass(sender, TreeNode) and not sender._meta.abstract


@receiver(pre_save)
def handle_pre_save(sender, instance, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Compute path/depth/order before a TreeNode subclass instance is saved.

    Behaviour:
      - If instance._state.adding is True (new node):
          Calls _insert_node() to set path, depth, order.
      - If parent has changed on an existing node:
          Delegates to move_to() service for full subtree recomputation.
      - If parent has not changed:
          Does nothing (path remains correct).
    """
    if not _is_tree_node_subclass(sender):
        return

    if instance._state.adding:
        # New node: compute path as last child of parent.
        from .conf import get_setting
        from .services.mutations import _compute_new_path

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)

        with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
            parent = instance.parent

            # Build scope filter so sibling counts are scoped when
            # tree_scope_field is set (e.g. vocabulary on Term).
            scope_filter = {}
            scope_field = getattr(sender, "tree_scope_field", None)
            if scope_field:
                scope_filter[f"{scope_field}_id"] = getattr(instance, f"{scope_field}_id")

            if parent is not None:
                # Count existing children to determine order.
                order = sender.objects.filter(parent_id=parent.pk, **scope_filter).count()
                depth = parent.depth + 1
                parent_path = parent.path
            else:
                order = sender.objects.filter(parent__isnull=True, **scope_filter).count()
                depth = 0
                parent_path = None

            instance.order = order
            instance.depth = depth
            instance.path = _compute_new_path(parent_path, order, separator, step_length)
    else:
        # Existing node: detect parent change.
        try:
            db_instance = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:
            return

        if db_instance.parent_id != instance.parent_id:
            # Parent has changed — delegate to move_to service.
            # We must reset the in-memory parent to the DB value first,
            # then call move_to with the new parent as target.
            from .services.mutations import move_to

            new_parent = instance.parent
            # Reset instance to its current DB state.
            instance.parent_id = db_instance.parent_id
            instance.path = db_instance.path
            instance.depth = db_instance.depth
            instance.order = db_instance.order

            if new_parent is not None:
                move_to(instance, new_parent, "last-child")
            else:
                # Moving to root — use a root-level move.
                # There is no natural 'right' target for a root move, so we
                # rebuild the node's position as a new root appended at the end.
                from .conf import get_setting
                from .services.mutations import _compute_new_path, _reorder_siblings_after_removal

                separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
                step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)

                with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
                    old_parent_id = instance.parent_id
                    old_order = instance.order
                    _reorder_siblings_after_removal(sender, old_parent_id, old_order)

                    scope_filter = {}
                    scope_field = getattr(sender, "tree_scope_field", None)
                    if scope_field:
                        scope_filter[f"{scope_field}_id"] = getattr(instance, f"{scope_field}_id")

                    new_order = sender.objects.filter(parent__isnull=True, **scope_filter).count()
                    new_path = _compute_new_path(None, new_order, separator, step_length)

                    # Update descendants.
                    old_path = instance.path
                    descendants = list(
                        sender.objects.filter(
                            path__startswith=old_path + separator,
                        ).order_by("path")
                    )
                    instance.parent_id = None
                    instance.path = new_path
                    instance.depth = 0
                    instance.order = new_order

                    if descendants:
                        for desc in descendants:
                            desc.path = new_path + desc.path[len(old_path) :]
                            desc.depth = desc.path.count(separator)
                        batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)
                        for i in range(0, len(descendants), batch_size):
                            sender.objects.bulk_update(
                                descendants[i : i + batch_size],
                                ["path", "depth"],
                            )


@receiver(post_delete)
def handle_post_delete(sender, instance, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Repair sibling order values after a TreeNode subclass instance is deleted.

    Calls _reorder_siblings_after_removal() per BR-TREE-022.
    """
    if not _is_tree_node_subclass(sender):
        return

    from .services.mutations import _reorder_siblings_after_removal

    _reorder_siblings_after_removal(sender, instance.parent_id, instance.order)
