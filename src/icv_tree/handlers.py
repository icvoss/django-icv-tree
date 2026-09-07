"""
Signal handlers for icv-tree.

handle_pre_save: computes path/depth/order on new node insert; delegates to
    move_to when parent changes on update.
handle_post_delete: repairs sibling order after node deletion.

Both handlers are connected per sender, never bare, so that unrelated
consumer models keep Django's fast-delete path (see
icvoss/django-icv-tree#24). Bare registration attached to every model in a
consuming project, and Django's Collector.can_fast_delete() returns False
for any model carrying a pre_delete/post_delete listener regardless of what
the handler body does, so this silently removed the fast-delete path (a
single DELETE ... WHERE) from every queryset .delete() in a consumer
project, not just TreeNode deletes. The handler bodies are unchanged: the
_skip_signals check and the _is_tree_node_subclass guard stay in place, both
because they are cheap and because they keep the handlers correct if
anything ever connects them senderless again.

Connection happens twice, deliberately:

- ``_connect_tree_handlers()`` walks ``apps.get_models()`` from
  ``IcvTreeConfig.ready()``, connecting every concrete TreeNode subclass
  that exists once the app registry is populated. apps.get_models() never
  returns abstract models, so the abstract TreeNode base itself is skipped
  automatically.
- ``_connect_handlers_for_new_model()`` listens on ``class_prepared`` so a
  model defined AFTER ``ready()`` (a test-local subclass, a dynamically
  built model) still gets wired.

Both paths use ``dispatch_uid`` per sender, so re-running either (a second
``ready()`` under some test runners, a duplicate ``class_prepared`` fire)
never double-connects a handler.

Residual limitation: a model prepared before ``apps.models_ready`` and never
registered in the app registry (i.e. it never reaches a normal ready()
walk or a later class_prepared fire once models are ready) is not wired.
This is not expected to occur for any model Django's own app loading
produces; see _connect_handlers_for_new_model() below for the detail.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager

from django.db.models.signals import class_prepared, post_delete, pre_save

# Thread-local flag used by skip_tree_signals() context manager.
_skip_signals = threading.local()


@contextmanager
def skip_tree_signals() -> Generator[None, None, None]:
    """Context manager to temporarily disable icv-tree pre_save/post_delete handlers.

    Useful for bulk operations (imports, data migrations, management commands)
    where you want to manage tree structure manually and avoid the overhead of
    the automatic path/order computation on every individual save.

    The context manager is nestable — the outer skip remains active until
    the outermost ``with`` block exits.

    Example::

        from icv_tree.handlers import skip_tree_signals

        with skip_tree_signals():
            MyTreeNode.objects.bulk_create(nodes)
            # pre_save / post_delete handlers are disabled here

        # Handlers resume here
    """
    old = getattr(_skip_signals, "skip", False)
    _skip_signals.skip = True
    try:
        yield
    finally:
        _skip_signals.skip = old


def _is_tree_node_subclass(sender) -> bool:  # type: ignore[no-untyped-def]
    """Return True if sender is a concrete subclass of TreeNode."""
    from .models import TreeNode

    return isinstance(sender, type) and issubclass(sender, TreeNode) and not sender._meta.abstract


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
    if getattr(_skip_signals, "skip", False):
        return
    if not _is_tree_node_subclass(sender):
        return

    if instance._state.adding:
        # New node: compute path as last child of parent.
        from .conf import get_setting
        from .services.mutations import _compute_new_path

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)

        # Route sibling counts through the base tree model so that siblings
        # written by a different MTI subtype are counted. Using sender.objects
        # would miss them and produce duplicate order / colliding paths.
        tree_objects = sender._tree_objects()

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
                order = tree_objects.filter(parent_id=parent.pk, **scope_filter).count()
                depth = parent.depth + 1
                parent_path = parent.path
            else:
                order = tree_objects.filter(parent__isnull=True, **scope_filter).count()
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

                # Route through the base tree model so MTI subtype siblings
                # and descendants are seen.
                tree_objects = sender._tree_objects()

                with __import__("django.db", fromlist=["transaction"]).transaction.atomic():
                    old_parent_id = instance.parent_id
                    old_order = instance.order
                    _reorder_siblings_after_removal(sender, old_parent_id, old_order)

                    scope_filter = {}
                    scope_field = getattr(sender, "tree_scope_field", None)
                    if scope_field:
                        scope_filter[f"{scope_field}_id"] = getattr(instance, f"{scope_field}_id")

                    new_order = tree_objects.filter(parent__isnull=True, **scope_filter).count()
                    new_path = _compute_new_path(None, new_order, separator, step_length)

                    # Update descendants.
                    old_path = instance.path
                    descendants = list(
                        tree_objects.filter(
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
                            tree_objects.bulk_update(
                                descendants[i : i + batch_size],
                                ["path", "depth"],
                            )


def handle_post_delete(sender, instance, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """Repair sibling order values after a TreeNode subclass instance is deleted.

    Calls _reorder_siblings_after_removal() per BR-TREE-022.

    Builds a scope_filter when the model defines tree_scope_field, mirroring
    the pre_save handler's scope_filter construction above. Without it, a
    deleted root's siblings-after-removal update (parent_id IS NULL) would
    also decrement the `order` field of every OTHER scope's root siblings,
    since parent_id IS NULL matches every scope's roots regardless of which
    scope the deleted node belonged to (icvoss/django-icv-tree#20).
    """
    if getattr(_skip_signals, "skip", False):
        return
    if not _is_tree_node_subclass(sender):
        return

    from .services.mutations import _reorder_siblings_after_removal

    scope_filter = {}
    scope_field = getattr(sender, "tree_scope_field", None)
    if scope_field:
        scope_filter[f"{scope_field}_id"] = getattr(instance, f"{scope_field}_id")

    _reorder_siblings_after_removal(sender, instance.parent_id, instance.order, scope_filter=scope_filter)


# ---------------------------------------------------------------------------
# Per-sender connection (icvoss/django-icv-tree#24)
# ---------------------------------------------------------------------------

# Handlers keyed by the signal they connect to and the predicate that
# decides whether a given model is one of theirs. Shared by both connection
# paths below so the two never drift apart.
_TREE_RECEIVERS = (
    (pre_save, handle_pre_save, "handle_pre_save"),
    (post_delete, handle_post_delete, "handle_post_delete"),
)


def _connect_for_model(model) -> None:  # type: ignore[no-untyped-def]
    """Connect the pre_save/post_delete handlers for a single concrete model.

    A no-op for any model that is not a concrete TreeNode subclass. Uses a
    dispatch_uid keyed on the handler name and the model, so calling this
    more than once for the same model (a second ready(), a duplicate
    class_prepared fire) never double-connects.
    """
    if not _is_tree_node_subclass(model):
        return

    for signal, handler, name in _TREE_RECEIVERS:
        signal.connect(
            handler,
            sender=model,
            dispatch_uid=f"icv_tree.handlers.{name}.{model._meta.label}",
        )


def _connect_tree_handlers() -> None:
    """Connect the pre_save/post_delete handlers for every model already registered.

    Called from IcvTreeConfig.ready(). Walks apps.get_models(), which is
    safe once the app registry is populated, and connects each handler with
    an explicit sender rather than the bare pre_save/post_delete registration
    this replaces. Bare registration attached to every model in a consuming
    project, which disables Django's fast-delete path for all of them: see
    #24 for the measured impact.

    Skips abstract models implicitly, since apps.get_models() never returns
    them, which is why the abstract TreeNode base itself needs no special
    casing here.

    There is no swappable base in this package (unlike icv_taxonomy's
    get_term_model()): TreeNode is abstract-only, so every concrete
    subclass a consumer defines, including multi-table-inheritance
    children such as tree_testapp's RegularPage/RedirectPage, is connected
    here via _is_tree_node_subclass, not a single resolved default model.

    Models defined after this call (a test-local model, a model built
    dynamically at runtime) are not covered here; see
    _connect_handlers_for_new_model() below for that case.
    """
    from django.apps import apps

    for model in apps.get_models():
        _connect_for_model(model)


def _connect_handlers_for_new_model(sender, **kwargs) -> None:  # type: ignore[no-untyped-def]
    """class_prepared receiver: wire a model defined after ready() has run.

    apps.get_models() in _connect_tree_handlers() only sees models that
    exist at ready() time. A model defined afterwards, such as a test-local
    subclass or one built dynamically, would otherwise never get connected.
    This listens on class_prepared instead.

    Guarded on apps.models_ready: class_prepared also fires for every model
    in the project during Django's own app-loading pass, well before that
    point, and the app registry is not queryable yet. Models prepared
    during app loading are already covered by _connect_tree_handlers() once
    ready() runs, so skipping them here loses nothing. The residual
    limitation is a model prepared before apps.models_ready that is never
    registered in the app registry at all: such a model is not wired by
    either path, though this does not occur for any model produced by
    Django's own app loading.
    """
    from django.apps import apps

    if not apps.models_ready:
        return
    _connect_for_model(sender)


class_prepared.connect(_connect_handlers_for_new_model)
