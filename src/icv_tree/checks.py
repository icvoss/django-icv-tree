"""
Django system checks for icv-tree.

Two kinds of check live here, with different registration and cost profiles.

``check_all_tree_models`` (E001/E002) is NOT auto-registered with the check
framework: the integrity queries are too expensive to run on every
``runserver``, ``migrate``, or even ``check --database``. Instead, invoke
explicitly via::

    manage.py icv_tree_rebuild --check

or call ``check_all_tree_models()`` directly in your own check / CI step.

``check_path_uniqueness`` (W001) IS auto-registered (see ``apps.py``): it only
inspects ``Meta.constraints``/``Meta.unique_together`` declarations, so it is
cheap enough to run on every ``check`` and ``migrate``.

E001: Warning, orphaned nodes (parent_id references missing rows)
E002: Error, path inconsistencies (depth mismatch, prefix violation, duplicate paths)
W000: Warning, integrity check itself failed to run (e.g. table missing)
W001: Warning, the model owning the path column declares no uniqueness constraint on it
"""

from __future__ import annotations

from django.apps import apps
from django.core.checks import Error, Warning


def _installed_tree_models() -> list:  # type: ignore[type-arg]
    """Return concrete, installed TreeNode subclasses that have not opted out.

    Shared discovery loop for ``check_all_tree_models`` and
    ``check_path_uniqueness``. Consuming models may opt out of either check by
    setting ``check_tree_integrity = False`` on the model class (BR-TREE-043).
    """
    from .models import TreeNode

    return [
        model
        for model in apps.get_models()
        if issubclass(model, TreeNode) and not model._meta.abstract and getattr(model, "check_tree_integrity", True)
    ]


def check_all_tree_models(app_configs=None, databases=None, **kwargs):  # type: ignore[no-untyped-def]
    """Check all concrete TreeNode subclasses for tree integrity issues.

    Generates:
      icv_tree.E001 Warning, orphaned nodes
      icv_tree.E002 Error, path inconsistencies

    Consuming models may opt out by setting ``check_tree_integrity = False``
    on the model class (BR-TREE-043).

    Not auto-registered.  Call directly or from a management command.
    """
    from .services.integrity import check_tree_integrity

    errors: list = []

    tree_models = _installed_tree_models()

    for model in tree_models:
        # When called with databases (e.g. from the check framework),
        # skip models whose DB alias isn't in the set.
        if databases is not None:
            db_alias = model.objects.db
            if db_alias not in databases:
                continue

        try:
            result = check_tree_integrity(model)
        except Exception as exc:  # noqa: BLE001
            # If the table does not exist yet (e.g. pre-migration), skip.
            errors.append(
                Warning(
                    f"icv_tree could not check integrity of {model.__name__}: {exc}",
                    id="icv_tree.W000",
                )
            )
            continue

        model_name = model.__name__

        if result["orphaned_nodes"]:
            count = len(result["orphaned_nodes"])
            errors.append(
                Warning(
                    f"{model_name} has {count} orphaned node(s) "
                    f"(parent_id references missing rows). "
                    f"Run icv_tree_rebuild --check to identify them.",
                    id="icv_tree.E001",
                    obj=model,
                )
            )

        path_issues = (
            len(result["depth_mismatches"]) + len(result["path_prefix_violations"]) + len(result["duplicate_paths"])
        )
        if path_issues:
            errors.append(
                Error(
                    f"{model_name} has {path_issues} path inconsistency/inconsistencies. "
                    f"Run python manage.py icv_tree_rebuild --model={model._meta.app_label}.{model_name} "
                    f"to repair.",
                    id="icv_tree.E002",
                    obj=model,
                )
            )

    return errors


def _constrained_field_sets(model) -> list[frozenset[str]]:  # type: ignore[no-untyped-def]
    """Return every field set covered by a UniqueConstraint or unique_together on model.

    Each entry is a frozenset of field names. Only plain field names are
    considered (a UniqueConstraint's ``fields``, not its ``expressions``, and
    not a partial constraint's ``condition``): the field-set comparison in
    ``check_path_uniqueness`` needs a name-for-name match, so an
    expression-only or conditional constraint cannot satisfy it and is
    excluded rather than mis-read.
    """
    field_sets: list[frozenset[str]] = []

    for constraint in model._meta.constraints:
        fields = getattr(constraint, "fields", None)
        if fields:
            field_sets.append(frozenset(fields))

    for group in model._meta.unique_together:
        field_sets.append(frozenset(group))

    return field_sets


