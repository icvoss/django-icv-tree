"""Tests for icv_tree.handlers: same-parent hand-edited tree field guard.

Regression for icvoss/django-icv-tree#27. handle_pre_save()'s existing-node
branch only acted when parent_id changed; a same-parent save with a
hand-edited path/depth/order was written to the database unchanged, with no
exception and no log line, regardless of ICV_TREE_CHECK_ON_SAVE.
"""

from __future__ import annotations

import pytest


@pytest.mark.django_db
class TestCheckOnSaveEnabled:
    """ICV_TREE_CHECK_ON_SAVE=True: a same-parent hand-edited field raises."""

    def test_hand_edited_path_raises(self, settings, tree_nodes):
        """Fails on old code: no exception is raised, and the bad path is saved.

        Setting is read at call time via get_setting(), so override_settings
        (the settings fixture) takes effect on the next save without a
        process restart.
        """
        from icv_tree.exceptions import TreeStructureError

        settings.ICV_TREE_CHECK_ON_SAVE = True

        child1 = tree_nodes["child1"]
        child1.path = "CORRUPTED"
        with pytest.raises(TreeStructureError, match="path"):
            child1.save()


@pytest.mark.django_db
class TestCheckOnSaveDisabled:
    """ICV_TREE_CHECK_ON_SAVE=False (default): a mismatch is logged, not raised."""

    def test_hand_edited_path_logs_and_saves(self, settings, tree_nodes, caplog):
        """Fails on old code: no icv_tree log record is emitted at all.

        caplog is keyed on record.name == "icv_tree", not caplog.text, per
        the ecosystem's send_robust/log-record discipline.
        """
        import logging

        settings.ICV_TREE_CHECK_ON_SAVE = False

        child1 = tree_nodes["child1"]
        child1.path = "CORRUPTED"
        with caplog.at_level(logging.WARNING, logger="icv_tree"):
            child1.save()  # must not raise

        icv_records = [r for r in caplog.records if r.name == "icv_tree"]
        assert len(icv_records) == 1

        child1.refresh_from_db()
        assert child1.path == "CORRUPTED"


@pytest.mark.django_db
class TestRawSaveSkipsCheck:
    """raw=True (loaddata) must skip the whole existing-node comparison."""

    def test_raw_save_with_check_on_save_does_not_raise(self, settings, tree_nodes):
        """Control: a raw save with a hand-edited path and the setting on
        must not raise, since loaddata needs to write values verbatim.
        """
        settings.ICV_TREE_CHECK_ON_SAVE = True

        child1 = tree_nodes["child1"]
        child1.path = "CORRUPTED"
        child1.save_base(raw=True)  # must not raise


@pytest.mark.django_db
class TestUntouchedSaveIsSilent:
    """Control: a same-parent re-save with no field changes emits nothing."""

    def test_untouched_resave_emits_no_warning_and_does_not_raise(self, settings, tree_nodes, caplog):
        import logging

        settings.ICV_TREE_CHECK_ON_SAVE = True

        child1 = tree_nodes["child1"]
        with caplog.at_level(logging.WARNING, logger="icv_tree"):
            child1.save()  # no field mutated, must not raise

        icv_records = [r for r in caplog.records if r.name == "icv_tree"]
        assert icv_records == []
