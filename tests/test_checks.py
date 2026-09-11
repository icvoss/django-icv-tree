"""Tests for icv_tree.checks (icv_tree.E001, icv_tree.E002).

``check_all_tree_models`` is NOT auto-registered with Django's check
framework (it is too expensive for startup).  It is called explicitly
from the ``icv_tree_rebuild --check`` management command and tested here
via direct invocation.
"""

from __future__ import annotations

import pytest


@pytest.mark.django_db
class TestSystemChecks:
    """Test the check_all_tree_models function."""

    def test_no_warnings_on_healthy_tree(self, tree_nodes):
        """A healthy tree should produce no check errors."""
        from icv_tree.checks import check_all_tree_models

        errors = check_all_tree_models()
        icv_errors = [e for e in errors if e.id and e.id.startswith("icv_tree.")]
        assert len(icv_errors) == 0

    def test_e001_warns_on_orphaned_nodes(self, db, simple_tree_model, make_node):
        """Check should emit icv_tree.E001 when orphaned nodes exist."""
        from django.db import connection

        from icv_tree.checks import check_all_tree_models

        root = make_node("root")
        child = make_node("child", parent=root)
        child_pk = child.pk

        # Delete the root via raw SQL to leave child with a dangling parent_id
        # (bypasses Django's CASCADE which would also delete child).
        with connection.cursor() as cursor:
            table = simple_tree_model._meta.db_table
            pk_col = simple_tree_model._meta.pk.column
            cursor.execute(
                f"DELETE FROM {table} WHERE {pk_col} = %s",  # noqa: S608
                [root.pk],
            )

        errors = check_all_tree_models()
        e001_errors = [e for e in errors if getattr(e, "id", None) == "icv_tree.E001"]

        # Clean up the orphan so PostgreSQL FK constraint check at teardown passes.
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {table} WHERE {pk_col} = %s",  # noqa: S608
                [child_pk],
            )

        assert len(e001_errors) >= 1

    def test_e002_errors_on_path_inconsistencies(self, db, simple_tree_model, make_node):
        """Check should emit icv_tree.E002 when path is inconsistent."""
        from icv_tree.checks import check_all_tree_models

        root = make_node("root")
        child = make_node("child", parent=root)

        # Corrupt the path so depth doesn't match.
        simple_tree_model.objects.filter(pk=child.pk).update(depth=99)

        errors = check_all_tree_models()
        e002_errors = [e for e in errors if getattr(e, "id", None) == "icv_tree.E002"]
        assert len(e002_errors) >= 1

    def test_check_opt_out_via_class_attribute(self, db):
        """Models with check_tree_integrity=False should be skipped."""
        from tree_testapp.models import OptOutTree

        from icv_tree.checks import check_all_tree_models

        # Create a node with a corrupted path.
        node = OptOutTree(name="root")
        node.save()
        OptOutTree.objects.filter(pk=node.pk).update(depth=99)

        errors = check_all_tree_models()
        # No errors for OptOutTree specifically.
        opt_out_errors = [e for e in errors if getattr(e, "obj", None) is OptOutTree]
        assert len(opt_out_errors) == 0


