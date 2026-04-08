"""
icv-tree mutation services.

All functions that write path, depth, and order fields are defined here.
These are the only code paths that mutate tree structure fields.

Design notes on path uniqueness during moves:
  The path field has a unique constraint. When shifting siblings to make room
  (increment) or close gaps (decrement), we must avoid transient collisions.
  We handle this by:
    1. When INCREMENTING (making room): update in DESCENDING order so the highest
       path step is updated first, avoiding collision with the next sibling.
    2. When DECREMENTING (closing gap): update in ASCENDING order.
    3. For descendants of shifted siblings, update them together with the sibling
       in a single bulk_update pass.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Q

from ..exceptions import TreeStructureError

if TYPE_CHECKING:
    from ..models import TreeNode

_VALID_POSITIONS = frozenset({"first-child", "last-child", "left", "right"})


def _compute_new_path(
    parent_path: str | None,
    order: int,
    separator: str,
    step_length: int,
) -> str:
    """Compute the path string for a node given its parent's path and its order.

    Args:
        parent_path: The parent node's path, or None for root nodes.
        order: The node's 0-based sibling order.
        separator: ICV_TREE_PATH_SEPARATOR value.
        step_length: ICV_TREE_STEP_LENGTH value.

    Returns:
        The computed path string.
        For root (parent_path=None, order=0): "0001"
        For child (parent_path="0001", order=2): "0001/0003"

    Side effects:
        None (pure function)
    """
    step = str(order + 1).zfill(step_length)
    if parent_path is None:
        return step
    return parent_path + separator + step


def _insert_node(
    node: TreeNode,
    parent: TreeNode | None,
    order: int,
) -> None:
    """Compute and assign path, depth, and order for a new node before its first save.

    Args:
        node: The unsaved TreeNode instance.
        parent: The intended parent node, or None for a root node.
        order: The intended 0-based sibling order.

    Returns:
        None (modifies node in-place)

    Side effects:
        Sets node.path, node.depth, node.order.
    """
    from ..conf import get_setting

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)

    parent_path = parent.path if parent is not None else None
    depth = (parent.depth + 1) if parent is not None else 0

    node.order = order
    node.depth = depth
    node.path = _compute_new_path(parent_path, order, separator, step_length)


def _reorder_siblings_after_removal(
    model: type,
    parent_id,
    removed_order: int,
    scope_filter: dict | None = None,
) -> int:
    """Decrement order values for all siblings after the removed position.

    Only updates the order field. Paths are NOT updated here — the path step
    values will be stale after this, but rebuild() can repair them if needed.
    This is intentional: deletion only needs to close the order gap, not
    recompute all paths, which could be expensive for large trees.

    Uses a single raw SQL UPDATE for efficiency, avoiding the Python round-trip
    of loading rows, mutating them, and calling bulk_update.

    Args:
        model: The concrete TreeNode subclass.
        parent_id: The parent's PK (or None for roots).
        removed_order: The order value of the removed node.
        scope_filter: Optional dict of extra WHERE conditions used by scoped
            trees (e.g. ``{"vocabulary_id": 5}``).  Keys must be valid column
            names on the model's table.

    Returns:
        Count of sibling rows updated.
    """
    from django.db import connection

    table = model._meta.db_table

    # Build a parameterised WHERE clause.  Column names are double-quoted
    # (ANSI SQL) to avoid reserved-word clashes; "order" is reserved on most
    # SQL engines.
    if parent_id is None:
        parent_clause = '"parent_id" IS NULL'
        params: list = [removed_order]
    else:
        parent_clause = '"parent_id" = %s'
        params = [parent_id, removed_order]

    scope_clauses: list[str] = []
    if scope_filter:
        for col, val in scope_filter.items():
            scope_clauses.append(f'"{col}" = %s')
            params.append(val)

    extra_where = (" AND " + " AND ".join(scope_clauses)) if scope_clauses else ""

    sql = (
        f'UPDATE "{table}" '  # noqa: S608
        f'SET "order" = "order" - 1 '
        f'WHERE {parent_clause} AND "order" > %s{extra_where}'
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount


def _shift_subtree_up(
    model: type,
    nodes: list,
    old_prefix: str,
    new_prefix: str,
    separator: str,
    batch_size: int,
) -> None:
    """Update paths for a set of nodes and their descendants by replacing a path prefix.

    Processes in ascending order (no unique constraint collision risk when
    moving to higher path steps, because lower steps are free).

    Used when closing a gap (shifting siblings down: order decreases).
    """
    # Collect nodes and all their descendants in a single batch query.
    all_nodes = list(nodes)
    if nodes:
        q = Q()
        for node in nodes:
            q |= Q(path__startswith=node.path + separator)
        all_nodes.extend(model.objects.filter(q).order_by("path"))

    # Deduplicate (nodes list may overlap with descendants if called with mixed input).
    seen: set = set()
    deduped = []
    for n in all_nodes:
        if n.pk not in seen:
            seen.add(n.pk)
            deduped.append(n)

    # Update paths (ascending order — moving to lower path values, safe).
    deduped.sort(key=lambda n: n.path)
    for n in deduped:
        if n.path.startswith(old_prefix):
            n.path = new_prefix + n.path[len(old_prefix) :]
        n.depth = n.path.count(separator)

    for i in range(0, len(deduped), batch_size):
        model.objects.bulk_update(deduped[i : i + batch_size], ["path", "depth", "order"])


def _shift_subtree_down(
    model: type,
    nodes: list,
    old_prefix: str,
    new_prefix: str,
    separator: str,
    batch_size: int,
) -> None:
    """Update paths for a set of nodes and their descendants by replacing a path prefix.

    Processes in descending order (safe when moving to higher path steps,
    to avoid transient unique constraint violations).

    Used when making room (shifting siblings up: order increases).
    """
    # Collect nodes and all their descendants in a single batch query.
    all_nodes = list(nodes)
    if nodes:
        q = Q()
        for node in nodes:
            q |= Q(path__startswith=node.path + separator)
        all_nodes.extend(model.objects.filter(q).order_by("path"))

    seen: set = set()
    deduped = []
    for n in all_nodes:
        if n.pk not in seen:
            seen.add(n.pk)
            deduped.append(n)

    # Sort descending so highest paths are updated first (avoids collisions).
    deduped.sort(key=lambda n: n.path, reverse=True)
    for n in deduped:
        if n.path.startswith(old_prefix):
            n.path = new_prefix + n.path[len(old_prefix) :]
        n.depth = n.path.count(separator)

    # bulk_update processes in the order we give it, so sort by path to
    # let PG handle the updates. However, PG defers unique constraints within
    # a statement, so individual updates within one UPDATE are fine.
    # The issue is bulk_update breaks into multiple UPDATE calls (by PK).
    # We must update one at a time in reverse path order to avoid collisions.
    for n in deduped:
        model.objects.filter(pk=n.pk).update(path=n.path, depth=n.depth, order=n.order)


def move_to(
    node: TreeNode,
    target: TreeNode,
    position: str = "last-child",
) -> None:
    """Move a node (and its entire subtree) to a new position in the tree.

    Args:
        node: The node to move.
        target: The reference node. Interpretation depends on position.
        position: One of 'first-child', 'last-child', 'left', 'right'.

    Returns:
        None

    Raises:
        TreeStructureError: If position is not one of the four valid values.
        TreeStructureError: If target is node itself or a descendant of node.

    Side effects:
        - Recomputes node.parent, node.path, node.depth, node.order
        - Bulk-updates path, depth, order on all descendant nodes
        - Reorders siblings at source and destination
        - Wrapped in transaction.atomic()
        - Emits node_moved signal after transaction commits
        - No-op if move would produce no structural change
    """
    from ..conf import get_setting
    from ..signals import node_moved

    if position not in _VALID_POSITIONS:
        raise TreeStructureError(
            f"Invalid position '{position}'. Must be one of: {', '.join(sorted(_VALID_POSITIONS))}."
        )

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)
    batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)

    # Cycle prevention.
    if target.pk == node.pk:
        raise TreeStructureError("Cannot move a node to itself.")
    if target.path.startswith(node.path + separator):
        raise TreeStructureError(f"Cannot move node '{node.pk}' under its own descendant '{target.pk}'.")

    # Determine new parent and new order.
    if position in ("first-child", "last-child"):
        new_parent_id = target.pk
        new_parent_path = target.path
        new_parent_depth = target.depth
        if position == "first-child":
            new_order = 0
        else:  # last-child
            sibling_count = node.__class__.objects.filter(parent_id=target.pk).count()
            if node.parent_id == target.pk:
                sibling_count -= 1
            new_order = sibling_count
    else:  # left or right
        new_parent_id = target.parent_id
        new_parent_path = target.parent.path if target.parent_id is not None else None
        new_parent_depth = target.parent.depth if target.parent_id is not None else -1
        new_order = target.order if position == "left" else target.order + 1
        if node.parent_id == new_parent_id and node.order < new_order:
            new_order -= 1

    # No-op check.
    if node.parent_id == new_parent_id and node.order == new_order:
        return

    old_parent_id = node.parent_id
    old_path = node.path
    old_order = node.order
    old_parent_instance = node.parent if node.parent_id is not None else None

    with transaction.atomic():
        # Collect the node's descendants (before we change paths).
        descendants = list(
            node.__class__.objects.filter(
                path__startswith=old_path + separator,
            ).order_by("path")
        )

        # Temporarily set the node's path to a placeholder to avoid unique
        # constraint collisions during sibling reordering.
        #
        # Why a UUID suffix? In the event of a crash or unexpected error mid-move,
        # this placeholder value may be left in the database. A unique suffix
        # ensures concurrent moves cannot produce colliding placeholder paths even
        # if two transactions somehow operate on the same old_path simultaneously.
        # Running rebuild() after such a crash will recompute all path values from
        # the parent FK adjacency list and clean up any stale placeholder paths.
        placeholder_path = f"__MOVING_{uuid.uuid4().hex[:8]}__" + old_path
        node.__class__.objects.filter(pk=node.pk).update(path=placeholder_path)

        # Also update descendants to use the placeholder prefix.
        for desc in descendants:
            desc.path = placeholder_path + desc.path[len(old_path) :]
            desc.depth = desc.path.count(separator)
        if descendants:
            for i in range(0, len(descendants), batch_size):
                node.__class__.objects.bulk_update(descendants[i : i + batch_size], ["path", "depth"])

        # Step 1: Close gap at source.
        # Siblings after old_order need to have their order decremented.
        source_siblings_after = list(
            node.__class__.objects.filter(
                parent_id=old_parent_id,
                order__gt=old_order,
            ).order_by("order")  # ascending: lower paths updated first
        )
        for sib in source_siblings_after:
            sib.order -= 1
        # Update paths of source siblings and their subtrees (ascending order).
        # Compute old/new paths for each sibling before touching the DB.
        source_parent_path = node.parent.path if old_parent_id is not None else None
        sib_path_map: list[tuple] = []  # (sib, old_sib_path, new_sib_path)
        for sib in source_siblings_after:
            old_sib_path = _compute_new_path(source_parent_path, sib.order + 1, separator, step_length)
            new_sib_path = _compute_new_path(source_parent_path, sib.order, separator, step_length)
            sib_path_map.append((sib, old_sib_path, new_sib_path))

        # Batch-fetch all descendants of all source siblings in one query.
        if sib_path_map:
            q = Q()
            for _sib, old_sib_path, _new in sib_path_map:
                q |= Q(path__startswith=old_sib_path + separator)
            all_sib_descendants = list(node.__class__.objects.filter(q).order_by("path"))
        else:
            all_sib_descendants = []

        # Group descendants by which sibling they belong to.
        sib_desc_map: dict = {old_sib_path: [] for _sib, old_sib_path, _new in sib_path_map}
        for desc in all_sib_descendants:
            for _sib, old_sib_path, _new in sib_path_map:
                if desc.path.startswith(old_sib_path + separator):
                    sib_desc_map[old_sib_path].append(desc)
                    break

        for sib, old_sib_path, new_sib_path in sib_path_map:
            sib_desc = sib_desc_map[old_sib_path]
            # Update sibling itself.
            sib.path = new_sib_path
            node.__class__.objects.filter(pk=sib.pk).update(path=new_sib_path, depth=sib.depth, order=sib.order)
            # Update sibling's descendants.
            for desc in sib_desc:
                desc.path = new_sib_path + desc.path[len(old_sib_path) :]
                desc.depth = desc.path.count(separator)
            if sib_desc:
                for i in range(0, len(sib_desc), batch_size):
                    node.__class__.objects.bulk_update(sib_desc[i : i + batch_size], ["path", "depth"])

        # Step 2: Make room at destination.
        # Siblings at >= new_order need order incremented.
        dest_siblings_at_or_after = list(
            node.__class__.objects.filter(
                parent_id=new_parent_id,
                order__gte=new_order,
            ).order_by("-order")  # DESCENDING: update highest path first to avoid collision
        )
        for sib in dest_siblings_at_or_after:
            sib.order += 1
        # Update paths of destination siblings and their subtrees.
        # Compute old/new paths for each sibling before touching the DB.
        dest_sib_path_map: list[tuple] = []  # (sib, old_sib_path, new_sib_path)
        for sib in dest_siblings_at_or_after:
            old_sib_path = _compute_new_path(new_parent_path, sib.order - 1, separator, step_length)
            new_sib_path = _compute_new_path(new_parent_path, sib.order, separator, step_length)
            dest_sib_path_map.append((sib, old_sib_path, new_sib_path))

        # Batch-fetch all descendants of all destination siblings in one query.
        if dest_sib_path_map:
            q = Q()
            for _sib, old_sib_path, _new in dest_sib_path_map:
                q |= Q(path__startswith=old_sib_path + separator)
            all_dest_sib_descendants = list(node.__class__.objects.filter(q).order_by("-path"))
        else:
            all_dest_sib_descendants = []

        # Group descendants by which sibling they belong to.
        dest_sib_desc_map: dict = {old_sib_path: [] for _sib, old_sib_path, _new in dest_sib_path_map}
        for desc in all_dest_sib_descendants:
            for _sib, old_sib_path, _new in dest_sib_path_map:
                if desc.path.startswith(old_sib_path + separator):
                    dest_sib_desc_map[old_sib_path].append(desc)
                    break

        for sib, old_sib_path, new_sib_path in dest_sib_path_map:
            sib_desc = dest_sib_desc_map[old_sib_path]
            # Update sibling itself (highest order first = descending).
            sib.path = new_sib_path
            node.__class__.objects.filter(pk=sib.pk).update(path=new_sib_path, depth=sib.depth, order=sib.order)
            # Update sibling's descendants.
            for desc in sib_desc:
                desc.path = new_sib_path + desc.path[len(old_sib_path) :]
                desc.depth = desc.path.count(separator)
            if sib_desc:
                for i in range(0, len(sib_desc), batch_size):
                    node.__class__.objects.bulk_update(sib_desc[i : i + batch_size], ["path", "depth"])

        # Step 3: Compute new path for the moved node.
        new_depth = (new_parent_depth + 1) if new_parent_id is not None else 0
        new_path = _compute_new_path(new_parent_path, new_order, separator, step_length)

        # Step 4: Update the moved node from placeholder to final path.
        node.__class__.objects.filter(pk=node.pk).update(
            parent_id=new_parent_id,
            path=new_path,
            depth=new_depth,
            order=new_order,
        )
        node.parent_id = new_parent_id
        node.path = new_path
        node.depth = new_depth
        node.order = new_order

        # Step 5: Update descendants from placeholder prefix to new path prefix.
        if descendants:
            for desc in descendants:
                # Replace the placeholder prefix with the new real path.
                old_placeholder = placeholder_path
                desc.path = new_path + desc.path[len(old_placeholder) :]
                desc.depth = desc.path.count(separator)
            for i in range(0, len(descendants), batch_size):
                node.__class__.objects.bulk_update(descendants[i : i + batch_size], ["path", "depth"])

    # Emit signal after commit.
    if new_parent_id is not None:
        try:
            new_parent_instance = node.__class__.objects.get(pk=new_parent_id)
        except node.__class__.DoesNotExist:
            new_parent_instance = None
    else:
        new_parent_instance = None

    def _emit() -> None:
        node_moved.send(
            sender=node.__class__,
            instance=node,
            old_parent=old_parent_instance,
            new_parent=new_parent_instance,
            old_path=old_path,
        )

    transaction.on_commit(_emit)
