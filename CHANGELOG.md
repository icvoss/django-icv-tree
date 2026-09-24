# Changelog

All notable changes to django-icv-tree are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed

- **`icv_tree.W001` no longer misreports multi-table inheritance children**
  (#50). The check read `Meta.constraints` and `Meta.unique_together` off
  every concrete `TreeNode` subclass, so an MTI child was warned even when
  its concrete parent carried exactly the constraint asked for, and the hint
  told the consumer to add a constraint the child's own table cannot hold
  (the tree columns live in the parent's table). The constraint sets are now
  evaluated on the model that owns the `path` column,
  `model._meta.get_field("path").model`, with the `tree_scope_field` pairing
  resolved on that same owner. A child whose parent satisfies the check now
  passes with no declaration of its own; a missing constraint is reported
  once, against the owning parent, instead of once per subclass; and when the
  child is reached before its parent the hint names the parent the constraint
  belongs on. A model that owns its own `path` column is checked exactly as
  before. Consumers who set `check_tree_integrity = False` on MTI children as
  a workaround for these warnings can now remove it, which also restores
  `icv_tree.E001`/`E002` coverage for those models: that attribute suppressed
  every integrity check, not just this one. Reported from
  icvoss/icvlocal.com, where five such warnings were tracked as
  icvoss/icv-cms#158 and were about to be answered by adding redundant
  constraints to tables that do not hold the column.

## [1.3.0] - 2026-09-11

### Added

- **New system check `icv_tree.W001`: path uniqueness** (#31). The abstract
  `path` field carries `db_index=True` only, not `unique=True`, so
  uniqueness on `path` (or on `(tree_scope_field, path)` for a scoped model)
  exists only if the concrete model adds its own `UniqueConstraint` or
  `unique_together`. `check_path_uniqueness()` now warns per concrete
  `TreeNode` subclass that declares none, naming the model and the expected
  field set, with a hint showing the `UniqueConstraint` to add. Registered
  automatically on the `models` check tag, since it only inspects `Meta`
  declarations and needs no database access; unlike `check_all_tree_models`
  (`icv_tree.E001`/`E002`), it runs on every `check` and `migrate`. A
  Warning for this release, since some existing consumers (e.g. icv-media's
  `MediaFolder`) do not yet declare the constraint; becomes an Error at the
  next major. Opt out per model with `check_tree_integrity = False`, the
  same attribute `check_all_tree_models` honours.

### Changed

- **README now documents package boundaries** (#30). Added a `## Boundaries`
  section stating what the package deliberately does not do (nested set or
  closure table representations, polymorphic inheritance handling beyond
  MTI routing, multi-tenancy, move history or versioning, drag-and-drop
  JavaScript, REST endpoints, search integration, caching, authorisation),
  matching the scope statement already in the umbrella spec and the shape
  of django-boundary's README.
- **CI now runs a PostgreSQL leg** (#40). `tests/test_rebuild_cte.py`
  exercises `_rebuild_cte()`, the `ICV_TREE_ENABLE_CTE` recursive-CTE fast
  path, but every test in it was skipped on every prior CI run: `ci.yml`
  had no PostgreSQL service, so `connection.vendor` was never
  `"postgresql"` and a green suite said nothing about that path. A new
  `test-postgres` job (Python 3.12, Django 6.1, `postgres:16` service) sets
  `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB`/`POSTGRES_USER`/
  `POSTGRES_PASSWORD` and `ICV_TREE_ENABLE_CTE=1`, runs the full suite
  against PostgreSQL, then runs `tests/test_rebuild_cte.py` a second time on
  its own and fails the job if that run reports no `N passed` summary line
  or any `SKIPPED` test, so the leg cannot pass vacuously if the vendor
  guard stops lifting. `tests/settings.py` now switches the `default`
  database alias to PostgreSQL when `POSTGRES_HOST` is set (the `other`
  alias, used only by the PathIndex router-guard tests, stays on SQLite)
  and reads `ICV_TREE_ENABLE_CTE` from the environment. Both aliases now
  set an explicit empty `TEST["DEPENDENCIES"]`: running
  `tests/test_operations_pathindex.py`'s `databases=["other"]` tests
  alongside a PostgreSQL `default` alias otherwise raises
  `ImproperlyConfigured: Circular dependency in TEST[DEPENDENCIES]`.

### Fixed

- **`ICV_TREE_CHECK_ON_SAVE` now validates same-parent saves** (#27).
  `handle_pre_save()`'s existing-node branch only acted when `parent_id`
  changed, so a caller that hand-edited `path`, `depth` or `order` on an
  already-persisted instance without changing `parent`, then called
  `save()`, had those values written to the database unchanged: no
  exception, no log line. `ICV_TREE_CHECK_ON_SAVE` is now read at call time
  and, when true, raises `TreeStructureError` naming the field and both the
  stored and in-memory values; when false, a mismatch is logged once
  through the new `icv_tree` logger and the save proceeds unchanged. Raw
  saves (`loaddata`) skip the check entirely.
- **`move_to()` now rejects a target from an unrelated tree model** (#28).
  `move_to()` checked for a self-move and a descendant-move cycle but never
  checked that `target` resolved to the same `_tree_model()` base as
  `node`. Passing an unrelated model's instance as `target` previously
  produced no exception and a silently wrong result; it now raises
  `TreeStructureError` naming both models. A move between two
  multi-table-inheritance subtypes of one base still succeeds, since both
  resolve to the same `_tree_model()`.
- **`rebuild()` no longer overwrites already-correct rows with placeholder
  paths** (#42). `_clear_paths_to_placeholders()` set every row in scope to
  a `__rebuild_<pk>__` placeholder before writing the recomputed values
  back, but only the rows in `to_update` were ever rewritten with their
  real path afterwards, so an already-correct row in a partially
  inconsistent tree kept its placeholder path permanently (until a
  subsequent `rebuild()` happened to repair it as a side effect). The
  placeholder pass is now restricted to the primary keys being rewritten:
  an updated row's final path cannot collide with an unchanged row's
  current path, since the full set of final paths computed by `rebuild()`
  is unique, so an unchanged row never needed a placeholder in the first
  place.
- **`rebuild()` now counts and reports orphaned rows on both paths** (#35).
  A row whose `parent_id` references a non-existent or otherwise
  unreachable row (typically a hard delete that bypassed cascade) was
  silently skipped by both the recursive-CTE path and the pure-Python BFS
  path, with no write and no record in the returned dict. `rebuild()` now
  logs one warning through the `icv_tree` logger naming the count and up to
  the first ten primary keys, and returns a new `nodes_orphaned: int` key
  alongside `nodes_updated`/`nodes_unchanged`. Reachable rows continue to
  rebuild correctly; orphaned rows are reported, not repaired or raised on.
- **`get_descendant_count()` now scopes to `tree_scope_field`** (#32).
  `get_descendant_count()` in `models.py` filtered `path__startswith` with
  no `_scope_filter()` call, unlike `get_descendants()`, which does. On a
  scoped model, a colliding path in another scope inflated the count with
  that other scope's rows. It now applies the same `_scope_filter()` as
  `get_descendants()`, so the two methods agree.
- **`move_to()` now scopes its descendant and sibling collections to
  `tree_scope_field`** (#33). The descendant collection and both
  source-side and destination-side sibling collections (including a
  `parent_id=None` root-level move) in `services/mutations.py` had no
  scope constraint at all. On a scoped model, a colliding path could pull
  another scope's descendants into the placeholder-rewrite pass, and a
  shared root `parent_id` value could shift another scope's root sibling
  `order` values. All three collections now filter through the node's own
  `_scope_filter()`, matching the scope discipline the traversal methods
  already apply; unscoped models are unaffected, since `_scope_filter()`
  returns an empty filter for them.
- **`ICV_TREE_REBUILD_BATCH_SIZE` is now validated at startup** (#36).
  `apps.py`'s `_validate_settings()` validated `ICV_TREE_PATH_SEPARATOR`
  and `ICV_TREE_STEP_LENGTH` but never read or validated
  `ICV_TREE_REBUILD_BATCH_SIZE`, even though `rebuild()` and `move_to()`
  both use it as a `range()` step. A value of `0` raised `ValueError`
  partway through a batch loop, mid-transaction, after other writes had
  already run; a negative value produced an empty `range()` silently, so
  computed changes were never persisted. `ICV_TREE_REBUILD_BATCH_SIZE` is
  now rejected at startup with `ImproperlyConfigured` unless it is an
  integer of at least 1, matching the style of the other two settings.
- **Save-driven move to root now sends `node_moved`** (#34).
  `handle_pre_save`'s existing-node branch in `handlers.py` delegated a
  parent change to `move_to()`, which sends `node_moved` after commit, but
  a save-driven move to root (new parent `None`) instead performed the
  move inline and never sent the signal. The inline root-move path now
  sends `node_moved` after commit via the same helper `move_to()` uses,
  with the same payload shape and timing, so a receiver observing
  structural moves no longer misses this case.
- **`PathIndex` migration operation now honours database routers** (#37).
  `database_forwards` and `database_backwards` in `operations.py` used to
  call `schema_editor.execute(sql)` unconditionally, never consulting
  `self.allow_migrate_model()`, unlike Django's own schema operations
  (`AddIndex`, `CreateModel`, and the rest). In a multi-database project
  with a router that refuses an app on a given alias, `migrate` correctly
  skipped `CreateModel` for that alias but `PathIndex` still tried to
  `CREATE INDEX` on the table that was never created, aborting the
  migration run with `relation "..." does not exist` (or the SQLite/MySQL
  equivalent). Both methods now resolve the model from the migration state
  and return early unless
  `self.allow_migrate_model(schema_editor.connection.alias, model)` is
  true, matching the pattern used by `AddIndex`. Related: the vendor check
  used to read the module-level `django.db.connection` (the default
  alias) rather than `schema_editor.connection`, so a non-default alias on
  a different backend received the wrong SQL; it now reads
  `schema_editor.connection.vendor`.
- **`_rebuild_cte()` no longer raises `UndefinedColumn` on PostgreSQL**
  (#43). The recursive CTE's `tree` member selected `tree.computed_path` in
  its recursive term, a column that only existed in the later
  `tree_with_path` CTE, so every call on the only backend the fast path
  targets (`ICV_TREE_ENABLE_CTE = True` on PostgreSQL) failed with
  `psycopg.errors.UndefinedColumn`. PostgreSQL also forbids a window
  function in a recursive CTE's recursive term, so the fix precomputes
  `sib_order` for every row up front in a non-recursive `numbered` CTE
  (partitioned the same way the anchor member always was: by scope and
  `parent_id` for roots, by `parent_id` alone for children), then the
  recursive `tree` CTE walks `numbered` and does only string concatenation
  and depth increment in its recursive term. Scope partitioning, the anchor
  restriction, zero-based `sib_order`, the final column order consumed by
  `pk_to_computed`, and the parametrised scope bind are all unchanged.

## [1.2.0] - 2026-09-07

### Fixed

- **`pre_save`/`post_delete` handlers connected per sender, not bare** (#24).
  `handle_pre_save` and `handle_post_delete` in `handlers.py` used to
  connect with `@receiver(pre_save)` / `@receiver(post_delete)` and no
  `sender`, so they attached to every model in a consuming project, not
  just `TreeNode` subclasses. The handlers guarded correctly and returned
  early for unrelated models, so this was never a correctness defect, but
  it had a measurable cost: Django's `Collector.can_fast_delete()` returns
  `False` for any model carrying a `pre_delete`/`post_delete` listener
  regardless of what the listener does, so every unrelated model in a
  consuming project lost Django's fast-delete path (a single
  `DELETE ... WHERE ...`) and instead fetched every row and dispatched
  signals per row on every queryset `.delete()`. Both handlers are now
  connected with an explicit `sender=model` for every concrete `TreeNode`
  subclass registered in the app registry, walked from
  `IcvTreeConfig.ready()`, plus a `class_prepared` receiver that wires a
  model defined after `ready()` (a test-local subclass, a dynamically
  built model). Handler bodies, `skip_tree_signals()`, and the
  `_is_tree_node_subclass` guard are unchanged.

  **Behaviour change on upgrade:** a `TreeNode` subclass prepared before
  `apps.models_ready` and never registered in the app registry at all is
  not wired by either connection path. This does not occur for any model
  produced by Django's own app loading; it only matters for exotic
  dynamic-model construction outside the normal app-loading sequence.

## [1.1.1] - 2026-08-20

### Fixed

- **Cross-scope data leak in `get_ancestors()` and `get_descendants()`**
  (#20). Both methods filtered on the materialised `path` alone, with no
  `tree_scope_field` predicate. Because path numbering restarts at
  `"0001"` independently in every scope, two scopes' first roots got the
  identical path string, so a `path__in` / `path__startswith` lookup on a
  node in one scope could return another scope's rows. In a multi-tenant
  consumer scoping trees by site or tenant (icv-cms's `Page.full_path`
  walks `get_ancestors()`), this could assemble a public URL from another
  tenant's slugs. Both methods now apply `tree_scope_field` when the model
  declares one; models without `tree_scope_field` are unaffected.
- `get_root()` raised `MultipleObjectsReturned` on any non-root node in a
  scoped model once two scopes' root paths collided, since its lookup was
  also unscoped against a `(scope_field, path)` uniqueness constraint. Now
  scoped the same way as `get_ancestors()`/`get_descendants()`.
- `post_delete`'s sibling-reorder handler decremented the `order` field of
  every scope's root siblings after a root deletion, not just the deleted
  node's own scope, because `parent_id IS NULL` matches every scope's
  roots. `_reorder_siblings_after_removal()` is now called with the same
  `scope_filter` construction the `pre_save` handler already uses.

## [1.1.0] - 2026-08-19

### Added

- **Django 6.1 added to the CI test matrix** and declared via the
  `Framework :: Django :: 6.1` classifier.

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
