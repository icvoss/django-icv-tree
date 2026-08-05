"""
icv-tree integrity services.

Provides rebuild() and check_tree_integrity() for path reconstruction
and consistency verification.
"""

from __future__ import annotations

import re
import uuid
from collections import deque
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction

if TYPE_CHECKING:
    from ..models import TreeNode


def _compute_new_path(
    parent_path: str | None,
    order: int,
    separator: str,
    step_length: int,
) -> str:
    """Compute a path string given parent path and sibling order.

    Re-implements the same pure function from mutations.py to avoid a circular
    import; integrity.py must not import from mutations.py.
    """
    step = str(order + 1).zfill(step_length)
    if parent_path is None:
        return step
    return parent_path + separator + step


def _bind(value: Any) -> Any:
    """Coerce a value to a DBAPI-safe bind parameter for a raw cursor.execute.

    Re-implements the same helper from mutations.py (see its docstring for
    the full rationale) to avoid a circular import; integrity.py must not
    import from mutations.py. A ``uuid.UUID`` is not an accepted bind type
    on every backend (SQLite's driver accepts only
    str/int/float/bytes/None), so a UUID scope value must be stringified
    before it reaches ``cursor.execute``.
    """
    if isinstance(value, uuid.UUID):
        return value.hex
    return value


def _scope_fk_value(scope: Any, scope_field: str | None) -> Any:
    """Return the raw FK value for a ``scope`` argument passed to rebuild().

    ``scope`` may be a model instance (e.g. a ``Vocabulary``) or the raw FK
    value (its primary key). Raw SQL (the CTE fast path) always needs the
    raw FK value; the ORM-based paths accept either transparently via
    ``qs.filter(**{scope_field: scope})``, so this helper exists solely for
    the raw-SQL bind parameter.
    """
    if scope is None or scope_field is None:
        return None
    pk = getattr(scope, "pk", None)
    return pk if pk is not None else scope


def _traverse_breadth_first(
    model: type,
    batch_size: int = 1000,
) -> Iterator[TreeNode]:
    """Yield all nodes in breadth-first order (roots first, then children, etc.).

    Args:
        model: A concrete TreeNode subclass.
        batch_size: Number of nodes to fetch per DB query.

    Yields:
        TreeNode instances in breadth-first order.

    Side effects:
        Multiple SELECT queries (one per depth level, batched).
    """
    # Start with roots.
    queue: deque = deque(model.objects.filter(parent__isnull=True).order_by("order"))
    while queue:
        node = queue.popleft()
        yield node
        children = list(model.objects.filter(parent_id=node.pk).order_by("order"))
        queue.extend(children)


def _is_postgresql() -> bool:
    """Return True if the current database backend is PostgreSQL."""
    return connection.vendor == "postgresql"


def _validate_scope(model: type, scope: Any) -> str | None:
    """Validate a ``scope`` argument against a model's ``tree_scope_field``.

    Args:
        model: A concrete TreeNode subclass.
        scope: The scope value passed to rebuild(), or None.

    Returns:
        The model's ``tree_scope_field`` name, or None if the model is
        unscoped (``tree_scope_field`` is None).

    Raises:
        ImproperlyConfigured: If scope is not None but the model does not
            define ``tree_scope_field``.
    """
    scope_field = getattr(model, "tree_scope_field", None)
    if scope is not None and scope_field is None:
        raise ImproperlyConfigured(
            f"{model.__name__} does not define tree_scope_field, so rebuild() "
            "cannot be scoped. Pass scope=None, or set tree_scope_field on the "
            "model to enable scoped rebuilds."
        )
    return scope_field


