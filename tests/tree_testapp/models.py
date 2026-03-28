"""Concrete test model for icv-tree package tests."""

from __future__ import annotations

from django.db import models

from icv_tree.models import TreeNode


class SimpleTree(TreeNode):
    """Minimal concrete TreeNode subclass used in all icv-tree tests."""

    name = models.CharField(
        max_length=100,
        verbose_name="name",
    )

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_simpletree"
        ordering = ["path"]

    def __str__(self) -> str:
        return self.name


class OptOutTree(TreeNode):
    """TreeNode subclass that opts out of system check integrity scans."""

    name = models.CharField(max_length=100)

    # Opt out of startup integrity check (BR-TREE-043).
    check_tree_integrity = False

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_optouttree"
        ordering = ["path"]

    def __str__(self) -> str:
        return self.name
