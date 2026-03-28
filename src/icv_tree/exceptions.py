"""Exceptions for icv-tree."""

from __future__ import annotations


class TreeStructureError(Exception):
    """Raised when a tree operation would violate structural invariants.

    Examples:
        - move_to() called with an invalid position value
        - move_to() called with a target that is a descendant of the node
          being moved (would create a cycle)
    """