def _rebuild_cte(model: type, scope: Any = None) -> dict:
    """PostgreSQL-only rebuild implementation using a recursive CTE.

    Only called when ICV_TREE_ENABLE_CTE = True and the database backend
    is PostgreSQL.

    When the model defines ``tree_scope_field``, the root-level ROW_NUMBER
    partitions by (scope_field_id, parent_id) so each scope's roots are
    numbered independently starting from 0001.

    Args:
        model: A concrete TreeNode subclass.
        scope: When given, restricts the rebuild to rows whose
            ``tree_scope_field`` column equals this value. Rows in other
            scopes are left completely untouched. Raises
            ImproperlyConfigured if the model has no ``tree_scope_field``.

    Returns:
        Same dict shape as rebuild().
    """
    from ..conf import get_setting
    from ..signals import tree_rebuilt

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)
    batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)

    scope_field = _validate_scope(model, scope)

    nodes_updated = 0
    nodes_unchanged = 0

    with transaction.atomic():
        table = model._meta.db_table
        pk_col = model._meta.pk.column

        _SQL_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
        assert _SQL_IDENT_RE.match(table), f"Unexpected characters in db_table: {table!r}"
        assert _SQL_IDENT_RE.match(pk_col), f"Unexpected characters in pk.column: {pk_col!r}"

        quoted_table = connection.ops.quote_name(table)
        quoted_pk = connection.ops.quote_name(pk_col)

        # When scoped, partition roots by scope FK column so each scope
        # gets independent path numbering starting at 0001.
        scope_col = None
        if scope_field:
            scope_col = f"{scope_field}_id"
            assert _SQL_IDENT_RE.match(scope_col), f"Unexpected characters in scope column: {scope_col!r}"
            quoted_scope = connection.ops.quote_name(scope_col)
            root_partition = f"PARTITION BY t.{quoted_scope}, t.parent_id"
        else:
            root_partition = "PARTITION BY t.parent_id"

        # When a specific scope value is given, restrict the anchor member
        # (roots) to that scope. The recursive member only ever joins
        # children of rows already in the CTE, so it never crosses into
        # another scope's subtree, meaning no extra filter is needed there.
        anchor_where = "t.parent_id IS NULL"
        sql_params: list = []
        if scope is not None and scope_col is not None:
            anchor_where += f" AND t.{quoted_scope} = %s"
            sql_params.append(_bind(_scope_fk_value(scope, scope_field)))

        raw_sql = f"""
            WITH RECURSIVE tree AS (
                SELECT
                    t.{quoted_pk},
                    t.parent_id,
                    ROW_NUMBER() OVER (
                        {root_partition}
                        ORDER BY t."order"
                    ) - 1 AS sib_order,
                    NULL::text AS parent_path,
                    0 AS computed_depth
                FROM {quoted_table} t
                WHERE {anchor_where}
                UNION ALL
                SELECT
                    child.{quoted_pk},
                    child.parent_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY child.parent_id
                        ORDER BY child."order"
                    ) - 1,
                    tree.computed_path,
                    tree.computed_depth + 1
                FROM {quoted_table} child
                JOIN tree ON child.parent_id = tree.{quoted_pk}
            ),
            tree_with_path AS (
                SELECT
                    {quoted_pk},
                    parent_id,
                    sib_order,
                    CASE
                        WHEN parent_path IS NULL
                        THEN LPAD((sib_order + 1)::text, {step_length}, '0')
                        ELSE parent_path || '{separator}' ||
                             LPAD((sib_order + 1)::text, {step_length}, '0')
                    END AS computed_path,
                    computed_depth
                FROM tree
            )
            SELECT {quoted_pk}, sib_order, computed_path, computed_depth
            FROM tree_with_path
        """  # noqa: S608 (internal query, identifiers from Django model registry)

        with connection.cursor() as cursor:
            cursor.execute(raw_sql, sql_params)
            rows = cursor.fetchall()

        pk_to_computed: dict = {row[0]: (int(row[1]), row[2], int(row[3])) for row in rows}

        all_nodes = list(_unfiltered_qs(model, scope_field=scope_field, scope=scope))
        to_update = []
        for node in all_nodes:
            computed = pk_to_computed.get(node.pk)
            if computed is None:
                continue
            new_order, new_path, new_depth = computed
            if node.path != new_path or node.depth != new_depth or node.order != new_order:
                node.path = new_path
                node.depth = new_depth
                node.order = new_order
                to_update.append(node)
                nodes_updated += 1
            else:
                nodes_unchanged += 1

        if to_update:
            # Clear paths to PK-based placeholders first to avoid
            # transient unique constraint violations during bulk_update.
            # Restricted to the target scope so other scopes' rows (and
            # their real paths) are never touched.
            _clear_paths_to_placeholders(model, batch_size, scope_field=scope_field, scope=scope)

            qs = _unfiltered_qs(model, scope_field=scope_field, scope=scope)
            for i in range(0, len(to_update), batch_size):
                qs.bulk_update(
                    to_update[i : i + batch_size],
                    ["path", "depth", "order"],
                )

    result = {"nodes_updated": nodes_updated, "nodes_unchanged": nodes_unchanged}

    def _emit():  # type: ignore[no-untyped-def]
        tree_rebuilt.send(
            sender=model,
            nodes_updated=nodes_updated,
            nodes_unchanged=nodes_unchanged,
            scope=scope,
        )

    transaction.on_commit(_emit)
    return result


