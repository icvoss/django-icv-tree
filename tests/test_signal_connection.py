"""Tests for per-sender connection of the pre_save/post_delete signal handlers.

Regression coverage for icvoss/django-icv-tree#24: handle_pre_save and
handle_post_delete in handlers.py used to connect with no sender, attaching
to every model in a consuming project. Django's Collector.can_fast_delete()
returns False for any model carrying a pre_delete/post_delete listener, so
this silently removed the fast-delete path (a single DELETE ... WHERE) from
every queryset .delete() in a consumer project, not just TreeNode deletes.
"""

from __future__ import annotations

import pytest
from conftest import throwaway_models
from django.db.models.deletion import Collector
from django.db.models.signals import post_delete, pre_save

# ---------------------------------------------------------------------------
# Receivers ARE connected for concrete TreeNode subclasses, including MTI
# ---------------------------------------------------------------------------


class TestHandlersConnectedToTreeNodeSubclasses:
    """AC-24: the icv-tree handlers must attach to every concrete TreeNode subclass."""

    def test_pre_save_has_listeners_for_simple_tree(self):
        from tree_testapp.models import SimpleTree

        assert pre_save.has_listeners(SimpleTree) is True

    def test_post_delete_has_listeners_for_simple_tree(self):
        from tree_testapp.models import SimpleTree

        assert post_delete.has_listeners(SimpleTree) is True

    def test_pre_save_has_listeners_for_uuid_tree(self):
        from tree_testapp.models import UUIDTree

        assert pre_save.has_listeners(UUIDTree) is True

    def test_post_delete_has_listeners_for_uuid_tree(self):
        from tree_testapp.models import UUIDTree

        assert post_delete.has_listeners(UUIDTree) is True

    def test_pre_save_has_listeners_for_scoped_tree(self):
        from tree_testapp.models import ScopedTree

        assert pre_save.has_listeners(ScopedTree) is True

    def test_post_delete_has_listeners_for_scoped_tree(self):
        from tree_testapp.models import ScopedTree

        assert post_delete.has_listeners(ScopedTree) is True

    def test_pre_save_has_listeners_for_mti_base_page(self):
        """Page is a concrete TreeNode subclass in its own right (MTI base)."""
        from tree_testapp.models import Page

        assert pre_save.has_listeners(Page) is True
        assert post_delete.has_listeners(Page) is True

    def test_mti_children_are_each_connected(self):
        """RegularPage and RedirectPage are separate concrete models sharing the
        Page base table; apps.get_models() returns all three, and each must be
        connected independently, not just the shared MTI base.
        """
        from tree_testapp.models import RedirectPage, RegularPage

        assert pre_save.has_listeners(RegularPage) is True
        assert post_delete.has_listeners(RegularPage) is True
        assert pre_save.has_listeners(RedirectPage) is True
        assert post_delete.has_listeners(RedirectPage) is True

    def test_apps_get_models_returns_all_three_page_models(self):
        """Constraint check: prove the MTI family is actually enumerated by
        apps.get_models(), which is what _connect_tree_handlers() walks.
        """
        from django.apps import apps
        from tree_testapp.models import Page, RedirectPage, RegularPage

        all_models = set(apps.get_models())
        assert {Page, RegularPage, RedirectPage} <= all_models


# ---------------------------------------------------------------------------
# Receivers are NOT connected to unrelated models
# ---------------------------------------------------------------------------


class TestHandlersNotConnectedToUnrelatedModels:
    """AC-24: the icv-tree handlers must not attach to non-TreeNode models."""

    def test_pre_save_has_no_listeners_for_unrelated_model(self):
        from tree_testapp.models import UnrelatedModel

        assert pre_save.has_listeners(UnrelatedModel) is False

    def test_post_delete_has_no_listeners_for_unrelated_model(self):
        """post_delete.has_listeners() is False for a model the package does not own.

        This is the load-bearing assertion for #24: post_delete listeners are
        what disable Django's fast-delete path via Collector.can_fast_delete().
        """
        from tree_testapp.models import UnrelatedModel

        assert post_delete.has_listeners(UnrelatedModel) is False

    def test_scope_model_has_no_tree_listeners(self):
        """A consumer's own unrelated model (Scope) is not wired to icv-tree handlers.

        Scope has an incoming FK from ScopedTree, so it is not itself
        fast-deletable, but that must come from its own relations, never
        from a tree handler attaching to it directly.
        """
        from tree_testapp.models import Scope

        from icv_tree import handlers

        sync_receivers, _async_receivers = post_delete._live_receivers(Scope)
        assert handlers.handle_post_delete not in sync_receivers