def _path_owner(model):  # type: ignore[no-untyped-def]
    """Return the concrete model whose own table holds ``model``'s path column.

    For a model that is not a multi-table-inheritance child, and for a child
    that redefines ``path`` on its own table, this is ``model`` itself. For an
    MTI child that inherits ``path`` from a concrete parent, this is that
    parent: the column lives in the parent's table, so the parent's table is
    the only one whose uniqueness constraint can guard it (BR-TREE-001, which
    scopes path uniqueness to "its concrete model's table").

    ``Field.model`` is the authority here rather than ``TreeNode._tree_model()``:
    the latter returns the topmost concrete TreeNode ancestor, which is the
    right answer for tree-walking queries but the wrong one for a child that
    shadows ``path`` with a column of its own (icvoss/django-icv-tree#50).
    """
    return model._meta.get_field("path").model


def check_path_uniqueness(app_configs=None, **kwargs):  # type: ignore[no-untyped-def]
    """Warn when the model owning a TreeNode's path column declares no uniqueness constraint.

    The abstract ``path`` field carries ``db_index=True`` only, not
    ``unique=True`` (a plain unique on path in the abstract Meta is wrong for
    scoped models, and would force a migration on every consumer). Uniqueness
    is therefore a constraint the concrete model must declare itself: a
    ``UniqueConstraint``/``unique_together`` whose field set is exactly
    ``{"path"}`` for an unscoped model, or exactly
    ``{tree_scope_field, "path"}`` for a model with ``tree_scope_field`` set.
    Without it, two concurrent inserts under the same parent that compute the
    same order (and therefore the same path) are both written successfully,
    silently (icvoss/django-icv-tree#31).

    The constraint is evaluated on the model that OWNS the ``path`` column, not
    on every concrete subclass (icvoss/django-icv-tree#50). A multi-table
    inheritance child inherits ``path`` from its concrete parent's table and
    cannot declare a constraint on a column its own table does not hold, so a
    child whose parent carries the constraint passes with no declaration of its
    own, and the missing-constraint warning is reported once against the owning
    parent rather than once per child. The ``tree_scope_field`` pairing is
    resolved on that same owner. A child that redefines ``path`` on its own
    table owns that column and is checked in its own right, as before. A
    consumer who set ``check_tree_integrity = False`` on MTI children purely to
    silence this check can remove it.

    This is a Warning, not an Error, for this release: icv-media's
    MediaFolder declares no such constraint today, and an Error would block
    its migrate on upgrade (icvoss/icv-media#72). It is promoted to an Error
    at the next major release, once consumers have adopted the constraint.

    Consuming models may opt out by setting ``check_tree_integrity = False``
    on the model class (BR-TREE-043), the same attribute
    ``check_all_tree_models`` honours: both checks answer "is this model's
    tree data trustworthy", so a model that has opted out of one has opted
    out of the other.

    Auto-registered on the ``models`` tag (see ``apps.py``): unlike
    ``check_all_tree_models``, this check only inspects Meta declarations, so
    it is cheap enough to run on every ``check`` and ``migrate``.
    """
    warnings: list = []
    reported: set = set()

    for model in _installed_tree_models():
        owner = _path_owner(model)

        # An MTI parent and each of its children resolve to the same owner, so
        # report the owner once rather than once per subclass.
        if owner in reported:
            continue

        scope_field = getattr(owner, "tree_scope_field", None)
        expected = frozenset({scope_field, "path"}) if scope_field else frozenset({"path"})

        if expected in _constrained_field_sets(owner):
            continue

        reported.add(owner)

        expected_repr = ", ".join(sorted(expected))
        constraint_hint = (
            f'models.UniqueConstraint(fields=["{scope_field}", "path"], name="unique_{owner._meta.model_name}_path")'
            if scope_field
            else f'models.UniqueConstraint(fields=["path"], name="unique_{owner._meta.model_name}_path")'
        )
        inherited_note = (
            ""
            if owner is model
            else (
                f" {model.__name__} inherits path from {owner.__name__} through multi-table inheritance, "
                f"so the constraint belongs on {owner.__name__}, whose table holds the column."
            )
        )

        warnings.append(
            Warning(
                f"{owner.__name__} declares no uniqueness constraint covering exactly "
                f"{{{expected_repr}}} on its path field.",
                hint=(
                    f"Add to {owner.__name__}.Meta.constraints: {constraint_hint}.{inherited_note} "
                    f"Opt out with check_tree_integrity = False on the model if this is intentional."
                ),
                id="icv_tree.W001",
                obj=owner,
            )
        )

    return warnings
