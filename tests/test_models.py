"""Tests for TreeNode, TreeManager, and TreeQuerySet."""

from __future__ import annotations

import pytest
from django.db import models


@pytest.mark.django_db
class TestTreeNodePathComputation:
    """Test that path, depth, and order are computed correctly on save."""

    def test_create_root_node_sets_path(self, make_node):
        """Root node should receive a valid 4-char path with no separator."""
        node = make_node("root")
        # path is a single zero-padded step (step_length=4).
        assert "/" not in node.path
        assert len(node.path) == 4
        assert node.depth == 0
        # order is consistent with path: int(path) - 1.
        assert node.order == int(node.path) - 1

    def test_create_second_root_gets_incremented_path(self, make_node):
        """Second root node should get a path one higher than the first."""
        first = make_node("first_root")
        second = make_node("second_root")
        assert second.order == first.order + 1
        assert int(second.path) == int(first.path) + 1

    def test_create_child_sets_path_with_parent_prefix(self, make_node):
        """Child node path should start with parent path."""
        root = make_node("root")
        child = make_node("child", parent=root)
        assert child.path == root.path + "/0001"
        assert child.depth == 1
        assert child.order == 0

    def test_depth_matches_path_segments(self, make_node):
        """depth must equal path.count('/')."""
        root = make_node("root")
        child = make_node("child", parent=root)
        grandchild = make_node("grandchild", parent=child)

        assert root.depth == root.path.count("/")
        assert child.depth == child.path.count("/")
        assert grandchild.depth == grandchild.path.count("/")

    def test_root_node_has_no_separator_in_path(self, make_node):
        """Root node path must contain no '/' separator."""
        root = make_node("root")
        assert "/" not in root.path

    def test_path_segments_are_zero_padded(self, make_node):
        """All path segments must be exactly 4 characters (step_length=4)."""
        root = make_node("root")
        child = make_node("child", parent=root)
        for segment in child.path.split("/"):
            assert len(segment) == 4

    def test_order_consistent_with_path_step(self, make_node):
        """order == int(last path segment) - 1."""
        root = make_node("root")
        child1 = make_node("c1", parent=root)
        child2 = make_node("c2", parent=root)
        child3 = make_node("c3", parent=root)

        for child in (child1, child2, child3):
            child.refresh_from_db()
            last_segment = child.path.split("/")[-1]
            assert child.order == int(last_segment) - 1

    def test_sibling_paths_are_sequential(self, make_node):
        """Sibling nodes should have sequential path steps."""
        root = make_node("root")
        c1 = make_node("c1", parent=root)
        c2 = make_node("c2", parent=root)
        c3 = make_node("c3", parent=root)
        assert c1.path == root.path + "/0001"
        assert c2.path == root.path + "/0002"
        assert c3.path == root.path + "/0003"

    def test_grandchild_path_has_three_segments(self, make_node):
        """Grandchild at depth 2 should have path with 2 separators."""
        root = make_node("root")
        child = make_node("child", parent=root)
        grandchild = make_node("grandchild", parent=child)
        assert grandchild.depth == 2
        assert grandchild.path == root.path + "/0001/0001"


