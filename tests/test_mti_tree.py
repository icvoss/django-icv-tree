"""Tests for tree traversal across multi-table-inheritance subtypes.

When a TreeNode base model has several concrete MTI children (e.g. a CMS
``Page`` base with ``RegularPage`` / ``RedirectPage`` children), every
tree-walking query must scope to the base table. A query bound to one concrete
subclass (``RegularPage.objects``) only sees that subclass's rows and would
miss ancestors or descendants stored as a sibling subtype.

These tests build a mixed-subtype tree and assert that traversal finds nodes
regardless of which concrete subtype wrote them.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def page_models(db):
    from tree_testapp.models import Page, RedirectPage, RegularPage

    return Page, RegularPage, RedirectPage


@pytest.fixture
def mixed_tree(page_models):
    """Build a 3-level tree alternating subtypes.

    root (RegularPage)
      └── mid (RedirectPage)
            └── leaf (RegularPage)
    """
    Page, RegularPage, RedirectPage = page_models

    root = RegularPage(name="root")
    root.save()
    mid = RedirectPage(name="mid", parent=root, target_url="/elsewhere")
    mid.save()
    leaf = RegularPage(name="leaf", parent=mid)
    leaf.save()

    for node in (root, mid, leaf):
        node.refresh_from_db()
    return {"root": root, "mid": mid, "leaf": leaf, "Page": Page}


@pytest.mark.django_db
class TestTreeModelResolution:
    """_tree_model() resolves to the base TreeNode for MTI children."""

    def test_base_model_resolves_to_itself(self, page_models):
        Page, _RegularPage, _RedirectPage = page_models
        assert Page._tree_model() is Page

    def test_child_models_resolve_to_base(self, page_models):
        Page, RegularPage, RedirectPage = page_models
        assert RegularPage._tree_model() is Page
        assert RedirectPage._tree_model() is Page

    def test_simple_model_resolves_to_itself(self, simple_tree_model):
        # Non-MTI models are unaffected: the base case returns cls.
        assert simple_tree_model._tree_model() is simple_tree_model


@pytest.mark.django_db
class TestAncestorsAcrossSubtypes:
    def test_leaf_ancestors_include_other_subtype(self, mixed_tree):
        leaf = mixed_tree["leaf"]
        names = list(leaf.get_ancestors().values_list("name", flat=True))
        # root (RegularPage) and mid (RedirectPage) must both appear.
        assert names == ["root", "mid"]

    def test_ancestors_include_self(self, mixed_tree):
        leaf = mixed_tree["leaf"]
        names = list(leaf.get_ancestors(include_self=True).values_list("name", flat=True))
        assert names == ["root", "mid", "leaf"]


@pytest.mark.django_db
class TestDescendantsAcrossSubtypes:
    def test_root_descendants_include_other_subtype(self, mixed_tree):
        root = mixed_tree["root"]
        names = set(root.get_descendants().values_list("name", flat=True))
        assert names == {"mid", "leaf"}

    def test_descendant_count_counts_all_subtypes(self, mixed_tree):
        root = mixed_tree["root"]
        assert root.get_descendant_count() == 2

    def test_descendants_include_self(self, mixed_tree):
        root = mixed_tree["root"]
        names = set(root.get_descendants(include_self=True).values_list("name", flat=True))
        assert names == {"root", "mid", "leaf"}


@pytest.mark.django_db
class TestChildrenAndSiblingsAcrossSubtypes:
    def test_children_found_across_subtypes(self, mixed_tree):
        root = mixed_tree["root"]
        names = list(root.get_children().values_list("name", flat=True))
        assert names == ["mid"]  # a RedirectPage child of a RegularPage

    def test_siblings_found_across_subtypes(self, page_models):
        Page, RegularPage, RedirectPage = page_models
        root = RegularPage(name="root")
        root.save()
        a = RegularPage(name="a", parent=root)
        a.save()
        b = RedirectPage(name="b", parent=root)
        b.save()
        a.refresh_from_db()
        # a's sibling b is a different subtype and must still be found.
        sibling_names = set(a.get_siblings().values_list("name", flat=True))
        assert sibling_names == {"b"}

    def test_is_leaf_sees_other_subtype_children(self, mixed_tree):
        mid = mixed_tree["mid"]  # RedirectPage with a RegularPage child
        assert mid.is_leaf() is False

    def test_get_root_returns_base_across_subtypes(self, mixed_tree):
        leaf = mixed_tree["leaf"]  # RegularPage; root is also RegularPage here
        root = leaf.get_root()
        assert root.name == "root"


@pytest.mark.django_db
class TestInsertAcrossSubtypes:
    """Write-path: order/path computation must scope to the base model.

    Regression for the MTI fix being applied to reads only — the pre_save
    handler counted siblings via ``sender.objects`` (the concrete subtype),
    so a sibling written by a different subtype was not counted, producing
    duplicate ``order`` values and colliding ``path`` strings.
    """

    def test_mixed_subtype_siblings_get_distinct_order(self, page_models):
        Page, RegularPage, RedirectPage = page_models
        root = RegularPage(name="root")
        root.save()
        a = RegularPage(name="a", parent=root)
        a.save()
        b = RedirectPage(name="b", parent=root, target_url="/x")
        b.save()

        a.refresh_from_db()
        b.refresh_from_db()
        # Siblings of different subtypes must occupy distinct order slots.
        assert {a.order, b.order} == {0, 1}

    def test_mixed_subtype_siblings_get_distinct_path(self, page_models):
        Page, RegularPage, RedirectPage = page_models
        root = RegularPage(name="root")
        root.save()
        a = RegularPage(name="a", parent=root)
        a.save()
        b = RedirectPage(name="b", parent=root, target_url="/x")
        b.save()

        a.refresh_from_db()
        b.refresh_from_db()
        assert a.path != b.path
        # Both children sit one level under root, counted across the base table.
        children = Page.objects.filter(parent=root).order_by("order")
        assert list(children.values_list("name", flat=True)) == ["a", "b"]

    def test_mixed_subtype_roots_get_distinct_order(self, page_models):
        Page, RegularPage, RedirectPage = page_models
        r1 = RegularPage(name="r1")
        r1.save()
        r2 = RedirectPage(name="r2", target_url="/x")
        r2.save()

        r1.refresh_from_db()
        r2.refresh_from_db()
        assert {r1.order, r2.order} == {0, 1}
        assert r1.path != r2.path
