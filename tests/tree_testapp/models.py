"""Concrete test model for icv-tree package tests."""

from __future__ import annotations

import uuid

from django.db import models

from icv_tree.models import TreeNode


class UUIDTree(TreeNode):
    """TreeNode subclass with a UUID primary key.

    Regression fixture for the sqlite UUID-bind bug (icvoss/django-icv-tree#2):
    ``_reorder_siblings_after_removal`` passed ``parent_id`` (a ``uuid.UUID``)
    straight to a raw ``cursor.execute``, which SQLite rejects. Deleting a
    non-root node exercises that path.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_uuidtree"
        ordering = ["path"]

    def __str__(self) -> str:
        return self.name


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

    # Opted out so this family stays out of the check fixtures, which count
    # warnings across every installed TreeNode subclass. Not because MTI needs
    # the opt-out: since icvoss/django-icv-tree#50, W001 resolves the model
    # owning the path column, so an MTI child needs no opt-out and no
    # constraint of its own (see TestCheckPathUniquenessUnderMTI).
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


class UnrelatedModel(models.Model):
    """Plain, cascade-free model unrelated to icv-tree.

    Deliberately has no incoming ForeignKey from any model in this test app
    and is not itself a TreeNode subclass, so it is the fixture for proving
    Collector.can_fast_delete() returns True for it once icv-tree's
    pre_save/post_delete handlers are connected per sender rather than
    bare (icvoss/django-icv-tree#24). Scope cannot be used for this: it has
    an incoming FK from ScopedTree, so it is never fast-deletable on its
    own relations alone regardless of any signal wiring.
    """

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tree_testapp"
        db_table = "tree_testapp_unrelatedmodel"

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