def _unfiltered_qs(model: type, scope_field: str | None = None, scope: Any = None):  # type: ignore[no-untyped-def]
    """Return a QuerySet that bypasses any manager-level filters (e.g. soft-delete).

    Checks for ``all_objects`` (icv-taxonomy convention) first, then falls
    back to ``_default_manager``.  As a last resort, constructs a raw
    QuerySet directly from the model, which is guaranteed unfiltered.

    Args:
        model: A concrete TreeNode subclass.
        scope_field: When given (together with ``scope``), the returned
            queryset is additionally filtered to rows whose scope FK
            equals ``scope``. Rows in other scopes are excluded entirely,
            so callers that write through this queryset (bulk_update,
            update()) never touch another scope's rows.
        scope: The scope value to filter to. Ignored if ``scope_field`` is
            None.
    """
    # Prefer all_objects (TreeManager on Term, no is_active filter).
    mgr = getattr(model, "all_objects", None)
    if mgr is not None:
        qs = mgr.all()
    else:
        # Fallback: construct a bare QuerySet (no manager filters).
        from django.db.models import QuerySet

        qs = QuerySet(model)

    if scope_field is not None and scope is not None:
        qs = qs.filter(**{scope_field: scope})

    return qs


def _clear_paths_to_placeholders(
    model: type,
    batch_size: int,
    scope_field: str | None = None,
    scope: Any = None,
) -> None:
    """Set paths to unique PK-based placeholders to avoid transient collisions.

    During rebuild, ``bulk_update`` writes new path values while old paths
    still exist in the table. When a unique constraint covers the path
    column, a new value can collide with an old value on a row that hasn't
    been updated yet.  By first setting every path to a placeholder that
    is guaranteed unique (derived from the PK), the subsequent real update
    can proceed without constraint violations.

    Uses a single UPDATE ... SET path = '__rebuild_' || pk || '__' so the
    operation is fast even for large tables, and does NOT mutate in-memory
    node objects.

    Args:
        model: A concrete TreeNode subclass.
        batch_size: Unused here (kept for signature symmetry with callers);
            the placeholder clear is a single UPDATE regardless of table size.
        scope_field: When given (together with ``scope``), only rows in
            that scope are cleared. Rows in other scopes keep their real
            paths untouched, which is safe because the uniqueness
            constraint on a scoped model covers ``(scope_field, path)``,
            not ``path`` alone. A placeholder in one scope can therefore
            never collide with a real path in a different scope.
        scope: The scope value to restrict clearing to. Ignored if
            ``scope_field`` is None.
    """
    from django.db.models import CharField, Value
    from django.db.models.functions import Cast, Concat

    _unfiltered_qs(model, scope_field=scope_field, scope=scope).update(
        path=Concat(Value("__rebuild_"), Cast("pk", CharField()), Value("__")),
    )


def _get_scope_value(node: TreeNode, scope_field: str | None):  # type: ignore[no-untyped-def]
    """Return the scope FK id for a node, or None if unscoped."""
    if scope_field is None:
        return None
    return getattr(node, f"{scope_field}_id")


