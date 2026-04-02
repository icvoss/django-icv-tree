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
