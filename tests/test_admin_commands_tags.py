"""Tests for TreeAdmin mixin, icv_tree_rebuild command, and icv_tree template tags."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.template import Context, Template
from django.test import RequestFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_superuser(username="admin"):
    """Create and return a superuser for admin request simulation."""
    return User.objects.create_superuser(username=username, password="password", email=f"{username}@example.com")


# ---------------------------------------------------------------------------
# Admin — TreeAdmin mixin
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTreeAdminImportable:
    """TreeAdmin is importable and has the expected interface."""

    def test_tree_admin_is_importable(self):
        """TreeAdmin should be importable from icv_tree.admin."""
        from icv_tree.admin import TreeAdmin

        assert TreeAdmin is not None

    def test_tree_admin_has_readonly_fields(self):
        """TreeAdmin declares path, depth, order as readonly_fields."""
        from icv_tree.admin import TreeAdmin

        assert "path" in TreeAdmin.readonly_fields
        assert "depth" in TreeAdmin.readonly_fields
        assert "order" in TreeAdmin.readonly_fields


@pytest.mark.django_db
class TestTreeAdminIndentedTitle:
    """indented_title produces depth-proportional indented output."""

    def _make_admin(self):
        from django.contrib import admin
        from tree_testapp.models import SimpleTree

        from icv_tree.admin import TreeAdmin

        class SimpleTreeAdmin(TreeAdmin, admin.ModelAdmin):
            pass

        return SimpleTreeAdmin(SimpleTree, AdminSite())

    def test_root_node_has_no_indent(self, make_node):
        """Root node (depth=0) should have no leading non-breaking spaces."""
        root = make_node("Root")
        admin_instance = self._make_admin()
        result = admin_instance.indented_title(root)
        # format_html returns a SafeString; the name should be present and
        # there should be no nbsp prefix for depth=0.
        assert "Root" in result
        assert result.startswith("Root")

    def test_child_node_has_single_indent_block(self, make_node):
        """Child node (depth=1) should have one indent block (4 nbsp) before title."""
        root = make_node("Root")
        child = make_node("Child", parent=root)
        assert child.depth == 1
        admin_instance = self._make_admin()
        result = admin_instance.indented_title(child)
        nbsp = "\u00a0\u00a0\u00a0\u00a0"
        assert result.startswith(nbsp)
        assert result.count(nbsp) == 1
        assert "Child" in result

    def test_grandchild_node_has_two_indent_blocks(self, make_node):
        """Grandchild node (depth=2) should have two indent blocks before title."""
        root = make_node("Root")
        child = make_node("Child", parent=root)
        grandchild = make_node("Grandchild", parent=child)
        assert grandchild.depth == 2
        admin_instance = self._make_admin()
        result = admin_instance.indented_title(grandchild)
        nbsp_block = "\u00a0\u00a0\u00a0\u00a0"
        assert result.startswith(nbsp_block * 2)
        assert "Grandchild" in result

    def test_indented_title_short_description(self):
        """indented_title.short_description should be set."""
        from icv_tree.admin import TreeAdmin

        assert hasattr(TreeAdmin.indented_title, "short_description")

    def test_indented_title_admin_order_field(self):
        """indented_title.admin_order_field should be 'path'."""
        from icv_tree.admin import TreeAdmin

        assert TreeAdmin.indented_title.admin_order_field == "path"


@pytest.mark.django_db
class TestTreeAdminGetReadonlyFields:
    """get_readonly_fields always includes path, depth, order."""

    def _make_admin(self):
        from django.contrib import admin
        from tree_testapp.models import SimpleTree

        from icv_tree.admin import TreeAdmin

        class SimpleTreeAdmin(TreeAdmin, admin.ModelAdmin):
            pass

        return SimpleTreeAdmin(SimpleTree, AdminSite())

    def test_get_readonly_fields_includes_path(self):
        """get_readonly_fields must include 'path'."""
        admin_instance = self._make_admin()
        rf = RequestFactory()
        request = rf.get("/admin/")
        request.user = _make_superuser("ro_user_path")
        fields = admin_instance.get_readonly_fields(request)
        assert "path" in fields

    def test_get_readonly_fields_includes_depth(self):
        """get_readonly_fields must include 'depth'."""
        admin_instance = self._make_admin()
        rf = RequestFactory()
        request = rf.get("/admin/")
        request.user = _make_superuser("ro_user_depth")
        fields = admin_instance.get_readonly_fields(request)
        assert "depth" in fields

    def test_get_readonly_fields_includes_order(self):
        """get_readonly_fields must include 'order'."""
        admin_instance = self._make_admin()
        rf = RequestFactory()
        request = rf.get("/admin/")
        request.user = _make_superuser("ro_user_order")
        fields = admin_instance.get_readonly_fields(request)
        assert "order" in fields

    def test_get_readonly_fields_does_not_duplicate(self):
        """Calling get_readonly_fields twice must not produce duplicates."""
        admin_instance = self._make_admin()
        rf = RequestFactory()
        request = rf.get("/admin/")
        request.user = _make_superuser("ro_user_dedup")
        fields = admin_instance.get_readonly_fields(request)
        assert len(fields) == len(set(fields))

    def test_get_readonly_fields_preserves_subclass_fields(self):
        """Extra readonly_fields declared on the subclass are preserved."""
        from django.contrib import admin
        from tree_testapp.models import SimpleTree

        from icv_tree.admin import TreeAdmin

        class ExtendedAdmin(TreeAdmin, admin.ModelAdmin):
            readonly_fields = ("name",)

        instance = ExtendedAdmin(SimpleTree, AdminSite())
        rf = RequestFactory()
        request = rf.get("/admin/")
        request.user = _make_superuser("ro_user_extra")
        fields = instance.get_readonly_fields(request)
        assert "name" in fields
        assert "path" in fields
        assert "depth" in fields
        assert "order" in fields


# ---------------------------------------------------------------------------
# Management command — icv_tree_rebuild
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIcvTreeRebuildCommand:
    """icv_tree_rebuild management command."""

    def test_rebuild_runs_successfully(self, tree_nodes):
        """Rebuild with a valid model path should complete without error."""
        call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", verbosity=0)

    def test_rebuild_outputs_summary(self, tree_nodes, capsys):
        """Rebuild with verbosity=1 should write a success summary to stdout."""
        call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", verbosity=1)
        captured = capsys.readouterr()
        assert "SimpleTree" in captured.out

    def test_rebuild_returns_consistent_tree(self, make_node):
        """After rebuild, all paths should be consistent with parent FK."""
        root = make_node("r")
        child = make_node("c", parent=root)
        grandchild = make_node("gc", parent=child)
        call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", verbosity=0)
        for node in [root, child, grandchild]:
            node.refresh_from_db()
        assert child.path.startswith(root.path + "/")
        assert grandchild.path.startswith(child.path + "/")

    def test_rebuild_invalid_model_format_raises_command_error(self):
        """--model without a dot separator should raise CommandError."""
        with pytest.raises(CommandError, match="app_label.ModelName"):
            call_command("icv_tree_rebuild", model="NoDotsHere", verbosity=0)

    def test_rebuild_unknown_model_raises_command_error(self):
        """--model referencing a non-existent model should raise CommandError."""
        with pytest.raises(CommandError, match="not found"):
            call_command("icv_tree_rebuild", model="tree_testapp.NonExistentModel", verbosity=0)

    def test_rebuild_non_treenode_model_raises_command_error(self):
        """--model referencing a non-TreeNode model should raise CommandError."""
        with pytest.raises(CommandError, match="TreeNode"):
            call_command("icv_tree_rebuild", model="auth.User", verbosity=0)

    def test_rebuild_with_model_flag(self):
        """Command accepts --model flag with app_label.ModelName format."""
        # Smoke test: no exception means the flag is parsed correctly.
        call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", verbosity=0)


@pytest.mark.django_db
class TestIcvTreeRebuildDryRun:
    """--dry-run flag reports without modifying data."""

    def test_dry_run_does_not_alter_paths(self, make_node):
        """--dry-run must not change any stored path values."""

        root = make_node("dr_root")
        child = make_node("dr_child", parent=root)

        path_before = child.path

        call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", dry_run=True, verbosity=0)

        child.refresh_from_db()
        assert child.path == path_before

    def test_dry_run_reports_consistent_tree(self, make_node, capsys):
        """--dry-run on a consistent tree should output a 'no changes' message."""
        make_node("consistent_root")
        call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", dry_run=True, verbosity=1)
        captured = capsys.readouterr()
        # Either "consistent" or "No changes" phrasing is acceptable.
        assert "consistent" in captured.out.lower() or "no change" in captured.out.lower()


@pytest.mark.django_db(transaction=True)
class TestIcvTreeRebuildCheck:
    """--check flag reports integrity issues and exits with code 1 when broken."""

    def test_check_passes_on_clean_tree(self, make_node, capsys):
        """--check on a valid tree should output 'No issues' and not raise."""
        make_node("chk_root")
        # Should complete without raising SystemExit.
        call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", check=True, verbosity=0)
        captured = capsys.readouterr()
        assert "No issues" in captured.out

    def test_check_exits_nonzero_on_broken_tree(self, make_node):
        """--check on a tree with depth mismatches should raise SystemExit(1)."""
        from django.db import connection

        root = make_node("broken_root")
        child = make_node("broken_child", parent=root)

        # Corrupt the depth field directly via SQL to simulate a broken tree.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE tree_testapp_simpletree SET depth = 99 WHERE id = %s",
                [child.pk],
            )

        with pytest.raises(SystemExit) as exc_info:
            call_command("icv_tree_rebuild", model="tree_testapp.SimpleTree", check=True, verbosity=0)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Template tags — recurse_tree, tree_breadcrumbs, is_ancestor_of
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecurseTreeTag:
    """recurse_tree renders tree nodes into a template."""

    def test_recurse_tree_renders_root_nodes(self, make_node):
        """recurse_tree should render each root node at least once."""
        make_node("tag_root1")
        make_node("tag_root2")

        from tree_testapp.models import SimpleTree

        roots = list(SimpleTree.objects.filter(parent__isnull=True))

        template = Template("{% load icv_tree %}{% recurse_tree nodes %}{{ node.name }},{% end_recurse_tree %}")
        context = Context({"nodes": roots})
        output = template.render(context)
        assert "tag_root1" in output
        assert "tag_root2" in output

    def test_recurse_tree_provides_node_variable(self, make_node):
        """Each iteration should expose {{ node }} in the template context."""
        root = make_node("exposed_root")
        template = Template("{% load icv_tree %}{% recurse_tree nodes %}{{ node.name }}{% end_recurse_tree %}")
        context = Context({"nodes": [root]})
        output = template.render(context)
        assert "exposed_root" in output

    def test_recurse_tree_provides_depth_variable(self, make_node):
        """Each iteration should expose {{ depth }} matching the node's depth."""
        root = make_node("depth_root")
        template = Template("{% load icv_tree %}{% recurse_tree nodes %}{{ depth }}{% end_recurse_tree %}")
        context = Context({"nodes": [root]})
        output = template.render(context)
        assert "0" in output

    def test_recurse_tree_provides_children_variable(self, make_node):
        """Each iteration should expose {{ children }} as the child queryset."""
        root = make_node("parent_node")
        make_node("child_node", parent=root)
        template = Template("{% load icv_tree %}{% recurse_tree nodes %}{{ children.count }}{% end_recurse_tree %}")
        context = Context({"nodes": [root]})
        output = template.render(context)
        assert "1" in output

    def test_recurse_tree_empty_queryset_renders_nothing(self):
        """Passing an empty list should render an empty string."""
        template = Template("{% load icv_tree %}{% recurse_tree nodes %}{{ node.name }}{% end_recurse_tree %}")
        context = Context({"nodes": []})
        output = template.render(context)
        assert output == ""

    def test_recurse_tree_requires_one_argument(self):
        """Tag with no argument should raise TemplateSyntaxError."""
        from django.template import TemplateSyntaxError

        with pytest.raises(TemplateSyntaxError):
            Template("{% load icv_tree %}{% recurse_tree %}{{ node }}{% end_recurse_tree %}")


