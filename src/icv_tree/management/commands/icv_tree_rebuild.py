"""
Management command: icv_tree_rebuild

Rebuilds or checks the materialised path fields for a given TreeNode model.

Usage examples::

    python manage.py icv_tree_rebuild --model=myapp.Page
    python manage.py icv_tree_rebuild --model=myapp.Page --dry-run
    python manage.py icv_tree_rebuild --model=myapp.Page --check
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Rebuild or check materialised path fields for a TreeNode model."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument(
            "--model",
            type=str,
            required=True,
            help="Dotted model path: app_label.ModelName (e.g., cms.Page).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Report what would change without writing to the database.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            default=False,
            help=(
                "Run check_tree_integrity() and report issues without repairing. Exits with code 1 if issues are found."
            ),
        )

    def handle(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        model_path = kwargs["model"]
        dry_run = kwargs["dry_run"]
        check_only = kwargs["check"]
        verbosity = kwargs["verbosity"]

        # Import the model.
        try:
            app_label, model_name = model_path.rsplit(".", 1)
        except ValueError as exc:
            raise CommandError(f"--model must be in 'app_label.ModelName' format. Got: {model_path!r}") from exc

        from django.apps import apps

        try:
            model = apps.get_model(app_label, model_name)
        except LookupError as exc:
            raise CommandError(f"Model '{model_path}' not found.") from exc

        from icv_tree.models import TreeNode

        if not issubclass(model, TreeNode):
            raise CommandError(f"'{model_path}' is not a TreeNode subclass.")

        if check_only:
            self._run_check(model, verbosity)
        elif dry_run:
            self._run_dry_run(model, verbosity)
        else:
            self._run_rebuild(model, verbosity)

    def _run_check(self, model, verbosity: int) -> None:  # type: ignore[no-untyped-def]
        from icv_tree.services import check_tree_integrity

        result = check_tree_integrity(model)
        model_label = f"{model._meta.app_label}.{model.__name__}"

        if verbosity >= 1:
            self.stdout.write(f"Checking tree integrity for {model_label}...")

        if result["total_issues"] == 0:
            self.stdout.write(self.style.SUCCESS(f"{model_label}: No issues found."))
        else:
            if result["orphaned_nodes"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Orphaned nodes ({len(result['orphaned_nodes'])}): "
                        f"{result['orphaned_nodes'][:10]}" + (" ..." if len(result["orphaned_nodes"]) > 10 else "")
                    )
                )
            if result["depth_mismatches"]:
                self.stdout.write(
                    self.style.ERROR(
                        f"  Depth mismatches ({len(result['depth_mismatches'])}): "
                        f"{result['depth_mismatches'][:10]}" + (" ..." if len(result["depth_mismatches"]) > 10 else "")
                    )
                )
            if result["path_prefix_violations"]:
                self.stdout.write(
                    self.style.ERROR(
                        f"  Path prefix violations ({len(result['path_prefix_violations'])}): "
                        f"{result['path_prefix_violations'][:10]}"
                        + (" ..." if len(result["path_prefix_violations"]) > 10 else "")
                    )
                )
            if result["duplicate_paths"]:
                self.stdout.write(
                    self.style.ERROR(
                        f"  Duplicate paths ({len(result['duplicate_paths'])}): "
                        f"{result['duplicate_paths'][:10]}" + (" ..." if len(result["duplicate_paths"]) > 10 else "")
                    )
                )
            raise SystemExit(1)

    def _run_dry_run(self, model, verbosity: int) -> None:  # type: ignore[no-untyped-def]
        """Report what rebuild() would change without writing to the database."""
        from icv_tree.services import check_tree_integrity

        model_label = f"{model._meta.app_label}.{model.__name__}"

        if verbosity >= 1:
            self.stdout.write(f"Dry-run: checking what rebuild would change for {model_label}...")

        result = check_tree_integrity(model)
        total_issues = result["total_issues"]

        if total_issues == 0:
            self.stdout.write(self.style.SUCCESS(f"{model_label}: Tree is consistent. No changes needed."))
        else:
            self.stdout.write(
                self.style.WARNING(f"{model_label}: {total_issues} issue(s) would be repaired by rebuild.")
            )

    def _run_rebuild(self, model, verbosity: int) -> None:  # type: ignore[no-untyped-def]
        from icv_tree.services import rebuild

        model_label = f"{model._meta.app_label}.{model.__name__}"

        if verbosity >= 1:
            self.stdout.write(f"Rebuilding tree for {model_label}...")

        result = rebuild(model)

        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{model_label}: Rebuilt. "
                    f"{result['nodes_updated']} node(s) updated, "
                    f"{result['nodes_unchanged']} unchanged."
                )
            )
