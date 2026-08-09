# Changelog

All notable changes to django-icv-tree are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-08-09

### Fixed

- `reorder_siblings()` raised `TreeStructureError` ("could not find
  row(s)") on every call against a UUID-pk model when `ordered_ids` was a
  list of strings, the convention used by every real caller (icv-cms's
  `reorder_pages`, and this package's own `TreeAdmin.tree_move_node`).
  `rows_by_pk` was keyed by the raw `row.pk` (a `uuid.UUID` instance), and
  the membership check compared that against the caller's un-normalised
  `ordered_ids`; `str(uuid.UUID(...)) != uuid.UUID(...)` under Python
  equality, so every id was reported missing even though the preceding
  `pk__in` filter had already resolved every row correctly. Both sides of
  the comparison are now normalised via `str()` (#9, #10).
- `TreeAdmin.tree_move_node` was registered at `<int:pk>/tree-move/`, so a
  UUID-formatted pk in the URL 404s before the view is ever reached. Now
  registered at `<path:pk>/tree-move/`, matching Django's own
  `ModelAdmin.get_urls()` convention for change/delete/history, which
  resolves any pk type a Django model lookup supports (#6).

### Changed

- `TreeNode.path`'s `help_text` no longer contains an em dash.
  **Breaking for `makemigrations --check`**: because `TreeNode` is
  abstract, every consumer subclass freezes this exact string into its
  own migration via `Field.deconstruct()`. Consumers regenerating or
  hand-adjusting migrations for a `TreeNode` subclass after upgrading to
  this version will see a one-time `help_text` alteration (cosmetic, no
  column change) until their frozen migration is updated to match the new
  string (#5).

## [0.4.0] - 2026-08-05

### Added

- **Scoped rebuilds** (issue #7). `rebuild()` (and `TreeManager.rebuild()`,
  and the `icv_tree_rebuild` management command) now accept an optional
  `scope` argument. When given, the rebuild reads, clears, and writes only
  the rows in that `tree_scope_field` value; every other scope's rows,
  including their path, depth, and order, are left completely untouched.
  This lets a scoped consumer (for example a vocabulary of terms) rebuild
  the whole tree it owns without touching any other vocabulary's tree.

  A scoped rebuild is safe from transient path collisions because a scoped
  model's uniqueness constraint covers `(scope_field, path)`, not `path`
  alone: the placeholder-clearing pass used during rebuild only ever
  clears rows within the target scope, so it can never collide with a
  real path belonging to a different, untouched scope.

  Passing `scope` to a model that does not define `tree_scope_field` raises
  `django.core.exceptions.ImproperlyConfigured`. `scope=None` (the
  default) keeps the existing full-rebuild behaviour unchanged.

  The PostgreSQL recursive-CTE fast path (`ICV_TREE_ENABLE_CTE = True`)
  also supports `scope`: the scope filter is threaded straight into the
  CTE's anchor (roots) query, so the fast path stays fast under scoping.

  The `tree_rebuilt` signal now carries a `scope` keyword argument (the
  value passed to `rebuild()`, or `None` for a full rebuild).

  New `--scope` option on `icv_tree_rebuild`, for example:
  `python manage.py icv_tree_rebuild --model=myapp.Term --scope=5`.

- **`reorder_siblings(model, ordered_ids)`.** A new public mutation
  primitive alongside `move_to`, for reordering a set of sibling rows
  without a rebuild and without touching any sibling that is not listed.

  `ordered_ids` names a set of rows that all share one parent (which may
  be `None` for roots) and gives their desired final sequence. The rows
  are permuted across the `(order, path)` slots they already occupy: the
  current slots are collected and sorted ascending, then handed out to
  the rows in the requested sequence. Any sibling of the same parent that
  is not named in `ordered_ids`, including siblings interleaved between
  the listed rows by order, is left completely untouched: its path,
  depth, and order are byte-for-byte unchanged. Listing a strict subset
  of a parent's children is fully supported, which is what lets a scoped
  consumer reorder only the rows it owns within a shared sibling list
  (for example a root sibling list spanning several `tree_scope_field`
  values) without disturbing any other scope's roots.

  This closes a gap the scoped `rebuild()` above could not: some
  concrete-polymorphic multi-table-inheritance consumers keep their scope
  column on a subclass table, so a scoped rebuild cannot serve their
  sibling-reorder path at all. `reorder_siblings` avoids rebuilding
  altogether, so it has no dependency on where the scope column lives.

  Collision safety follows the same two-phase placeholder pattern
  `move_to` already uses for the single node it moves, generalised to
  every row in the permutation: an arbitrary permutation (unlike a
  simple insert or remove, which only ever shifts a contiguous range by
  one step) has no write order that is safe for every case, a 3-cycle
  collides whether written ascending or descending in a single pass, and
  neither does the simplest case of two siblings exchanging slots. Every
  listed row (and its descendants) is first moved to a unique placeholder
  path, then every row is written to its final path, so the unique path
  constraint is never at risk mid-permutation.

  Raises `TreeStructureError`, the same exception `move_to` raises for
  its cycle guard, if `ordered_ids` is empty, contains a duplicate,
  names an id that does not exist, or names rows that do not all share
  one parent. Is a no-op if the requested sequence already matches the
  current order. Does not emit a signal: `node_moved`'s payload (a
  single node with an old and new parent) has no natural shape for a
  multi-row permutation with no parent change.

## [0.3.1] - 2026-07-28

### Fixed

- **UUID-PK trees no longer crash on non-root deletion under SQLite**
  (issue #2). `_reorder_siblings_after_removal` passed the parent's PK
  straight to a raw `cursor.execute`; when the tree model used a
  `UUIDField` primary key, `parent_id` was a `uuid.UUID`, which SQLite's
  DBAPI driver rejects (`sqlite3.ProgrammingError: type 'UUID' is not
  supported`). Postgres stringifies UUID in its driver, which masked the
  bug there. Bind values are now coerced with a small `_bind()` helper
  (`str(uuid)`, everything else unchanged), so the raw reorder is
  backend-agnostic. The same coercion is applied to `scope_filter` values,
  which had the identical latent issue for a UUID scope key. Regression
  tests cover a UUID-PK tree model deleting a non-root node.

## [0.3.0] - 2026-07-09

### Changed

- Minimum Django is now 5.2 (was 5.0). Django 5.2 and 6.0 are the
  supported and CI-tested versions.
- Packaging: the build backend now requires setuptools 77+ (PEP 639
  SPDX licence metadata) and no longer lists wheel; project URLs point
  at the icvoss GitHub organisation.

## [0.2.1] - 2026-06-24

### Fixed

- **Tree traversal across multi-table-inheritance subtypes.** When a `TreeNode`
  subclass is the base of an MTI chain (e.g. a `Page` base with `RegularPage` /
  `RedirectPage` children), traversal methods queried the concrete subclass's
  manager and missed ancestors or descendants stored as a sibling subtype.
  `get_ancestors`, `get_descendants`, `get_children`, `get_siblings`,
  `get_root`, `is_leaf`, and `get_descendant_count` now scope to the tree's
  base model via the new `TreeNode._tree_model()` / `_tree_objects()` helpers.
  Non-inherited models are unaffected (the helpers resolve to the model itself).

- **Insert/move path computation across multi-table-inheritance subtypes.**
  The write path counted siblings via the concrete subtype's manager
  (`sender.objects` / `node.__class__.objects`), so a sibling written by a
  different MTI subtype was not counted, producing duplicate `order` values
  and colliding `path` strings. The `pre_save` handler, the root-move branch,
  `_reorder_siblings_after_removal`, and `move_to` now route sibling counts and
  structural updates through the base tree model. Non-inherited and scoped
  (non-MTI) trees are unaffected.

## [0.2.0] - 2026-04-08

Promoted to Production/Stable.

### Added

- `skip_tree_signals()` context manager: temporarily disables the
  `handle_pre_save` handler during bulk operations, eliminating 2 DB
  queries per save when batch-creating nodes
- 23 new tests covering admin, management commands, and template tags
- 10 new tests for skip_tree_signals and raw SQL sibling reorder

### Changed

- `_shift_subtree_up()` / `_shift_subtree_down()` now load all affected
  descendants in a single batch query using `Q` objects instead of N+1
  per-sibling queries. `move_to()` with 50 siblings: ~216 → ~10 queries.
- `_reorder_siblings_after_removal()` replaced with a single raw SQL
  `UPDATE SET "order" = "order" - 1` instead of loading into Python and
  calling `bulk_update()`.

## [0.1.5] - 2026-04-02

### Fixed

- Integrity check completely removed from Django's auto-run check framework:
  `Tags.database` checks still fire during `migrate`, so the check is now
  not registered at all. Use `manage.py icv_tree_rebuild --check` instead.
- Integrity check queries reduced from 4 to 3 per model: depth and prefix
  checks merged into a single annotated `values_list` pass (pure ORM, no raw SQL)
- Removed unused `checks` import from `IcvTreeConfig.ready()`

## [0.1.0] - 2026-03-27

### Added

- `TreeNode` abstract model with `parent`, `path`, `depth`, and `order` fields
- `TreeManager` with `roots()`, `at_depth()`, `rebuild()` methods
- `TreeQuerySet` with `ancestors_of()`, `descendants_of()`, `children_of()`,
  `siblings_of()`, `with_tree_fields()` chainable methods
- `move_to()` service: moves a node and its entire subtree atomically with
  `bulk_update()` for descendant path recomputation
- `rebuild()` service: reconstructs all paths from the parent FK adjacency list
  using breadth-first traversal and batch updates
- `check_tree_integrity()` service: detects orphaned nodes, depth mismatches,
  path prefix violations, and duplicate paths without modifying data
- `PathIndex` migration operation: adds `text_pattern_ops` index on PostgreSQL
  for efficient `LIKE 'path/%'` prefix queries
- `node_moved` and `tree_rebuilt` signals with documented kwargs
- Django system checks `icv_tree.E001` (orphaned nodes) and `icv_tree.E002`
  (path inconsistencies)
- `TreeAdmin` mixin: indented display, read-only path/depth/order fields,
  drag-drop ordering hooks
- `icv_tree_rebuild` management command with `--model`, `--dry-run`, `--check`
  arguments
- `recurse_tree` and `tree_breadcrumbs` template tags
- `TreeTestMixin` and factory utilities in `icv_tree.testing`
- Settings: `ICV_TREE_PATH_SEPARATOR`, `ICV_TREE_STEP_LENGTH`,
  `ICV_TREE_MAX_PATH_LENGTH`, `ICV_TREE_ENABLE_CTE`,
  `ICV_TREE_REBUILD_BATCH_SIZE`, `ICV_TREE_CHECK_ON_SAVE`