def _rebuild_scoped(  # noqa: C901
    model: type,
    roots: list,
    parent_to_children: dict,
    separator: str,
    step_length: int,
    scope_field: str | None,
) -> tuple[list, int, int]:
    """Run BFS rebuild over a set of roots, numbering paths from 0.

    Returns (to_update, nodes_updated, nodes_unchanged).
    """
    nodes_updated = 0
    nodes_unchanged = 0
    to_update: list = []
    computed: dict = {}

    # When scoped, group roots by scope value so each scope starts at order 0.
    if scope_field:
        scope_to_roots: dict = {}
        for root in roots:
            sv = _get_scope_value(root, scope_field)
            scope_to_roots.setdefault(sv, []).append(root)
        ordered_roots: list[tuple] = []
        for _sv, scoped_roots in scope_to_roots.items():
            for i, root in enumerate(scoped_roots):
                ordered_roots.append((root, i))
    else:
        ordered_roots = [(root, i) for i, root in enumerate(roots)]

    queue: deque = deque()
    for root, order_idx in ordered_roots:
        new_path = _compute_new_path(None, order_idx, separator, step_length)
        new_depth = 0
        new_order = order_idx
        computed[root.pk] = (new_path, new_depth)
        if root.path != new_path or root.depth != new_depth or root.order != new_order:
            root.path = new_path
            root.depth = new_depth
            root.order = new_order
            to_update.append(root)
            nodes_updated += 1
        else:
            nodes_unchanged += 1
        queue.append(root)

    while queue:
        parent = queue.popleft()
        parent_path, parent_depth = computed[parent.pk]
        children = parent_to_children.get(parent.pk, [])
        for i, child in enumerate(children):
            new_path = _compute_new_path(parent_path, i, separator, step_length)
            new_depth = parent_depth + 1
            new_order = i
            computed[child.pk] = (new_path, new_depth)
            if child.path != new_path or child.depth != new_depth or child.order != new_order:
                child.path = new_path
                child.depth = new_depth
                child.order = new_order
                to_update.append(child)
                nodes_updated += 1
            else:
                nodes_unchanged += 1
            queue.append(child)

    return to_update, nodes_updated, nodes_unchanged


def rebuild(model: type, scope: Any = None) -> dict:
    """Reconstruct path, depth, and order for nodes from the parent FK.

    When the model defines ``tree_scope_field``, roots are grouped by scope
    value and path numbering restarts at 0001 for each scope. This prevents
    cross-scope path collisions.

    Args:
        model: A concrete TreeNode subclass (e.g., Page).
        scope: When given, restricts the rebuild to rows whose
            ``tree_scope_field`` column equals this value. The node load,
            the placeholder-clearing pass, and the bulk updates are all
            restricted to that scope; rows in every other scope are left
            completely untouched (their path, depth, and order are never
            read for writing, let alone changed). Because children of a
            scoped node always share their parent's scope, restricting the
            initial node load to the target scope is sufficient to keep
            the whole BFS traversal inside that scope. Defaults to None,
            which rebuilds every row exactly as before.

    Returns:
        Dict with keys:
          - nodes_updated: int
          - nodes_unchanged: int

    Raises:
        ImproperlyConfigured: If scope is not None but the model does not
            define ``tree_scope_field``.

    Side effects:
        - Reads nodes (all, or just the target scope) via BFS traversal
        - bulk_update() in batches
        - Wrapped in transaction.atomic()
        - Emits tree_rebuilt signal (carrying scope) after commit
    """
    from ..conf import get_setting

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)
    batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)
    enable_cte = get_setting("ICV_TREE_ENABLE_CTE", False)

    scope_field = _validate_scope(model, scope)

    if enable_cte and _is_postgresql():
        return _rebuild_cte(model, scope=scope)

    with transaction.atomic():
        # Use unfiltered queryset to include inactive/soft-deleted rows.
        # When scope is given, this also excludes every other scope's rows
        # from the load entirely, so the BFS below never sees them.
        qs = _unfiltered_qs(model, scope_field=scope_field, scope=scope)

        # Load nodes grouped by parent for BFS.
        parent_to_children: dict = {}
        all_nodes: list = []
        for node in qs.order_by("parent_id", "order"):
            all_nodes.append(node)
            pid = node.parent_id
            parent_to_children.setdefault(pid, []).append(node)

        roots = parent_to_children.get(None, [])
        to_update, nodes_updated, nodes_unchanged = _rebuild_scoped(
            model,
            roots,
            parent_to_children,
            separator,
            step_length,
            scope_field,
        )

        if to_update:
            # Clear paths to PK-based placeholders first to avoid
            # transient unique constraint violations during bulk_update.
            # Restricted to the target scope so other scopes' rows (and
            # their real paths) are never touched.
            _clear_paths_to_placeholders(model, batch_size, scope_field=scope_field, scope=scope)

            # Now write the final computed paths.
            for i in range(0, len(to_update), batch_size):
                qs.bulk_update(
                    to_update[i : i + batch_size],
                    ["path", "depth", "order"],
                )

    result = {"nodes_updated": nodes_updated, "nodes_unchanged": nodes_unchanged}

    def _emit():  # type: ignore[no-untyped-def]
        from ..signals import tree_rebuilt

        tree_rebuilt.send(
            sender=model,
            nodes_updated=nodes_updated,
            nodes_unchanged=nodes_unchanged,
            scope=scope,
        )

    transaction.on_commit(_emit)
    return result


