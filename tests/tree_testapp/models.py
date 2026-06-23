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


class Page(TreeNode):
    """Base of a multi-table-inheritance tree (like a CMS Page hierarchy).

    Concrete child models (``RegularPage``, ``RedirectPage``) store their rows
    in separate child tables but share the base ``Page`` table for the tree
    columns. Tree walks must scope to this base table so a node's ancestors and
    descendants are found regardless of which subtype wrote them.
    """

    name = models.CharField(max_length=100)

    # Child rows live in their own tables; skip the startup integrity check.
    check_tree_integrity = False

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_page"
        ordering = ["path"]

    def __str__(self) -> str:
        return self.name


class RegularPage(Page):
    """Concrete MTI child of Page."""

    body = models.TextField(blank=True, default="")

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_regularpage"


class RedirectPage(Page):
    """Second concrete MTI child of Page, stored in a sibling table."""

    target_url = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_redirectpage"


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
