"""
icv-tree admin utilities.

TreeAdmin — mixin for Django admin classes managing TreeNode subclasses.
Provides indented list display, read-only tree fields, and drag-drop
ordering hooks.

Usage::

    from icv_tree.admin import TreeAdmin
    from django.contrib import admin

    @admin.register(Page)
    class PageAdmin(TreeAdmin, admin.ModelAdmin):
        pass
"""

from __future__ import annotations

from django.http import JsonResponse
from django.urls import path
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _


class TreeAdmin:
    """Mixin for admin classes managing TreeNode subclasses.

    Provides:
      - Indented list display proportional to node depth (BR-TREE-047)
      - path, depth, and order in readonly_fields (BR-TREE-050)
      - drag-drop ordering hooks via tree_move_node admin view (BR-TREE-049)

    This is a mixin — compose it with ModelAdmin::

        class PageAdmin(TreeAdmin, admin.ModelAdmin):
            pass
    """

    readonly_fields = ("path", "depth", "order")

    def get_readonly_fields(self, request, obj=None):  # type: ignore[no-untyped-def]
        """Ensure path, depth, and order are always read-only."""
        existing = list(super().get_readonly_fields(request, obj))  # type: ignore[misc]
        for field in ("path", "depth", "order"):
            if field not in existing:
                existing.append(field)
        return existing

    def indented_title(self, obj) -> str:  # type: ignore[no-untyped-def]
        """Display the node's __str__ with depth-proportional indentation.

        Used as a list_display column that replaces the default title column.
        """
        indent = "\u00a0\u00a0\u00a0\u00a0" * obj.depth  # non-breaking spaces
        return format_html("{}{}", indent, str(obj))

    indented_title.short_description = _("Title")  # type: ignore[attr-defined]
    indented_title.admin_order_field = "path"  # type: ignore[attr-defined]

    def get_list_display(self, request):  # type: ignore[no-untyped-def]
        """Prepend indented_title to list_display."""
        existing = list(super().get_list_display(request))  # type: ignore[misc]
        if "indented_title" not in existing:
            existing = ["indented_title"] + [f for f in existing if f != "__str__"]
        return existing

    def get_queryset(self, request):  # type: ignore[no-untyped-def]
        """Order by path (depth-first) in the admin list view."""
        return super().get_queryset(request).order_by("path")  # type: ignore[misc]

    def get_urls(self):  # type: ignore[no-untyped-def]
        """Add tree_move_node URL for drag-drop AJAX endpoint."""
        urls = super().get_urls()  # type: ignore[misc]
        model_info = (
            self.model._meta.app_label,  # type: ignore[attr-defined]
            self.model._meta.model_name,  # type: ignore[attr-defined]
        )
        custom_urls = [
            path(
                "<int:pk>/tree-move/",
                self.admin_site.admin_view(self.tree_move_node),  # type: ignore[attr-defined]
                name=f"{model_info[0]}_{model_info[1]}_tree_move",
            ),
        ]
        return custom_urls + urls

    def tree_move_node(self, request, pk: int):  # type: ignore[no-untyped-def]
        """AJAX endpoint for drag-drop node reordering.

        Expects POST with target_id and position (first-child, last-child,
        left, or right).

        Returns JSON: {"status": "ok", "new_path": "..."} or
                      {"status": "error", "message": "..."}
        """
        if request.method != "POST":
            return JsonResponse({"status": "error", "message": "POST required"}, status=405)

        if not request.user.has_perm(
            f"{self.model._meta.app_label}.change_{self.model._meta.model_name}"  # type: ignore[attr-defined]
        ):
            return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

        from .exceptions import TreeStructureError
        from .services import move_to

        try:
            node = self.model.objects.get(pk=pk)  # type: ignore[attr-defined]
            target_id = request.POST.get("target_id")
            position = request.POST.get("position", "last-child")
            target = self.model.objects.get(pk=target_id)  # type: ignore[attr-defined]
            move_to(node, target, position)
            # Refresh to get updated path.
            node.refresh_from_db()
            return JsonResponse({"status": "ok", "new_path": node.path})
        except self.model.DoesNotExist:  # type: ignore[attr-defined]
            return JsonResponse({"status": "error", "message": "Node not found"}, status=404)
        except TreeStructureError as exc:
            return JsonResponse({"status": "error", "message": str(exc)}, status=400)

    class Media:
        """JavaScript for drag-drop ordering in the admin.

        Consuming projects may override Media to supply their own
        drag-drop library (e.g., SortableJS, jsTree).
        """

        js = ()
