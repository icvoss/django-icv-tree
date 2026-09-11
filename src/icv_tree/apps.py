"""AppConfig for icv-tree."""

from __future__ import annotations

from django.apps import AppConfig
from django.core.checks import Tags, register
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _


class IcvTreeConfig(AppConfig):
    name = "icv_tree"
    label = "icv_tree"
    verbose_name = _("ICV Tree")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Connect signal handlers and validate settings at startup."""
        from . import handlers
        from .conf import get_setting

        # Connect the pre_save/post_delete handlers, per sender, for every
        # concrete TreeNode subclass registered so far (see
        # icvoss/django-icv-tree#24: bare pre_save/post_delete registration
        # disabled Django's fast-delete path for every model in a consuming
        # project, not just TreeNode subclasses).
        handlers._connect_tree_handlers()

        self._validate_settings(get_setting)

        # icv_tree.W001: concrete TreeNode subclasses without a uniqueness
        # constraint on path (icvoss/django-icv-tree#31). Inspects only Meta
        # declarations, so it is cheap enough to run on every check/migrate,
        # unlike check_all_tree_models (E001/E002), which is not registered.
        from .checks import check_path_uniqueness

        register(check_path_uniqueness, Tags.models)

    @staticmethod
    def _validate_settings(get_setting) -> None:  # type: ignore[no-untyped-def]
        """Raise ImproperlyConfigured if any ICV_TREE_* settings are invalid.

        Validated settings (BR-TREE-036, BR-TREE-037, BR-TREE-038):
          ICV_TREE_PATH_SEPARATOR: must be exactly 1 character, not a digit
          ICV_TREE_STEP_LENGTH: must be an int in [1, 10]
          ICV_TREE_REBUILD_BATCH_SIZE: must be an int >= 1
        """
        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)
        rebuild_batch_size = get_setting("ICV_TREE_REBUILD_BATCH_SIZE", 1000)

        if not isinstance(separator, str) or len(separator) != 1:
            raise ImproperlyConfigured(f"ICV_TREE_PATH_SEPARATOR must be a single character string. Got: {separator!r}")

        if separator.isdigit():
            raise ImproperlyConfigured(
                "ICV_TREE_PATH_SEPARATOR must not be a digit (0-9) because path "
                f"steps are numeric strings. Got: {separator!r}"
            )

        if not isinstance(step_length, int) or not (1 <= step_length <= 10):
            raise ImproperlyConfigured(
                f"ICV_TREE_STEP_LENGTH must be an integer between 1 and 10 inclusive. Got: {step_length!r}"
            )

        if not isinstance(rebuild_batch_size, int) or isinstance(rebuild_batch_size, bool) or rebuild_batch_size < 1:
            raise ImproperlyConfigured(
                f"ICV_TREE_REBUILD_BATCH_SIZE must be an integer of at least 1. Got: {rebuild_batch_size!r}"
            )
