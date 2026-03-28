"""
icv-tree integrity services.

Provides rebuild() and check_tree_integrity() for path reconstruction
and consistency verification.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator
from typing import TYPE_CHECKING

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


def _rebuild_cte(model: type) -> dict:
    """PostgreSQL-only rebuild implementation using a recursive CTE.

    Only called when ICV_TREE_ENABLE_CTE = True and the database backend
    is PostgreSQL.

    Args:
        model: A concrete TreeNode subclass.

    Returns:
        Same dict shape as rebuild().
    """
    from ..conf import get_setting
    from ..signals import tree_rebuilt

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)
    batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)

    nodes_updated = 0
    nodes_unchanged = 0

    with transaction.atomic():
        # Load all nodes into memory — CTE path computed in Python after
        # topological sort via the CTE query.
        table = model._meta.db_table
        pk_col = model._meta.pk.column

        # Safety assertion: table name and primary key column come from Django's
        # model registry (_meta.db_table, _meta.pk.column) and are NOT user
        # input. However, we assert here as a defence-in-depth measure to
        # guarantee these values contain only valid SQL identifier characters
        # before they are interpolated into the query string.
        _SQL_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
        assert _SQL_IDENT_RE.match(table), f"Unexpected characters in db_table: {table!r}"
        assert _SQL_IDENT_RE.match(pk_col), f"Unexpected characters in pk.column: {pk_col!r}"

        # Quote identifiers using the database backend's own quoting so the
        # query is valid even if a table or column name is a reserved word.
        quoted_table = connection.ops.quote_name(table)
        quoted_pk = connection.ops.quote_name(pk_col)

        # Use recursive CTE to get topologically sorted nodes with
        # (id, parent_id, sibling_rank) where sibling_rank is the
        # 0-based position among siblings ordered by existing order field.
        # Identifiers are quoted with the backend's own quoting (e.g. double
        # quotes on PostgreSQL) so the query remains valid even if a table or
        # column name happens to be a SQL reserved word.
        # step_length and separator are from Django settings (integers/strings),
        # not user input, so f-string interpolation is safe here.
        raw_sql = f"""
            WITH RECURSIVE tree AS (
                SELECT
                    t.{quoted_pk},
                    t.parent_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY t.parent_id
                        ORDER BY t."order"
                    ) - 1 AS sib_order,
                    NULL::text AS parent_path,
                    0 AS computed_depth
                FROM {quoted_table} t
                WHERE t.parent_id IS NULL
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
        """  # noqa: S608 — internal query, identifiers from Django model registry

        with connection.cursor() as cursor:
            cursor.execute(raw_sql)
            rows = cursor.fetchall()

        # rows: (pk, sib_order, computed_path, computed_depth)
        pk_to_computed: dict = {row[0]: (int(row[1]), row[2], int(row[3])) for row in rows}

        # Load all nodes.
        all_nodes = list(model.objects.all())
        to_update = []
        for node in all_nodes:
            computed = pk_to_computed.get(node.pk)
            if computed is None:
                # Orphan — skip; system check E001 handles these.
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

        for i in range(0, len(to_update), batch_size):
            model.objects.bulk_update(
                to_update[i : i + batch_size],
                ["path", "depth", "order"],
            )

    result = {"nodes_updated": nodes_updated, "nodes_unchanged": nodes_unchanged}

    def _emit():  # type: ignore[no-untyped-def]

        tree_rebuilt.send(
            sender=model,
            nodes_updated=nodes_updated,
            nodes_unchanged=nodes_unchanged,
        )

    transaction.on_commit(_emit)
    return result


def rebuild(model: type) -> dict:
    """Reconstruct path, depth, and order for all nodes from the parent FK.

    Args:
        model: A concrete TreeNode subclass (e.g., Page).

    Returns:
        Dict with keys:
          - nodes_updated: int
          - nodes_unchanged: int

    Side effects:
        - Reads all nodes via BFS traversal
        - bulk_update() in batches
        - Wrapped in transaction.atomic()
        - Emits tree_rebuilt signal after commit
    """
    from ..conf import get_setting

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)
    batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)
    enable_cte = get_setting("ICV_TREE_ENABLE_CTE", False)

    if enable_cte and _is_postgresql():
        return _rebuild_cte(model)

    nodes_updated = 0
    nodes_unchanged = 0
    to_update = []

    # Maps pk -> (computed_path, computed_depth) for parent lookup.
    computed: dict = {}

    with transaction.atomic():
        # Load all nodes grouped by parent for BFS.
        # We use an iterative BFS with a sibling counter per parent.
        parent_to_children: dict = {}
        all_nodes_by_pk: dict = {}

        for node in model.objects.all().order_by("parent_id", "order"):
            all_nodes_by_pk[node.pk] = node
            pid = node.parent_id
            if pid not in parent_to_children:
                parent_to_children[pid] = []
            parent_to_children[pid].append(node)

        # BFS from roots (parent_id=None).
        roots = parent_to_children.get(None, [])
        queue: deque = deque()
        for i, root in enumerate(roots):
            new_path = _compute_new_path(None, i, separator, step_length)
            new_depth = 0
            new_order = i
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

        # Batch update all changed nodes.
        for i in range(0, len(to_update), batch_size):
            model.objects.bulk_update(
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
    """ORM-only integrity check.

    Query 1: select_related('parent') — single LEFT JOIN for orphans + prefix
             violations, streamed in chunks to avoid loading all rows at once.
    Query 2: values_list('pk', 'path', 'depth') — streamed depth check on
             lightweight tuples, no full model instantiation.
    Query 3: GROUP BY path HAVING COUNT > 1 — duplicate detection.
    """
    from django.db.models import Count, Subquery

    # Query 1: orphans — parent_id set but parent row does not exist.
    # Uses a NOT IN subquery: the DB evaluates this as an anti-join, one pass.
    all_pks_subquery = model.objects.values("pk")
    orphaned_nodes = list(
        model.objects.filter(parent_id__isnull=False)
        .exclude(parent_id__in=Subquery(all_pks_subquery))
        .values_list("pk", flat=True)
    )

    # Query 2: prefix violations via select_related (ORM LEFT JOIN).
    # Excludes orphans so the join always resolves.
    path_prefix_violations = []
    orphaned_set = set(orphaned_nodes)

    nodes_qs = (
        model.objects.filter(parent_id__isnull=False)
        .exclude(pk__in=orphaned_set)
        .select_related("parent")
        .only("pk", "path", "parent__path", "parent_id")
    )
    for node in nodes_qs.iterator(chunk_size=2000):
        if not node.path.startswith(node.parent.path + separator):
            path_prefix_violations.append(node.pk)

    # Query 2: depth mismatches — stream lightweight tuples only.
    depth_mismatches = []
    for pk, path, depth in model.objects.values_list("pk", "path", "depth").iterator(chunk_size=5000):
        if depth != path.count(separator):
            depth_mismatches.append(pk)

    # Query 3: duplicates — single GROUP BY.
    duplicate_paths = list(
        model.objects.values("path").annotate(cnt=Count("pk")).filter(cnt__gt=1).values_list("path", flat=True)
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
