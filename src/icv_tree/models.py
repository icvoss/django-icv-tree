"""
icv-tree models.

Provides:
  TreeNode     — abstract model; subclass to add materialised path tree behaviour
  TreeManager  — default manager; exposes roots(), at_depth(), rebuild()
  TreeQuerySet — chainable queryset with tree-aware filter methods
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models
from django.db.models import BooleanField, Case, Count, Value, When
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    pass


class TreeQuerySet(models.QuerySet):
    """Chainable queryset with tree-aware filtering methods.

    All methods return a TreeQuerySet so additional filters may be applied.
    """

    def ancestors_of(
        self,
        node: TreeNode,
        include_self: bool = False,
    ) -> TreeQuerySet:
        """Filter to nodes that are ancestors of the given node.

        Args:
            node: The reference node.
            include_self: If True, include the node itself.

        Returns:
            TreeQuerySet filtered to ancestor paths, ordered by depth ascending.
        """
        from .conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        parts = node.path.split(separator)
        # Build ancestor path strings: ["0001"], ["0001", "0002"] -> "0001/0002", etc.
        ancestor_paths = [separator.join(parts[: i + 1]) for i in range(len(parts) - 1)]
        if include_self:
            ancestor_paths.append(node.path)
        if not ancestor_paths:
            return self.none()
        return self.filter(path__in=ancestor_paths).order_by("depth")

    def descendants_of(
        self,
        node: TreeNode,
        include_self: bool = False,
    ) -> TreeQuerySet:
        """Filter to nodes that are descendants of the given node.

        Args:
            node: The reference node.
            include_self: If True, include the node itself.

        Returns:
            TreeQuerySet filtered by path prefix, ordered by path (depth-first).
        """
        from .conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        qs = self.filter(path__startswith=node.path + separator)
        if include_self:
            qs = qs | self.filter(pk=node.pk)
        return qs.order_by("path")

    def children_of(self, node: TreeNode) -> TreeQuerySet:
        """Filter to direct children of the given node.

        Returns:
            TreeQuerySet filtered to parent=node, ordered by order ascending.
        """
        return self.filter(parent=node).order_by("order")

    def siblings_of(
        self,
        node: TreeNode,
        include_self: bool = False,
    ) -> TreeQuerySet:
        """Filter to sibling nodes of the given node (same parent).

        Returns:
            TreeQuerySet filtered to parent=node.parent, optionally excluding node.
        """
        qs = self.filter(parent=node.parent_id)
        if not include_self:
            qs = qs.exclude(pk=node.pk)
        return qs.order_by("order")

    def with_tree_fields(self) -> TreeQuerySet:
        """Annotate the queryset with commonly used computed tree fields.

        Adds annotations:
          - is_root: True if parent_id IS NULL
          - child_count: number of direct children

        Returns:
            Annotated TreeQuerySet.
        """
        # Use Count on the "children" reverse relation with distinct=True to avoid
        # fan-out on multi-valued JOIN paths. This generates a single GROUP BY on
        # the outer query's PK, which is safe.
        is_root_expr = Case(
            When(parent_id__isnull=True, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
        return self.annotate(
            is_root=is_root_expr,
            child_count=Count("children", distinct=True),
        )


class TreeManager(models.Manager):
    """Default manager for TreeNode subclasses."""

    def get_queryset(self) -> TreeQuerySet:
        """Return a TreeQuerySet instance for all objects."""
        return TreeQuerySet(self.model, using=self._db)

    def roots(self) -> TreeQuerySet:
        """Return a QuerySet of all root nodes (depth=0, parent=None).

        Returns:
            TreeQuerySet filtered to parent=None, ordered by order ascending.
        """
        return self.get_queryset().filter(parent__isnull=True).order_by("order")

    def at_depth(self, depth: int) -> TreeQuerySet:
        """Return a QuerySet of all nodes at the specified depth level.

        Args:
            depth: Zero-based depth level (0 = roots, 1 = children of roots, etc.)

        Returns:
            TreeQuerySet filtered to depth=depth, ordered by path.
        """
        return self.get_queryset().filter(depth=depth).order_by("path")

    def rebuild(self) -> dict:
        """Rebuild all path, depth, and order values from the parent FK adjacency list.

        Returns:
            Dict with keys:
              - nodes_updated: int
              - nodes_unchanged: int

        Side effects:
            Emits tree_rebuilt signal after commit.
        """
        from .services import rebuild

        return rebuild(self.model)

    # Expose TreeQuerySet methods directly on the manager for convenience.

    def ancestors_of(
        self,
        node: TreeNode,
        include_self: bool = False,
    ) -> TreeQuerySet:
        """Delegate to TreeQuerySet.ancestors_of()."""
        return self.get_queryset().ancestors_of(node, include_self=include_self)

    def descendants_of(
        self,
        node: TreeNode,
        include_self: bool = False,
    ) -> TreeQuerySet:
        """Delegate to TreeQuerySet.descendants_of()."""
        return self.get_queryset().descendants_of(node, include_self=include_self)

    def children_of(self, node: TreeNode) -> TreeQuerySet:
        """Delegate to TreeQuerySet.children_of()."""
        return self.get_queryset().children_of(node)

    def siblings_of(
        self,
        node: TreeNode,
        include_self: bool = False,
    ) -> TreeQuerySet:
        """Delegate to TreeQuerySet.siblings_of()."""
        return self.get_queryset().siblings_of(node, include_self=include_self)

    def with_tree_fields(self) -> TreeQuerySet:
        """Delegate to TreeQuerySet.with_tree_fields()."""
        return self.get_queryset().with_tree_fields()


class TreeNode(models.Model):
    """Abstract model providing materialised path tree behaviour.

    Subclass this to add tree structure to any concrete model:

        class Page(TreeNode):
            title = models.CharField(max_length=255)

    Or combine with icv_core.BaseModel for UUID PK and timestamps:

        class Page(BaseModel, TreeNode):
            title = models.CharField(max_length=255)

    Fields managed by icv-tree (do not set manually):
      parent — adjacency list FK; the canonical source of truth
      path   — materialised path string; computed from parent FK
      depth  — zero-based depth (derived from path)
      order  — zero-based sibling order (determines path step value)

    To move a node, call node.move_to(target, position). Setting parent
    directly and calling save() will trigger the pre_save handler which
    delegates to the move_to service.
    """

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        db_index=True,
        verbose_name=_("parent"),
        help_text=_("Parent node. Null for root nodes."),
    )
    path = models.CharField(
        max_length=255,
        db_index=True,
        editable=False,
        unique=True,
        verbose_name=_("path"),
        help_text=_("Materialised path string (e.g. '0001/0002/0003'). Managed by icv-tree — do not edit directly."),
    )
    depth = models.PositiveIntegerField(
        default=0,
        editable=False,
        db_index=True,
        verbose_name=_("depth"),
        help_text=_("Zero-based depth in the tree. Root nodes have depth=0."),
    )
    order = models.PositiveIntegerField(
        default=0,
        editable=False,
        db_index=True,
        verbose_name=_("order"),
        help_text=_("Zero-based sibling ordering index within the parent. Used to compute path step values."),
    )

    objects = TreeManager()

    class Meta:
        abstract = True
        ordering = ["path"]

    # ------------------------------------------------------------------
    # Traversal instance methods
    # ------------------------------------------------------------------

    def get_ancestors(self, include_self: bool = False) -> models.QuerySet:
        """Return a QuerySet of all ancestor nodes, ordered root-first.

        Args:
            include_self: If True, include this node as the last result.

        Returns:
            QuerySet of the same concrete model type, ordered by depth ascending.
        """
        from .conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        parts = self.path.split(separator)
        ancestor_paths = [separator.join(parts[: i + 1]) for i in range(len(parts) - 1)]
        if include_self:
            ancestor_paths.append(self.path)
        if not ancestor_paths:
            return self.__class__.objects.none()
        return self.__class__.objects.filter(path__in=ancestor_paths).order_by("depth")

    def get_descendants(self, include_self: bool = False) -> models.QuerySet:
        """Return a QuerySet of all descendant nodes, ordered depth-first.

        Args:
            include_self: If True, include this node as the first result.

        Returns:
            QuerySet of the same concrete model type, ordered by path.
        """
        from .conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        qs = self.__class__.objects.filter(path__startswith=self.path + separator)
        if include_self:
            qs = qs | self.__class__.objects.filter(pk=self.pk)
        return qs.order_by("path")

    def get_children(self) -> models.QuerySet:
        """Return a QuerySet of direct children, ordered by order field.

        Returns:
            QuerySet of the same concrete model type, ordered by order ascending.
        """
        return self.__class__.objects.filter(parent=self).order_by("order")

    def get_siblings(self, include_self: bool = False) -> models.QuerySet:
        """Return a QuerySet of sibling nodes (same parent).

        Args:
            include_self: If True, include this node in the result.

        Returns:
            QuerySet of the same concrete model type, ordered by order ascending.
        """
        qs = self.__class__.objects.filter(parent_id=self.parent_id)
        if not include_self:
            qs = qs.exclude(pk=self.pk)
        return qs.order_by("order")

    def get_root(self) -> TreeNode:
        """Return the root node of the tree this node belongs to.

        Returns:
            Instance of the same concrete model type. Returns self if already root.

        Side effects:
            One DB query if not already root; zero queries if already root.
        """
        if self.depth == 0:
            return self
        from .conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        root_path = self.path.split(separator)[0]
        return self.__class__.objects.get(path=root_path)

    def is_root(self) -> bool:
        """Return True if this node has no parent (no DB query required)."""
        return self.parent_id is None

    def is_leaf(self) -> bool:
        """Return True if this node has no children.

        Side effects:
            One DB EXISTS query.
        """
        return not self.__class__.objects.filter(parent=self).exists()

    def get_descendant_count(self) -> int:
        """Return the total number of descendant nodes.

        Side effects:
            One DB COUNT query.
        """
        from .conf import get_setting

        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        return self.__class__.objects.filter(path__startswith=self.path + separator).count()

    def move_to(
        self,
        target: TreeNode,
        position: str = "last-child",
    ) -> None:
        """Move this node (and its entire subtree) to a new position in the tree.

        Args:
            target: The reference node. Interpretation depends on position.
            position: One of 'first-child', 'last-child', 'left', 'right'.

        Raises:
            TreeStructureError: If position is not one of the four valid values.
            TreeStructureError: If target is self or a descendant of self (cycle).
        """
        from .services import move_to

        move_to(self, target, position)
