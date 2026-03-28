"""Signal definitions for icv-tree.

Consuming projects connect to these signals to react to tree restructuring
events (e.g., cache invalidation, search re-indexing).
"""

from __future__ import annotations

from django.dispatch import Signal

# Emitted after a node (and its subtree) has been moved to a new position.
#
# Keyword arguments:
#   sender      — The concrete model class (e.g., Page)
#   instance    — The TreeNode instance after the move (new path/parent set)
#   old_parent  — The parent TreeNode (or None) before the move
#   new_parent  — The parent TreeNode (or None) after the move
#   old_path    — str, the node's path value before the move
node_moved = Signal()

# Emitted after a full rebuild() completes.
#
# Keyword arguments:
#   sender          — The concrete model class (e.g., Page)
#   nodes_updated   — int, count of nodes whose path/depth/order was changed
#   nodes_unchanged — int, count of nodes already consistent
tree_rebuilt = Signal()
