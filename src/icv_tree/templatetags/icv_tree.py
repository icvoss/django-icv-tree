"""
icv_tree template tags.

{% load icv_tree %}

Tags:
  recurse_tree       — renders a tree structure recursively using a template
  tree_breadcrumbs   — renders the ancestor chain for a node

Filters:
  is_ancestor_of     — returns True if one node is an ancestor of another

Auto-escaping notes:
  - ``recurse_tree`` renders a caller-supplied NodeList. Django's template
    engine applies auto-escaping to all variable output within that NodeList,
    so node content is escaped automatically. No ``mark_safe`` is used here.
  - ``tree_breadcrumbs`` returns a plain Python list of model instances for
    use in a ``{% for %}`` loop. The caller's template variables (e.g.
    ``{{ crumb }}``) are auto-escaped by Django's template engine.
  - ``is_ancestor_of`` returns a boolean; no HTML is produced.
  None of these tags call ``mark_safe``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django import template
from django.template import Context

if TYPE_CHECKING:
    from icv_tree.models import TreeNode

register = template.Library()


class RecurseTreeNode(template.Node):
    """Template node that renders a tree recursively.

    Variables available inside the template:
      node        — the current TreeNode instance
      children    — a QuerySet of the node's direct children
      depth       — the current depth (0 = root)
    """

    def __init__(
        self,
        nodes_var: str,
        nodelist: template.NodeList,
    ) -> None:
        self.nodes_var = template.Variable(nodes_var)
        self.nodelist = nodelist

    def _render_node(
        self,
        context: Context,
        node: TreeNode,
    ) -> str:
        children = node.get_children()
        bits = []

        with context.update(
            {
                "node": node,
                "children": children,
                "depth": node.depth,
            }
        ):
            bits.append(self.nodelist.render(context))

        return "".join(bits)

    def render(self, context: Context) -> str:  # type: ignore[override]
        nodes = self.nodes_var.resolve(context)
        bits = [self._render_node(context, node) for node in nodes]
        return "".join(bits)


@register.tag("recurse_tree")
def do_recurse_tree(
    parser: template.base.Parser,
    token: template.base.Token,
) -> RecurseTreeNode:
    """Render a tree structure recursively.

    Usage::

        {% load icv_tree %}
        {% recurse_tree nodes %}
            <li>
                {{ node }}
                {% if children %}
                <ul>
                    {% recurse_tree children %}
                        <li>{{ node }}</li>
                    {% end_recurse_tree %}
                </ul>
                {% endif %}
            </li>
        {% end_recurse_tree %}

    Args:
        nodes: A QuerySet or iterable of TreeNode instances to render.
    """
    bits = token.split_contents()
    if len(bits) != 2:  # noqa: PLR2004
        raise template.TemplateSyntaxError(f"'{bits[0]}' tag requires exactly one argument: the nodes queryset.")
    nodes_var = bits[1]
    nodelist = parser.parse(("end_recurse_tree",))
    parser.delete_first_token()
    return RecurseTreeNode(nodes_var, nodelist)


@register.simple_tag(takes_context=True)
def tree_breadcrumbs(
    context: Context,
    node: TreeNode,
    include_self: bool = True,
) -> list:
    """Return the ancestor chain for a node as a list (for use in {% for %} loops).

    Usage::

        {% load icv_tree %}
        {% tree_breadcrumbs node as crumbs %}
        {% for crumb in crumbs %}
            <a href="{{ crumb.get_absolute_url }}">{{ crumb }}</a>
        {% endfor %}

    Args:
        node: The TreeNode instance.
        include_self: Whether to include the node itself. Default True.

    Returns:
        List of TreeNode instances ordered from root to node.
    """
    return list(node.get_ancestors(include_self=include_self))


@register.filter
def is_ancestor_of(node: TreeNode, other: TreeNode) -> bool:
    """Return True if node is an ancestor of other.

    Usage::

        {% if node|is_ancestor_of:current_node %}active{% endif %}
    """
    from icv_tree.conf import get_setting

    separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
    return other.path.startswith(node.path + separator)