@pytest.mark.django_db
class TestTreeNodeTraversalMethods:
    """Test instance traversal methods on TreeNode."""

    def test_get_ancestors_returns_queryset(self, tree_nodes):
        """get_ancestors() must return a QuerySet, not a list."""
        grandchild = tree_nodes["grandchild1"]
        result = grandchild.get_ancestors()
        assert isinstance(result, models.QuerySet)

    def test_get_ancestors_ordered_root_first(self, tree_nodes):
        """get_ancestors() must order by depth ascending (root first)."""
        grandchild = tree_nodes["grandchild1"]
        ancestors = list(grandchild.get_ancestors())
        assert len(ancestors) == 2
        assert ancestors[0].depth == 0  # root
        assert ancestors[1].depth == 1  # child1

    def test_get_ancestors_include_self(self, tree_nodes):
        """get_ancestors(include_self=True) should include the node itself last."""
        grandchild = tree_nodes["grandchild1"]
        ancestors = list(grandchild.get_ancestors(include_self=True))
        assert len(ancestors) == 3
        assert ancestors[-1].pk == grandchild.pk

    def test_get_descendants_returns_queryset(self, tree_nodes):
        """get_descendants() must return a QuerySet."""
        root = tree_nodes["root1"]
        result = root.get_descendants()
        assert isinstance(result, models.QuerySet)

    def test_get_descendants_returns_correct_nodes(self, tree_nodes):
        """get_descendants() should include all nodes below, not siblings."""
        root1 = tree_nodes["root1"]
        descendants = set(root1.get_descendants().values_list("name", flat=True))
        assert descendants == {"child1", "grandchild1", "grandchild2", "child2"}
        assert "root2" not in descendants

    def test_get_descendants_ordered_depth_first(self, tree_nodes):
        """get_descendants() must order by path (depth-first pre-order)."""
        root = tree_nodes["root1"]
        names = list(root.get_descendants().values_list("name", flat=True))
        # Depth-first: child1, grandchild1, grandchild2, child2
        assert names.index("child1") < names.index("grandchild1")
        assert names.index("grandchild1") < names.index("grandchild2")
        assert names.index("grandchild2") < names.index("child2")

    def test_get_descendants_include_self(self, tree_nodes):
        """get_descendants(include_self=True) should include the root."""
        root = tree_nodes["root1"]
        pks = set(root.get_descendants(include_self=True).values_list("pk", flat=True))
        assert root.pk in pks

    def test_get_children_returns_direct_children_only(self, tree_nodes):
        """get_children() should return only direct children, not grandchildren."""
        root = tree_nodes["root1"]
        children = list(root.get_children())
        child_names = {c.name for c in children}
        assert child_names == {"child1", "child2"}
        assert len(children) == 2

    def test_get_children_ordered_by_order(self, tree_nodes):
        """get_children() should order by order field."""
        root = tree_nodes["root1"]
        children = list(root.get_children())
        orders = [c.order for c in children]
        assert orders == sorted(orders)

    def test_get_siblings_excludes_self_by_default(self, tree_nodes):
        """get_siblings() must exclude self by default."""
        child1 = tree_nodes["child1"]
        siblings = list(child1.get_siblings())
        pks = [s.pk for s in siblings]
        assert child1.pk not in pks
        assert len(siblings) == 1

    def test_get_siblings_include_self(self, tree_nodes):
        """get_siblings(include_self=True) must include self."""
        child1 = tree_nodes["child1"]
        siblings = list(child1.get_siblings(include_self=True))
        pks = [s.pk for s in siblings]
        assert child1.pk in pks
        assert len(siblings) == 2

    def test_get_root_returns_self_for_root_node(self, tree_nodes):
        """get_root() should return self when node is already a root."""
        root = tree_nodes["root1"]
        assert root.get_root().pk == root.pk

    def test_get_root_returns_root_for_child(self, tree_nodes):
        """get_root() should return the root ancestor for a non-root node."""
        grandchild = tree_nodes["grandchild1"]
        root = grandchild.get_root()
        assert root.pk == tree_nodes["root1"].pk

    def test_is_root_true_for_root_node(self, tree_nodes):
        """is_root() should return True for root nodes (no DB query)."""
        root = tree_nodes["root1"]
        assert root.is_root() is True

    def test_is_root_false_for_child(self, tree_nodes):
        """is_root() should return False for non-root nodes."""
        child = tree_nodes["child1"]
        assert child.is_root() is False

    def test_is_leaf_true_for_node_without_children(self, tree_nodes):
        """is_leaf() should return True when the node has no children."""
        grandchild = tree_nodes["grandchild1"]
        assert grandchild.is_leaf() is True

    def test_is_leaf_false_for_node_with_children(self, tree_nodes):
        """is_leaf() should return False when the node has children."""
        root = tree_nodes["root1"]
        assert root.is_leaf() is False

    def test_get_descendant_count(self, tree_nodes):
        """get_descendant_count() should return total descendant count."""
        root = tree_nodes["root1"]
        assert root.get_descendant_count() == 4  # child1, grandchild1, grandchild2, child2


@pytest.mark.django_db
class TestTreeManager:
    """Test TreeManager methods."""

    def test_roots_returns_only_root_nodes(self, tree_nodes, simple_tree_model):
        """roots() should return only nodes with no parent."""
        roots = list(simple_tree_model.objects.roots())
        root_names = {r.name for r in roots}
        assert root_names == {"root1", "root2"}

    def test_at_depth_filters_by_depth(self, tree_nodes, simple_tree_model):
        """at_depth(n) should return only nodes at depth n."""
        depth_0 = list(simple_tree_model.objects.at_depth(0))
        depth_1 = list(simple_tree_model.objects.at_depth(1))
        depth_2 = list(simple_tree_model.objects.at_depth(2))

        assert {n.name for n in depth_0} == {"root1", "root2"}
        assert {n.name for n in depth_1} == {"child1", "child2"}
        assert {n.name for n in depth_2} == {"grandchild1", "grandchild2"}


