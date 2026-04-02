# Changelog

All notable changes to django-icv-tree are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [0.1.4] — 2026-04-02

### Fixed

- System check no longer blocks `runserver` / `migrate` startup — moved from
  `Tags.models` to `Tags.database` so it only runs with
  `manage.py check --database default`
- Integrity check queries reduced from 4 to 3 per model — depth and prefix
  checks merged into a single annotated `values_list` pass (pure ORM, no raw SQL)
- System check now respects Django's `--database` flag

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