@pytest.mark.django_db
class TestTreeBreadcrumbsTag:
    """tree_breadcrumbs returns the ancestor chain for a node."""

    def test_breadcrumbs_for_root_includes_root(self, make_node):
        """Root node breadcrumbs with include_self=True should contain the root."""
        root = make_node("bc_root")
        template = Template(
            "{% load icv_tree %}{% tree_breadcrumbs node as crumbs %}{% for c in crumbs %}{{ c.name }},{% endfor %}"
        )
        context = Context({"node": root})
        output = template.render(context)
        assert "bc_root" in output

    def test_breadcrumbs_for_grandchild_contains_full_chain(self, make_node):
        """Grandchild breadcrumbs should contain root, child, and grandchild."""
        root = make_node("bc_root2")
        child = make_node("bc_child", parent=root)
        grandchild = make_node("bc_grandchild", parent=child)
        template = Template(
            "{% load icv_tree %}{% tree_breadcrumbs node as crumbs %}{% for c in crumbs %}{{ c.name }},{% endfor %}"
        )
        context = Context({"node": grandchild})
        output = template.render(context)
        assert "bc_root2" in output
        assert "bc_child" in output
        assert "bc_grandchild" in output

    def test_breadcrumbs_ordered_root_first(self, make_node):
        """Ancestor chain must be ordered from root to node (root first)."""
        root = make_node("ord_root")
        child = make_node("ord_child", parent=root)
        grandchild = make_node("ord_grandchild", parent=child)
        template = Template(
            "{% load icv_tree %}{% tree_breadcrumbs node as crumbs %}{% for c in crumbs %}{{ c.name }} {% endfor %}"
        )
        context = Context({"node": grandchild})
        output = template.render(context)
        root_pos = output.index("ord_root")
        child_pos = output.index("ord_child")
        grandchild_pos = output.index("ord_grandchild")
        assert root_pos < child_pos < grandchild_pos

    def test_breadcrumbs_exclude_self_when_false(self, make_node):
        """include_self=False should exclude the node itself from the chain."""
        root = make_node("excl_root")
        child = make_node("excl_child", parent=root)
        template = Template(
            "{% load icv_tree %}"
            "{% tree_breadcrumbs node False as crumbs %}"
            "{% for c in crumbs %}{{ c.name }},{% endfor %}"
        )
        context = Context({"node": child})
        output = template.render(context)
        assert "excl_root" in output
        assert "excl_child" not in output

    def test_breadcrumbs_for_root_without_self_is_empty(self, make_node):
        """Root node breadcrumbs with include_self=False should be empty."""
        root = make_node("lone_root")
        template = Template(
            "{% load icv_tree %}"
            "{% tree_breadcrumbs node False as crumbs %}"
            "{% for c in crumbs %}{{ c.name }},{% endfor %}"
        )
        context = Context({"node": root})
        output = template.render(context)
        assert output.strip() == ""


