"""
icv-tree pytest fixtures for consuming projects.

Import and use these in your conftest.py::

    from icv_tree.testing.fixtures import *  # noqa: F401,F403

Or import individually::

    from icv_tree.testing.fixtures import simple_tree
"""

from __future__ import annotations

import pytest


@pytest.fixture
def tree_integrity_checker():
    """Fixture providing a callable that asserts tree integrity.

    Usage::

        def test_my_tree(tree_integrity_checker):
            # ... build tree ...
            tree_integrity_checker(Page)
    """
    from icv_tree.services import check_tree_integrity

    def _check(model):  # type: ignore[no-untyped-def]
        result = check_tree_integrity(model)
        assert result["total_issues"] == 0, f"Tree integrity failed: {result}"

    return _check