def check_tree_integrity(model: type) -> dict:
    """Scan a tree model's table for structural inconsistencies without repairing.

    Uses a single SQL query on PostgreSQL (LEFT JOIN + aggregation) or two
    efficient queries on other databases. Avoids loading full rows into Python.

    Args:
        model: A concrete TreeNode subclass.

    Returns:
        Dict with keys:
          - orphaned_nodes: list of PKs where parent_id references a missing row
          - depth_mismatches: list of PKs where depth != path.count(separator)
          - path_prefix_violations: list of PKs where parent.path is not a
                                     proper prefix of node.path
          - duplicate_paths: list of path strings appearing more than once
          - total_issues: int — sum of all issue counts

    Side effects:
        None (read-only queries only)
    """
    from ..conf import get_setting

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")

    return _check_integrity_orm(model, separator)


def _check_integrity_orm(model: type, separator: str) -> dict:
    """ORM-only integrity check using three efficient queries.

    Query 1 (orphans): LEFT OUTER JOIN via ORM — finds rows whose parent_id
        references a missing row. Uses a single anti-join subquery.
    Query 2 (depth + prefix): Single values_list query joining parent via
        LEFT OUTER JOIN. Streams lightweight tuples (no model instantiation)
        and checks both depth mismatches and prefix violations in one pass.
    Query 3 (duplicates): GROUP BY + HAVING COUNT > 1 aggregation.
    """
    from django.db.models import Count, F, Subquery

    qs = _unfiltered_qs(model)

    # --- Query 1: orphans (anti-join) ---
    all_pks_subquery = qs.values("pk")
    orphaned_nodes: list = list(
        qs.filter(parent_id__isnull=False)
        .exclude(parent_id__in=Subquery(all_pks_subquery))
        .values_list("pk", flat=True)
    )
    orphaned_set = set(orphaned_nodes)

    # --- Query 2: depth mismatches + prefix violations in one pass ---
    # Single query with LEFT JOIN to parent via annotate(parent_path=F("parent__path")).
    # Returns lightweight tuples: (pk, path, depth, parent_path).
    depth_mismatches: list = []
    path_prefix_violations: list = []

    combined_qs = qs.annotate(parent_path=F("parent__path")).values_list("pk", "path", "depth", "parent_path")
    for pk, path, depth, parent_path in combined_qs.iterator(chunk_size=5000):
        # Depth check: expected depth == number of separators in path.
        if depth != path.count(separator):
            depth_mismatches.append(pk)

        # Prefix check: only for non-root, non-orphaned nodes.
        if parent_path is not None and pk not in orphaned_set and not path.startswith(parent_path + separator):
            path_prefix_violations.append(pk)

    # --- Query 3: duplicate paths ---
    scope_field = getattr(model, "tree_scope_field", None)
    group_fields = [f"{scope_field}_id", "path"] if scope_field else ["path"]

    duplicate_paths: list = list(
        qs.values(*group_fields).annotate(cnt=Count("pk")).filter(cnt__gt=1).values_list("path", flat=True)
    )

    return {
        "orphaned_nodes": orphaned_nodes,
        "depth_mismatches": depth_mismatches,
        "path_prefix_violations": path_prefix_violations,
        "duplicate_paths": duplicate_paths,
        "total_issues": (
            len(orphaned_nodes) + len(depth_mismatches) + len(path_prefix_violations) + len(duplicate_paths)
        ),
    }
