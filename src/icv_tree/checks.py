"""
Django system checks for icv-tree.

Registered under ``Tags.database`` so they only run when the caller opts in
to database checks (``manage.py check --database default``).  This avoids
blocking ``runserver`` / ``migrate`` with potentially expensive integrity
queries on every startup.

E001 — Warning: orphaned nodes (parent_id references missing rows)
E002 — Error: path inconsistencies (depth mismatch, prefix violation, duplicate paths)
"""

from __future__ import annotations

from django.apps import apps
from django.core.checks import Error, Tags, Warning, register


@register(Tags.database)
def check_all_tree_models(app_configs, databases=None, **kwargs):  # type: ignore[no-untyped-def]
    """Check all concrete TreeNode subclasses for tree integrity issues.

    Generates:
      icv_tree.E001 Warning — orphaned nodes
      icv_tree.E002 Error   — path inconsistencies

    Consuming models may opt out by setting ``check_tree_integrity = False``
    on the model class (BR-TREE-043).

    Only runs when ``databases`` includes the alias used by the model
    (respects Django's ``--database`` flag).
    """
    from .models import TreeNode
    from .services.integrity import check_tree_integrity

    errors: list = []

    if databases is None:
        return errors

    # Find all concrete (non-abstract) TreeNode subclasses that are installed.
    tree_models = [
        model
        for model in apps.get_models()
        if issubclass(model, TreeNode) and not model._meta.abstract and getattr(model, "check_tree_integrity", True)
    ]

    for model in tree_models:
        # Respect --database: skip models whose DB alias isn't in the set.
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
