"""
icv-tree factory-boy patterns for consuming projects.

Because TreeNode is abstract, there is no concrete factory here.
Instead, this module provides a base factory class and a mixin that
consuming projects use to create factories for their own TreeNode subclasses.

Usage::

    # In consuming project: factories.py
    import factory
    from icv_tree.testing.factories import TreeNodeFactory

    class PageFactory(TreeNodeFactory):
        class Meta:
            model = Page

        title = factory.Sequence(lambda n: f"Page {n}")

    # Usage in tests:
    root = PageFactory()                         # creates a root node
    child = PageFactory(parent=root)             # creates a child of root
    subtree = PageFactory.create_batch(5, parent=root)  # five children
"""

from __future__ import annotations

import factory


class TreeNodeFactory(factory.django.DjangoModelFactory):
    """Base factory for concrete TreeNode subclasses.

    Consuming projects subclass this and set Meta.model to their concrete model.

    The parent field defaults to None (creates a root node). Pass parent=<instance>
    to create a child node.

    Note:
        path, depth, and order are computed automatically by the pre_save
        handler — do not declare them in subclasses.
    """

    parent = None

    class Meta:
        abstract = True
        exclude = []


class TreeNodeChildFactory(TreeNodeFactory):
    """Factory variant that always creates a child node.

    Consuming projects use this with SubFactory to create child nodes::

        class PageWithParentFactory(TreeNodeChildFactory):
            class Meta:
                model = Page

            title = factory.Sequence(lambda n: f"Child Page {n}")
            parent = factory.SubFactory(PageFactory)
    """

    class Meta:
        abstract = True
