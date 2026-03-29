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


class Scope(models.Model):
    """Simple scope model (analogous to Vocabulary) for testing tree_scope_field."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_scope"

    def __str__(self) -> str:
        return self.name


class ScopedTree(TreeNode):
    """TreeNode subclass that scopes paths by a FK, like Term scopes by Vocabulary."""

    tree_scope_field = "scope"

    name = models.CharField(max_length=100)
    scope = models.ForeignKey(
        Scope,
        on_delete=models.CASCADE,
        related_name="nodes",
    )

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_scopedtree"
        ordering = ["path"]
        unique_together = [("scope", "path")]

    def __str__(self) -> str:
        return self.name