@pytest.mark.django_db
class TestIsAncestorOfFilter:
    """is_ancestor_of filter returns correct boolean."""

    def test_ancestor_returns_true(self, make_node):
        """An ancestor node should return True for is_ancestor_of its descendant."""
        root = make_node("anc_root")
        child = make_node("anc_child", parent=root)
        template = Template("{% load icv_tree %}{% if root|is_ancestor_of:child %}yes{% else %}no{% endif %}")
        context = Context({"root": root, "child": child})
        assert template.render(context) == "yes"

    def test_non_ancestor_returns_false(self, make_node):
        """A non-ancestor node should return False for is_ancestor_of."""
        root1 = make_node("na_root1")
        root2 = make_node("na_root2")
        template = Template("{% load icv_tree %}{% if root1|is_ancestor_of:root2 %}yes{% else %}no{% endif %}")
        context = Context({"root1": root1, "root2": root2})
        assert template.render(context) == "no"

    def test_node_is_not_ancestor_of_itself(self, make_node):
        """A node must not report itself as its own ancestor."""
        root = make_node("self_root")
        template = Template("{% load icv_tree %}{% if node|is_ancestor_of:node %}yes{% else %}no{% endif %}")
        context = Context({"node": root})
        assert template.render(context) == "no"

    def test_deep_ancestor_returns_true(self, make_node):
        """An ancestor several levels up should still return True."""
        root = make_node("deep_root")
        child = make_node("deep_child", parent=root)
        grandchild = make_node("deep_grandchild", parent=child)
        template = Template("{% load icv_tree %}{% if root|is_ancestor_of:grandchild %}yes{% else %}no{% endif %}")
        context = Context({"root": root, "grandchild": grandchild})
        assert template.render(context) == "yes"

    def test_child_is_not_ancestor_of_parent(self, make_node):
        """A child must not be reported as an ancestor of its parent."""
        root = make_node("rev_root")
        child = make_node("rev_child", parent=root)
        template = Template("{% load icv_tree %}{% if child|is_ancestor_of:root %}yes{% else %}no{% endif %}")
        context = Context({"child": child, "root": root})
        assert template.render(context) == "no"

    def test_is_ancestor_of_direct_call(self, make_node):
        """is_ancestor_of filter can be called directly as a Python function."""
        from icv_tree.templatetags.icv_tree import is_ancestor_of

        root = make_node("direct_root")
        child = make_node("direct_child", parent=root)
        assert is_ancestor_of(root, child) is True
        assert is_ancestor_of(child, root) is False
        assert is_ancestor_of(root, root) is False