# ---------------------------------------------------------------------------
# Fast-delete regression: an unrelated, cascade-free model regains
# Collector.can_fast_delete()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFastDeleteRegainedForUnrelatedModels:
    """AC-24: a cascade-free consumer model regains Django's fast-delete path.

    UnrelatedModel has no incoming ForeignKey from any model in tree_testapp
    (and nothing else in this fixture's INSTALLED_APPS references it), so a
    signal listener FROM THIS PACKAGE was the only thing #24's fix controls
    that could block can_fast_delete() for it. Scope is deliberately NOT
    used here: it has an incoming FK from ScopedTree (on_delete=CASCADE), so
    Collector.can_fast_delete(Scope.objects.all()) is False regardless of
    icv-tree's signal wiring, which would make an assertion on it vacuous
    either way.

    Measured baseline on unmodified main (this repo's own fixture, Django
    6.1, tests/settings.py): all 14 models registered in this settings
    module carry exactly one delete listener each (icv_tree's own bare
    post_delete receiver) and 0/14 have can_fast_delete() == True.
    Disconnecting icv_tree.handlers.handle_post_delete flips exactly two of
    them to True (admin.LogEntry, sessions.Session); every other model in
    the fixture, including UnrelatedModel's siblings, stays False for
    reasons unrelated to icv-tree's listeners (an incoming CASCADE FK, a
    concrete MTI parent, or similar). UnrelatedModel is the dedicated,
    genuinely cascade-free fixture added in this change specifically so
    this assertion has a model it can be true FOR.

    Each test below asserts both halves: fast-delete is regained, AND no
    icv_tree listener is attached to this model at all. Asserting only the
    first half would not prove the second (a passing collector check says
    nothing about why), and asserting only the second half is already
    covered separately in TestHandlersNotConnectedToUnrelatedModels.
    """

    def test_unrelated_model_regains_fast_delete(self, db):
        from tree_testapp.models import UnrelatedModel

        UnrelatedModel.objects.create(name="Fast Delete Candidate")

        collector = Collector(using="default")
        assert collector.can_fast_delete(UnrelatedModel.objects.all()) is True

        sync_pre_save, _ = pre_save._live_receivers(UnrelatedModel)
        sync_post_delete, _ = post_delete._live_receivers(UnrelatedModel)
        assert sync_pre_save == []
        assert sync_post_delete == []

    def test_fast_delete_is_false_when_a_post_delete_listener_is_added(self, db):
        """Control: proves the assertion above is sensitive to a real listener,
        not vacuously true regardless of connection state.
        """
        from tree_testapp.models import UnrelatedModel

        UnrelatedModel.objects.create(name="Control Candidate")

        def noop(**kwargs) -> None:
            return None

        post_delete.connect(noop, sender=UnrelatedModel, dispatch_uid="test.control.noop", weak=False)
        try:
            collector = Collector(using="default")
            assert collector.can_fast_delete(UnrelatedModel.objects.all()) is False
        finally:
            post_delete.disconnect(sender=UnrelatedModel, dispatch_uid="test.control.noop")

        # And it is restored once the control listener is removed.
        collector = Collector(using="default")
        assert collector.can_fast_delete(UnrelatedModel.objects.all()) is True

    def test_simple_tree_itself_is_not_fast_deletable(self, db):
        """SimpleTree still carries its own post_delete listener (by design):
        handle_post_delete must keep firing per row to repair sibling order
        (BR-TREE-022). This is the intended, narrowed cost: only TreeNode
        subclasses pay it, not every consumer model.
        """
        from tree_testapp.models import SimpleTree

        collector = Collector(using="default")
        assert collector.can_fast_delete(SimpleTree.objects.all()) is False


# ---------------------------------------------------------------------------
# A model defined after ready() is wired via class_prepared
# ---------------------------------------------------------------------------


