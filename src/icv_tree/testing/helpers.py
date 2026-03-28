"""
icv-tree test helpers.

TreeTestMixin — assertion methods and tree construction utilities for
consuming project test suites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from icv_tree.models import TreeNode


class TreeTestMixin:
    """Mixin providing tree-specific assertion methods for test cases.

    Compatible with both unittest.TestCase and pytest classes.

    Usage::

        from icv_tree.testing import TreeTestMixin

        class TestPageTree(TreeTestMixin, TestCase):
            def test_something(self):
                self.assert_tree_valid(Page)
    """

    def assert_tree_valid(self, model: type) -> None:
        """Assert that the tree has no integrity issues.

        Args:
            model: A concrete TreeNode subclass.

        Raises:
            AssertionError: If any orphaned nodes, depth mismatches,
                            path prefix violations, or duplicate paths exist.
        """
        from icv_tree.services import check_tree_integrity

        result = check_tree_integrity(model)
        assert result["total_issues"] == 0, (
            f"Tree integrity check failed for {model.__name__}:\n"
            f"  Orphaned nodes: {result['orphaned_nodes']}\n"
            f"  Depth mismatches: {result['depth_mismatches']}\n"
            f"  Path prefix violations: {result['path_prefix_violations']}\n"
            f"  Duplicate paths: {result['duplicate_paths']}"
        )

    def assert_is_descendant_of(
        self,
        node: TreeNode,
        ancestor: TreeNode,
    ) -> None:
        """Assert that node is a descendant of ancestor.

        Args:
            node: The node to check.
            ancestor: The expected ancestor.

        Raises:
            AssertionError: If node is not a descendant of ancestor.
        """
        from icv_tree.conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        assert node.path.startswith(ancestor.path + separator), (
            f"{node!r} (path={node.path!r}) is not a descendant of {ancestor!r} (path={ancestor.path!r})."
        )

    def assert_is_ancestor_of(
        self,
        node: TreeNode,
        descendant: TreeNode,
    ) -> None:
        """Assert that node is an ancestor of descendant.

        Args:
            node: The expected ancestor.
            descendant: The node to check.

        Raises:
            AssertionError: If node is not an ancestor of descendant.
        """
        from icv_tree.conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        assert descendant.path.startswith(node.path + separator), (
            f"{node!r} (path={node.path!r}) is not an ancestor of {descendant!r} (path={descendant.path!r})."
        )

    def create_tree_structure(
        self,
        model: type,
        structure: dict[str, Any],
        parent: TreeNode | None = None,
        **extra_fields: Any,
    ) -> dict[str, TreeNode]:
        """Build a tree from a nested dict structure.

        Each key is a node name; the value is a nested dict of children
        (empty dict for leaf nodes). Returns a flat dict mapping name -> node.

        Args:
            model: A concrete TreeNode subclass.
            structure: Nested dict defining the tree shape.
                       Keys are node names; values are nested dicts.
            parent: Parent node for the top-level nodes (None = roots).
            **extra_fields: Additional field values passed to model() for every node.
                            The model must have a 'name' field or equivalent.

        Returns:
            Flat dict of {name: TreeNode instance}.

        Example::

            nodes = self.create_tree_structure(Page, {
                'Home': {
                    'About': {},
                    'Blog': {
                        'Post 1': {},
                        'Post 2': {},
                    },
                },
                'Contact': {},
            })
            home = nodes['Home']
            blog = nodes['Blog']
        """
        result: dict[str, TreeNode] = {}
        for name, children in structure.items():
            node = model(parent=parent, **{**extra_fields, "name": name})
            node.save()
            result[name] = node
            if children:
                child_nodes = self.create_tree_structure(model, children, parent=node, **extra_fields)
                result.update(child_nodes)
        return result