class TestCheckPathUniqueness:
    """Test check_path_uniqueness (icv_tree.W001, icvoss/django-icv-tree#31).

    Called directly rather than via registry.run_checks(): on Django 6.1,
    run_checks() also runs database checks and trips the "no queries during
    checks" guard, per project-standards LESSONS.md. check_path_uniqueness
    itself only inspects Meta declarations, so it needs no database access
    and no @pytest.mark.django_db.
    """

    def test_unconstrained_unscoped_model_warns(self):
        """A concrete model with no constraint on path yields icv_tree.W001.

        SimpleTree (tree_testapp) declares no UniqueConstraint or
        unique_together at all, so it is the naturally occurring fixture for
        the missing-constraint case; no throwaway model needed.

        Teeth: this assertion fails on pre-#31 code because
        check_path_uniqueness does not exist yet (ImportError), not because
        of any behavioural difference; that is expected for a new check.
        """
        from tree_testapp.models import SimpleTree

        from icv_tree.checks import check_path_uniqueness

        warnings = check_path_uniqueness()
        simple_tree_warnings = [w for w in warnings if getattr(w, "obj", None) is SimpleTree]

        assert len(simple_tree_warnings) == 1
        assert simple_tree_warnings[0].id == "icv_tree.W001"
        assert "SimpleTree" in simple_tree_warnings[0].msg
        assert "path" in simple_tree_warnings[0].msg
        assert "UniqueConstraint" in simple_tree_warnings[0].hint

    def test_scoped_model_with_unique_together_on_scope_and_path_is_silent(self):
        """ScopedTree declares unique_together = (scope, path); no warning.

        ScopedTree.tree_scope_field = "scope", and its Meta declares
        unique_together = [("scope", "path")], which is exactly the expected
        field set for a scoped model. This is the naturally occurring
        satisfying fixture; no throwaway model needed.
        """
        from tree_testapp.models import ScopedTree

        from icv_tree.checks import check_path_uniqueness

        warnings = check_path_uniqueness()
        scoped_tree_warnings = [w for w in warnings if getattr(w, "obj", None) is ScopedTree]

        assert scoped_tree_warnings == []

    def test_unscoped_model_with_unique_constraint_on_path_is_silent(self):
        """A throwaway unscoped model with UniqueConstraint(fields=["path"]) is silent.

        Registered directly on the tree_testapp app_label, mirroring the
        late-defined-model pattern in test_signal_connection.py, and removed
        in a finally block so it does not leak into other tests that
        enumerate installed TreeNode subclasses (e.g. check_all_tree_models,
        _connect_tree_handlers).
        """
        from django.apps import apps
        from django.db import models

        from icv_tree.checks import check_path_uniqueness
        from icv_tree.models import TreeNode

        class ConstrainedUnscopedTree(TreeNode):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "tree_testapp"
                db_table = "tree_testapp_constrainedunscopedtree"
                constraints = [
                    models.UniqueConstraint(fields=["path"], name="unique_constrainedunscopedtree_path"),
                ]

        try:
            warnings = check_path_uniqueness()
            model_warnings = [w for w in warnings if getattr(w, "obj", None) is ConstrainedUnscopedTree]
            assert model_warnings == []
        finally:
            apps.all_models["tree_testapp"].pop("constrainedunscopedtree", None)
            apps.clear_cache()

    def test_scoped_model_with_constraint_on_path_only_still_warns(self):
        """A scoped model whose constraint covers only path (not scope) still warns.

        The field set must equal exactly {tree_scope_field, "path"} for a
        scoped model; a UniqueConstraint(fields=["path"]) alone is the wrong
        field set (it does not prevent cross-scope path collisions), so this
        must still produce icv_tree.W001.

        Teeth: if the field-set comparison were loosened to "path appears in
        any constraint" rather than an exact set match, this assertion is the
        one that would start failing (the model would wrongly go silent),
        because {"path"} would satisfy a containment check even though it is
        missing the scope field.
        """
        from django.apps import apps
        from django.db import models

        from icv_tree.checks import check_path_uniqueness
        from icv_tree.models import TreeNode

        class WrongFieldSetScopedTree(TreeNode):
            tree_scope_field = "scope"

            name = models.CharField(max_length=100)
            scope = models.ForeignKey(
                "tree_testapp.Scope",
                on_delete=models.CASCADE,
                related_name="wrong_field_set_scoped_nodes",
            )

            class Meta:
                app_label = "tree_testapp"
                db_table = "tree_testapp_wrongfieldsetscopedtree"
                constraints = [
                    models.UniqueConstraint(fields=["path"], name="unique_wrongfieldsetscopedtree_path"),
                ]

        try:
            warnings = check_path_uniqueness()
            model_warnings = [w for w in warnings if getattr(w, "obj", None) is WrongFieldSetScopedTree]
            assert len(model_warnings) == 1
            assert model_warnings[0].id == "icv_tree.W001"
            assert "scope" in model_warnings[0].msg
            assert "path" in model_warnings[0].msg
        finally:
            apps.all_models["tree_testapp"].pop("wrongfieldsetscopedtree", None)
            apps.clear_cache()

    def test_opt_out_model_is_skipped(self):
        """A model with check_tree_integrity = False is excluded, same as check_all_tree_models."""
        from tree_testapp.models import OptOutTree

        from icv_tree.checks import check_path_uniqueness

        warnings = check_path_uniqueness()
        opt_out_warnings = [w for w in warnings if getattr(w, "obj", None) is OptOutTree]
        assert opt_out_warnings == []


