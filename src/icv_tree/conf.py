"""
icv-tree package settings.

All settings use the ICV_TREE_* prefix and are evaluated at call time via
get_setting() to respect pytest settings fixture overrides. Never import these
module-level constants into other modules — always call get_setting() inside
function bodies.
"""

from __future__ import annotations

from django.conf import settings


def get_setting(name: str, default):  # type: ignore[no-untyped-def]
    """Return the named ICV_TREE_* setting, falling back to default."""
    return getattr(settings, name, default)


# ------------------------------------------------------------------
# Path format
# ------------------------------------------------------------------

# Single character used to separate path segments.
# Must not be a digit (0-9). Changing this after migration breaks all paths.
ICV_TREE_PATH_SEPARATOR: str = getattr(settings, "ICV_TREE_PATH_SEPARATOR", "/")

# Number of digits per path segment. 4 supports up to 9,999 siblings per node.
# Valid range: 1–10. Changing this after migration breaks all paths.
ICV_TREE_STEP_LENGTH: int = getattr(settings, "ICV_TREE_STEP_LENGTH", 4)

# Maximum CharField length for the path field. Determines maximum tree depth:
#   floor(max_path_length / (step_length + len(separator)))
# With defaults (4 + 1 = 5): floor(255 / 5) = 51 levels.
ICV_TREE_MAX_PATH_LENGTH: int = getattr(settings, "ICV_TREE_MAX_PATH_LENGTH", 255)

# ------------------------------------------------------------------
# Optimisations
# ------------------------------------------------------------------

# Enable PostgreSQL recursive CTE optimisations for ancestor queries and
# rebuild operations. Requires PostgreSQL 9.4+. Has no effect on other databases.
ICV_TREE_ENABLE_CTE: bool = getattr(settings, "ICV_TREE_ENABLE_CTE", False)

# Number of nodes updated per batch during rebuild(). Reduces memory usage for
# very large trees.
ICV_TREE_REBUILD_BATCH_SIZE: int = getattr(settings, "ICV_TREE_REBUILD_BATCH_SIZE", 1000)

# ------------------------------------------------------------------
# Development / debugging
# ------------------------------------------------------------------

# If True, run path validation on every TreeNode.save(). Adds a small overhead
# per save. Recommended for development only.
ICV_TREE_CHECK_ON_SAVE: bool = getattr(settings, "ICV_TREE_CHECK_ON_SAVE", False)
