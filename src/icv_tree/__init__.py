"""icv-tree — Materialised path tree structures for Django."""

__version__ = "0.2.0"

default_app_config = "icv_tree.apps.IcvTreeConfig"

from icv_tree.handlers import skip_tree_signals  # noqa: E402

__all__ = ["skip_tree_signals"]