@pytest.mark.django_db
class TestAppConfigValidation:
    """Test that IcvTreeConfig.ready() validates settings correctly."""

    def test_invalid_separator_empty_string_raises(self):
        """Empty path separator should raise ImproperlyConfigured."""
        from django.core.exceptions import ImproperlyConfigured

        from icv_tree.apps import IcvTreeConfig

        with pytest.raises(ImproperlyConfigured, match="ICV_TREE_PATH_SEPARATOR"):
            IcvTreeConfig._validate_settings(lambda name, default: "" if name == "ICV_TREE_PATH_SEPARATOR" else default)

    def test_invalid_separator_multi_char_raises(self):
        """Multi-character path separator should raise ImproperlyConfigured."""
        from django.core.exceptions import ImproperlyConfigured

        from icv_tree.apps import IcvTreeConfig

        with pytest.raises(ImproperlyConfigured, match="ICV_TREE_PATH_SEPARATOR"):
            IcvTreeConfig._validate_settings(
                lambda name, default: "//" if name == "ICV_TREE_PATH_SEPARATOR" else default
            )

    def test_invalid_separator_digit_raises(self):
        """Digit path separator should raise ImproperlyConfigured."""
        from django.core.exceptions import ImproperlyConfigured

        from icv_tree.apps import IcvTreeConfig

        with pytest.raises(ImproperlyConfigured, match="must not be a digit"):
            IcvTreeConfig._validate_settings(
                lambda name, default: "1" if name == "ICV_TREE_PATH_SEPARATOR" else default
            )

    def test_invalid_step_length_zero_raises(self):
        """Step length of 0 should raise ImproperlyConfigured."""
        from django.core.exceptions import ImproperlyConfigured

        from icv_tree.apps import IcvTreeConfig

        with pytest.raises(ImproperlyConfigured, match="ICV_TREE_STEP_LENGTH"):
            IcvTreeConfig._validate_settings(lambda name, default: 0 if name == "ICV_TREE_STEP_LENGTH" else default)

    def test_invalid_step_length_eleven_raises(self):
        """Step length of 11 should raise ImproperlyConfigured."""
        from django.core.exceptions import ImproperlyConfigured

        from icv_tree.apps import IcvTreeConfig

        with pytest.raises(ImproperlyConfigured, match="ICV_TREE_STEP_LENGTH"):
            IcvTreeConfig._validate_settings(lambda name, default: 11 if name == "ICV_TREE_STEP_LENGTH" else default)

    def test_valid_settings_do_not_raise(self):
        """Valid settings should not raise."""
        from icv_tree.apps import IcvTreeConfig

        # Should not raise.
        IcvTreeConfig._validate_settings(lambda name, default: default)

    def test_invalid_rebuild_batch_size_zero_raises(self):
        """A batch size of 0 should raise ImproperlyConfigured.

        Regression for icvoss/django-icv-tree#36: ICV_TREE_REBUILD_BATCH_SIZE
        was read by move_to()/rebuild() but never validated at startup, so 0
        instead raised ValueError partway through a batch loop, mid-transaction.
        """
        from django.core.exceptions import ImproperlyConfigured

        from icv_tree.apps import IcvTreeConfig

        with pytest.raises(ImproperlyConfigured, match="ICV_TREE_REBUILD_BATCH_SIZE"):
            IcvTreeConfig._validate_settings(
                lambda name, default: 0 if name == "ICV_TREE_REBUILD_BATCH_SIZE" else default
            )

    def test_invalid_rebuild_batch_size_negative_raises(self):
        """A negative batch size should raise ImproperlyConfigured.

        Regression for icvoss/django-icv-tree#36: a negative value produced
        an empty range() silently, so every computed row change was never
        persisted, with no exception at startup or at call time.
        """
        from django.core.exceptions import ImproperlyConfigured

        from icv_tree.apps import IcvTreeConfig

        with pytest.raises(ImproperlyConfigured, match="ICV_TREE_REBUILD_BATCH_SIZE"):
            IcvTreeConfig._validate_settings(
                lambda name, default: -1 if name == "ICV_TREE_REBUILD_BATCH_SIZE" else default
            )

    def test_rebuild_batch_size_one_does_not_raise(self):
        """A batch size of 1 is the smallest valid value and must not raise."""
        from icv_tree.apps import IcvTreeConfig

        IcvTreeConfig._validate_settings(lambda name, default: 1 if name == "ICV_TREE_REBUILD_BATCH_SIZE" else default)

    def test_default_rebuild_batch_size_does_not_raise(self):
        """The default ICV_TREE_REBUILD_BATCH_SIZE (1000) must not raise."""
        from icv_tree.apps import IcvTreeConfig

        IcvTreeConfig._validate_settings(lambda name, default: default)
