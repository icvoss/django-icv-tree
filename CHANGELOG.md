# Changelog

All notable changes to django-icv-tree are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [0.2.0] — 2026-04-08

Promoted to Production/Stable.

### Added

- `skip_tree_signals()` context manager — temporarily disables the
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

## [0.1.5] — 2026-04-02

### Fixed

- Integrity check completely removed from Django's auto-run check framework —
  `Tags.database` checks still fire during `migrate`, so the check is now
  not registered at all. Use `manage.py icv_tree_rebuild --check` instead.
- Integrity check queries reduced from 4 to 3 per model — depth and prefix
  checks merged into a single annotated `values_list` pass (pure ORM, no raw SQL)
- Removed unused `checks` import from `IcvTreeConfig.ready()`

## [0.1.0] — 2026-03-27

### Added

- `TreeNode` abstract model with `parent`, `path`, `depth`, and `order` fields
- `TreeManager` with `roots()`, `at_depth()`, `rebuild()` methods
- `TreeQuerySet` with `ancestors_of()`, `descendants_of()`, `children_of()`,
  `siblings_of()`, `with_tree_fields()` chainable methods
- `move_to()` service — moves a node and its entire subtree atomically with
  `bulk_update()` for descendant path recomputation
- `rebuild()` service — reconstructs all paths from the parent FK adjacency list
  using breadth-first traversal and batch updates
- `check_tree_integrity()` service — detects orphaned nodes, depth mismatches,
  path prefix violations, and duplicate paths without modifying data
- `PathIndex` migration operation — adds `text_pattern_ops` index on PostgreSQL
  for efficient `LIKE 'path/%'` prefix queries
- `node_moved` and `tree_rebuilt` signals with documented kwargs
- Django system checks `icv_tree.E001` (orphaned nodes) and `icv_tree.E002`
  (path inconsistencies)
- `TreeAdmin` mixin — indented display, read-only path/depth/order fields,
  drag-drop ordering hooks
- `icv_tree_rebuild` management command with `--model`, `--dry-run`, `--check`
  arguments
- `recurse_tree` and `tree_breadcrumbs` template tags
- `TreeTestMixin` and factory utilities in `icv_tree.testing`
- Settings: `ICV_TREE_PATH_SEPARATOR`, `ICV_TREE_STEP_LENGTH`,
  `ICV_TREE_MAX_PATH_LENGTH`, `ICV_TREE_ENABLE_CTE`,
  `ICV_TREE_REBUILD_BATCH_SIZE`, `ICV_TREE_CHECK_ON_SAVE`