class TestModelDefinedAfterReadyIsWired:
    """AC-24: a TreeNode subclass defined after IcvTreeConfig.ready() has already
    run (a test-local model, mirroring a dynamically built model in a real
    consumer) still gets its handlers connected, via the class_prepared path.
    """

    def test_class_prepared_connects_a_late_defined_tree_node_subclass(self):
        from django.db import models

        from icv_tree.models import TreeNode

        class LateDefinedTree(TreeNode):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "tree_testapp"
                db_table = "tree_testapp_latedefinedtree"

        with throwaway_models(LateDefinedTree):
            assert pre_save.has_listeners(LateDefinedTree) is True
            assert post_delete.has_listeners(LateDefinedTree) is True

    def test_class_prepared_guard_is_a_noop_before_models_ready(self):
        """_connect_handlers_for_new_model() returns early when apps.models_ready
        is False, rather than attempting to resolve a model against a registry
        that is not populated yet. Simulate that state directly rather than
        relying on Django's own startup ordering, which has already completed
        by the time tests run.
        """
        from unittest.mock import patch

        from icv_tree import handlers

        with patch("django.apps.apps.models_ready", False):
            calls = []
            original = handlers._connect_for_model

            def spy(model):  # type: ignore[no-untyped-def]
                calls.append(model)
                return original(model)

            with patch.object(handlers, "_connect_for_model", side_effect=spy):
                handlers._connect_handlers_for_new_model(sender=object)
            assert calls == []

    def test_class_prepared_leaves_a_late_defined_unrelated_model_untouched(self):
        """The class_prepared path is not a blanket per-model attach: a
        cascade-free, non-TreeNode model defined after ready() (mirroring a
        model a consumer defines later, e.g. via a dynamically loaded app)
        gets no icv_tree listener, the same as one already registered at
        ready() time (TestHandlersNotConnectedToUnrelatedModels above).
        """
        from django.db import models

        class LateDefinedUnrelated(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "tree_testapp"
                db_table = "tree_testapp_latedefinedunrelated"

            def __str__(self) -> str:
                return self.name

        with throwaway_models(LateDefinedUnrelated):
            assert pre_save.has_listeners(LateDefinedUnrelated) is False
            assert post_delete.has_listeners(LateDefinedUnrelated) is False


# ---------------------------------------------------------------------------
# dispatch_uid guards against double connection
# ---------------------------------------------------------------------------


class TestDoubleReadyDoesNotDoubleConnect:
    """AC-24: calling the connect functions twice must not register a handler twice."""

    def test_connect_tree_handlers_is_idempotent(self):
        from tree_testapp.models import SimpleTree

        from icv_tree import handlers

        sync_before, async_before = pre_save._live_receivers(SimpleTree)

        # Simulate a second ready() call.
        handlers._connect_tree_handlers()

        sync_after, async_after = pre_save._live_receivers(SimpleTree)
        assert len(sync_after) == len(sync_before)
        assert len(async_after) == len(async_before)

    def test_connect_for_model_called_twice_does_not_double_connect(self):
        from tree_testapp.models import SimpleTree

        from icv_tree import handlers

        sync_before, _ = post_delete._live_receivers(SimpleTree)

        handlers._connect_for_model(SimpleTree)
        handlers._connect_for_model(SimpleTree)

        sync_after, _ = post_delete._live_receivers(SimpleTree)
        assert len(sync_after) == len(sync_before)


# ---------------------------------------------------------------------------
# Existing tree behaviour is unchanged by the per-sender connection change
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExistingTreeBehaviourUnchanged:
    """AC-24: the connection mechanism changed; the handlers' own behaviour did not."""

    def test_new_root_node_gets_a_path(self, db, simple_tree_model):
        node = simple_tree_model(name="Root")
        node.save()
        node.refresh_from_db()

        assert node.path
        assert node.depth == 0

    def test_deleting_a_node_reorders_siblings(self, db, make_node):
        root = make_node("root")
        child1 = make_node("child1", parent=root)
        child2 = make_node("child2", parent=root)

        assert child2.order == 1

        child1.delete()
        child2.refresh_from_db()

        assert child2.order == 0