@pytest.mark.django_db
class TestTreeQuerySet:
    """Test TreeQuerySet methods."""

    def test_descendants_of_returns_queryset(self, tree_nodes, simple_tree_model):
        """descendants_of() must return a QuerySet."""
        root = tree_nodes["root1"]
        result = simple_tree_model.objects.descendants_of(root)
        assert isinstance(result, models.QuerySet)

    def test_descendants_of_returns_correct_nodes(self, tree_nodes, simple_tree_model):
        """descendants_of() must include all descendants."""
        root = tree_nodes["root1"]
        names = set(simple_tree_model.objects.descendants_of(root).values_list("name", flat=True))
        assert names == {"child1", "grandchild1", "grandchild2", "child2"}

    def test_ancestors_of_returns_queryset(self, tree_nodes, simple_tree_model):
        """ancestors_of() must return a QuerySet."""
        grandchild = tree_nodes["grandchild1"]
        result = simple_tree_model.objects.ancestors_of(grandchild)
        assert isinstance(result, models.QuerySet)

    def test_ancestors_of_returns_correct_nodes(self, tree_nodes, simple_tree_model):
        """ancestors_of() must return the correct ancestors."""
        grandchild = tree_nodes["grandchild1"]
        names = set(simple_tree_model.objects.ancestors_of(grandchild).values_list("name", flat=True))
        assert names == {"root1", "child1"}

    def test_children_of_returns_direct_children(self, tree_nodes, simple_tree_model):
        """children_of() should return direct children only."""
        root = tree_nodes["root1"]
        children = set(simple_tree_model.objects.children_of(root).values_list("name", flat=True))
        assert children == {"child1", "child2"}

    def test_siblings_of_excludes_node_by_default(self, tree_nodes, simple_tree_model):
        """siblings_of() should exclude the node itself by default."""
        child1 = tree_nodes["child1"]
        siblings = list(simple_tree_model.objects.siblings_of(child1))
        pks = [s.pk for s in siblings]
        assert child1.pk not in pks

    def test_with_tree_fields_annotates_is_root_and_child_count(self, tree_nodes, simple_tree_model):
        """with_tree_fields() should annotate is_root and child_count."""
        qs = simple_tree_model.objects.with_tree_fields().filter(name="root1").first()
        assert qs is not None
        assert hasattr(qs, "is_root")
        assert hasattr(qs, "child_count")

    def test_queryset_methods_are_composable(self, tree_nodes, simple_tree_model):
        """TreeQuerySet methods must be chainable with further filters."""
        root = tree_nodes["root1"]
        qs = simple_tree_model.objects.descendants_of(root).filter(depth=1).order_by("order")
        names = list(qs.values_list("name", flat=True))
        assert "child1" in names
        assert "child2" in names
        assert "grandchild1" not in names

    def test_with_tree_fields_does_not_break_filtering(self, tree_nodes, simple_tree_model):
        """with_tree_fields() must not prevent further filter/order_by calls."""
        root1 = tree_nodes["root1"]
        # Scope to root1's subtree to avoid seeing nodes from other test runs
        # that may have persisted in the shared sandbox DB.
        qs = (
            simple_tree_model.objects.with_tree_fields()
            .filter(depth=1, path__startswith=root1.path + "/")
            .order_by("path")
        )
        assert qs.count() == 2


@pytest.mark.django_db
class TestPathUniqueness:
    """Test path uniqueness behaviour.

    Since icv-tree no longer enforces ``unique=True`` on the abstract
    ``path`` field (to support scoped trees via ``tree_scope_field``),
    concrete models must opt in to uniqueness via their own Meta
    constraints.  ``ScopedTree`` uses ``unique_together`` on
    ``(scope, path)``; models without a scope field should declare their
    own constraint if global uniqueness is desired.
    """

    def test_scoped_path_uniqueness_constraint(self, db):
        """Duplicate paths within the same scope should raise IntegrityError."""
        from django.db import IntegrityError, transaction

        from tree_testapp.models import Scope, ScopedTree

        scope = Scope.objects.create(name="unique-test")
        n1 = ScopedTree.objects.create(name="first", scope=scope)

        n2 = ScopedTree.objects.create(name="second", scope=scope)

        with pytest.raises(IntegrityError), transaction.atomic():
            ScopedTree.objects.filter(pk=n2.pk).update(path=n1.path)

    def test_same_path_allowed_across_scopes(self, db):
        """Duplicate paths in different scopes are allowed."""
        from tree_testapp.models import Scope, ScopedTree

        s1 = Scope.objects.create(name="scope-1")
        s2 = Scope.objects.create(name="scope-2")
        n1 = ScopedTree.objects.create(name="a", scope=s1)
        n2 = ScopedTree.objects.create(name="b", scope=s2)
        n1.refresh_from_db()
        n2.refresh_from_db()
        # Both should have path "0001" — no collision.
        assert n1.path == n2.path == "0001"
