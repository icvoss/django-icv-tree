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

Design notes on path uniqueness during an arbitrary permutation
(reorder_siblings): a shift only ever moves a contiguous range by one step,
so a single ascending or descending pass is always collision-safe. An
arbitrary permutation of N slots (for example a 3-cycle) has no such
monotonic-safe ordering in general, so reorder_siblings clears every row it
is about to move to a unique placeholder path first, then writes the final
paths in a second pass. This is the same two-phase trick move_to already
uses for the single node it moves, generalised to every row in the
permutation.
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


def _bind(value):
    """Coerce a value to a DBAPI-safe bind parameter for a raw cursor.execute.

    A ``uuid.UUID`` is not an accepted bind type on every backend (SQLite's
    driver accepts only str/int/float/bytes/None), so a UUID PK or a UUID
    scope value must be stringified before it reaches ``cursor.execute``.
    Postgres stringifies UUID itself, but doing it here makes the raw SQL path
    backend-agnostic.

    Django's UUIDField stores UUIDs in hex format without dashes (e.g.
    'f0d389d358374607932ecbe68e7d1fd4', not 'f0d389d3-5837-4607-932e-cbe68e7d1fd4'),
    so we use ``.hex`` to match the database representation.

    Everything else (int order values, str/int scope values, None) passes
    through unchanged.
    """
    if isinstance(value, uuid.UUID):
        return value.hex
    return value


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
        model: A TreeNode subclass. For multi-table inheritance the tree
            columns (order, parent_id, path) live on the base table, so this
            resolves to the base tree model before touching the table.
        parent_id: The parent's PK (or None for roots).
        removed_order: The order value of the removed node.
        scope_filter: Optional dict of extra WHERE conditions used by scoped
            trees (e.g. ``{"vocabulary_id": 5}``).  Keys must be valid column
            names on the model's table.

    Returns:
        Count of sibling rows updated.
    """
    from django.db import connection

    # The order/parent_id/path columns live on the base tree model's table;
    # for MTI subtypes the concrete table does not hold them.
    model = model._tree_model()
    table = model._meta.db_table

    # Build a parameterised WHERE clause.  Column names are double-quoted
    # (ANSI SQL) to avoid reserved-word clashes; "order" is reserved on most
    # SQL engines.
    #
    # Bind values are coerced with _bind() so a UUID PK works on every backend.
    # A raw ``uuid.UUID`` is not an accepted DBAPI bind type on SQLite
    # (``sqlite3`` accepts only str/int/float/bytes/None), so a UUID-PK tree
    # model raised ``sqlite3.ProgrammingError`` here on any non-root deletion.
    # Postgres's driver stringifies UUID itself, which masked the bug there.
    # Stringifying matches how the ORM serialises a UUID for the backend.
    if parent_id is None:
        parent_clause = '"parent_id" IS NULL'
        params: list = [removed_order]
    else:
        parent_clause = '"parent_id" = %s'
        params = [_bind(parent_id), removed_order]

    scope_clauses: list[str] = []
    if scope_filter:
        for col, val in scope_filter.items():
            scope_clauses.append(f'"{col}" = %s')
            params.append(_bind(val))

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

    # All structural reads/writes route through the base tree model so that
    # multi-table-inheritance subtypes share one path/order namespace.
    tree_model = node._tree_model()
    tree_objects = tree_model._default_manager

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
            sibling_count = tree_objects.filter(parent_id=target.pk).count()
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
            tree_objects.filter(
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
        tree_objects.filter(pk=node.pk).update(path=placeholder_path)

        # Also update descendants to use the placeholder prefix.
        for desc in descendants:
            desc.path = placeholder_path + desc.path[len(old_path) :]
            desc.depth = desc.path.count(separator)
        if descendants:
            for i in range(0, len(descendants), batch_size):
                tree_objects.bulk_update(descendants[i : i + batch_size], ["path", "depth"])

        # Step 1: Close gap at source.
        # Siblings after old_order need to have their order decremented.
        source_siblings_after = list(
            tree_objects.filter(
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
            all_sib_descendants = list(tree_objects.filter(q).order_by("path"))
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
            tree_objects.filter(pk=sib.pk).update(path=new_sib_path, depth=sib.depth, order=sib.order)
            # Update sibling's descendants.
            for desc in sib_desc:
                desc.path = new_sib_path + desc.path[len(old_sib_path) :]
                desc.depth = desc.path.count(separator)
            if sib_desc:
                for i in range(0, len(sib_desc), batch_size):
                    tree_objects.bulk_update(sib_desc[i : i + batch_size], ["path", "depth"])

        # Step 2: Make room at destination.
        # Siblings at >= new_order need order incremented.
        dest_siblings_at_or_after = list(
            tree_objects.filter(
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
            all_dest_sib_descendants = list(tree_objects.filter(q).order_by("-path"))
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
            tree_objects.filter(pk=sib.pk).update(path=new_sib_path, depth=sib.depth, order=sib.order)
            # Update sibling's descendants.
            for desc in sib_desc:
                desc.path = new_sib_path + desc.path[len(old_sib_path) :]
                desc.depth = desc.path.count(separator)
            if sib_desc:
                for i in range(0, len(sib_desc), batch_size):
                    tree_objects.bulk_update(sib_desc[i : i + batch_size], ["path", "depth"])

        # Step 3: Compute new path for the moved node.
        new_depth = (new_parent_depth + 1) if new_parent_id is not None else 0
        new_path = _compute_new_path(new_parent_path, new_order, separator, step_length)

        # Step 4: Update the moved node from placeholder to final path.
        tree_objects.filter(pk=node.pk).update(
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
                tree_objects.bulk_update(descendants[i : i + batch_size], ["path", "depth"])

    # Emit signal after commit.
    if new_parent_id is not None:
        try:
            new_parent_instance = tree_objects.get(pk=new_parent_id)
        except tree_model.DoesNotExist:
            new_parent_instance = None
    else:
        new_parent_instance = None

    def _emit() -> None:
        node_moved.send(
            sender=tree_model,
            instance=node,
            old_parent=old_parent_instance,
            new_parent=new_parent_instance,
            old_path=old_path,
        )

    transaction.on_commit(_emit)


def reorder_siblings(model: type, ordered_ids: list) -> None:
    """Permute a set of sibling rows across the path slots they already occupy.

    Contract: ``ordered_ids`` identifies a set of sibling rows (all sharing
    the same parent, which may be ``None`` for roots). This function does
    NOT insert, remove, or renumber the sibling list; it only permutes the
    rows named in ``ordered_ids`` across the ``(order, path)`` slots those
    same rows already occupy:

      1. The current slots occupied by the listed rows are collected and
         sorted ascending (by order, equivalently by path step).
      2. Those sorted slots are assigned to the rows in the exact sequence
         given by ``ordered_ids``: the first id gets the lowest slot, the
         last id gets the highest slot.

    Every sibling of the same parent that is NOT named in ``ordered_ids``
    (including siblings interleaved between the listed rows by order) is
    left completely untouched: its path, depth, and order are byte-for-byte
    unchanged. Listing a strict subset of a parent's children is supported
    and is the intended use for a scoped consumer that wants to reorder
    only the rows it owns within a shared sibling list (for example, a
    root sibling list that spans several tree_scope_field values).

    Because this operation never adds or removes a slot, it never needs a
    rebuild(): it stays entirely within the slots already occupied by the
    listed rows.

    Args:
        model: A concrete TreeNode subclass.
        ordered_ids: The primary keys of the sibling rows to permute, in
            the desired final order. Must contain at least one id, no
            duplicates, and every id must reference a row that exists and
            shares the same parent as every other listed row.

    Returns:
        None

    Raises:
        TreeStructureError: If ``ordered_ids`` is empty, contains a
            duplicate, references an id that does not exist, or references
            rows that do not all share the same parent.

    Side effects:
        - Rewrites path, depth, and order for every listed row and its
          descendants (siblings not listed, and their descendants, are
          never read for writing)
        - Wrapped in transaction.atomic()
        - bulk_update() in batches per ICV_TREE_REBUILD_BATCH_SIZE
        - No-op if the requested sequence already matches the current order
        - Does not emit a signal: node_moved's payload (a single node, its
          old/new parent, its old path) has no natural shape for a
          multi-row permutation with no parent change, so no signal is
          raised. Connect to your own post-request signal if a reorder
          needs to be observed.
    """
    from ..conf import get_setting

    if not ordered_ids:
        raise TreeStructureError("reorder_siblings() requires at least one id in ordered_ids.")

    if len(set(ordered_ids)) != len(ordered_ids):
        raise TreeStructureError("reorder_siblings() received duplicate ids in ordered_ids.")

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)
    batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)

    # All structural reads/writes route through the base tree model so that
    # multi-table-inheritance subtypes share one path/order namespace, the
    # same routing move_to() uses.
    tree_model = model._tree_model()
    tree_objects = tree_model._default_manager

    rows = list(tree_objects.filter(pk__in=ordered_ids).select_related("parent"))
    # Keyed by str(pk), not the raw pk, so this dict membership check works
    # regardless of whether the caller's ordered_ids are the row's native pk
    # type (e.g. uuid.UUID) or its string form. Every real caller (icv-cms's
    # reorder_pages, this package's own admin move endpoint, #6) passes
    # string ids for a UUID-pk model, since that is the convention used
    # everywhere else in the tree API; row.pk itself is a uuid.UUID
    # instance, and str(uuid.UUID(...)) != uuid.UUID(...) under Python's
    # equality, so comparing raw values here always missed every id despite
    # the pk__in filter above resolving them correctly (Django coerces the
    # lookup value). See #9/#10.
    rows_by_pk = {str(row.pk): row for row in rows}

    missing = [pk for pk in ordered_ids if str(pk) not in rows_by_pk]
    if missing:
        raise TreeStructureError(f"reorder_siblings() could not find row(s) with id(s): {missing!r}.")

    parent_ids = {row.parent_id for row in rows}
    if len(parent_ids) > 1:
        raise TreeStructureError(
            f"reorder_siblings() requires every row to share one parent; got parent_id values: {parent_ids!r}."
        )

    # Ordered list of TreeNode instances matching ordered_ids exactly.
    ordered_rows = [rows_by_pk[str(pk)] for pk in ordered_ids]

    # The rows' shared parent, needed to recompute each slot's path string.
    parent_path = ordered_rows[0].parent.path if ordered_rows[0].parent_id is not None else None
    depth = ordered_rows[0].depth

    # Step 1: collect the slots the listed rows currently occupy, sorted
    # ascending. These are the ONLY slots that will be written; every other
    # sibling's slot is left alone.
    current_slots = sorted(row.order for row in ordered_rows)

    # Step 2: assign slots to rows in the requested sequence and detect the
    # no-op case (already in the requested order).
    assignments: list[tuple] = []  # (row, new_order, new_path)
    changed = False
    for row, new_order in zip(ordered_rows, current_slots, strict=True):
        new_path = _compute_new_path(parent_path, new_order, separator, step_length)
        if row.order != new_order or row.path != new_path:
            changed = True
        assignments.append((row, new_order, new_path))

    if not changed:
        return

    with transaction.atomic():
        # Collect descendants of every listed row up front, keyed by the
        # row's pk (not its path, which is about to change twice).
        descendants_by_pk: dict = {row.pk: [] for row in ordered_rows}
        desc_q = Q()
        for row in ordered_rows:
            desc_q |= Q(path__startswith=row.path + separator)
        if ordered_rows:
            for desc in tree_objects.filter(desc_q).order_by("path"):
                for row in ordered_rows:
                    if desc.path.startswith(row.path + separator):
                        descendants_by_pk[row.pk].append(desc)
                        break

        # Phase 1: vacate every listed row's real path to a unique
        # placeholder, moving its descendants' paths onto the placeholder
        # prefix in the same pass. An arbitrary permutation has no
        # monotonic-safe write order in general (a 3-cycle collides whether
        # written ascending or descending in a single pass), so every row
        # must vacate its real path before any row lands on its final path.
        # The UUID suffix mirrors move_to()'s placeholder: a crash
        # mid-permutation leaves a recognisable, uniquely-suffixed value
        # that rebuild() (scoped or full) can repair.
        placeholder_by_pk: dict = {}
        vacate_updates: list = []
        for row in ordered_rows:
            old_path = row.path
            placeholder = f"__REORDER_{uuid.uuid4().hex[:8]}__" + old_path
            placeholder_by_pk[row.pk] = placeholder
            row.path = placeholder
            vacate_updates.append(row)
            for desc in descendants_by_pk[row.pk]:
                desc.path = placeholder + desc.path[len(old_path) :]
                desc.depth = desc.path.count(separator)
                vacate_updates.append(desc)

        for i in range(0, len(vacate_updates), batch_size):
            tree_objects.bulk_update(vacate_updates[i : i + batch_size], ["path", "depth"])

        # Phase 2: land every listed row on its final order and path,
        # moving its descendants' paths onto the final prefix. Every row is
        # currently on a unique placeholder, so writing the final paths in
        # any order is collision-safe. Rows and their descendants are
        # batched separately because they update different field sets (a
        # descendant's own order never changes), mirroring move_to()'s
        # existing sibling-versus-descendant bulk_update split.
        landed_rows: list = []
        landed_descendants: list = []
        for row, new_order, new_path in assignments:
            placeholder = placeholder_by_pk[row.pk]
            row.order = new_order
            row.path = new_path
            row.depth = depth
            landed_rows.append(row)
            for desc in descendants_by_pk[row.pk]:
                desc.path = new_path + desc.path[len(placeholder) :]
                desc.depth = desc.path.count(separator)
                landed_descendants.append(desc)

        for i in range(0, len(landed_rows), batch_size):
            tree_objects.bulk_update(landed_rows[i : i + batch_size], ["path", "depth", "order"])
        for i in range(0, len(landed_descendants), batch_size):
            tree_objects.bulk_update(landed_descendants[i : i + batch_size], ["path", "depth"])
