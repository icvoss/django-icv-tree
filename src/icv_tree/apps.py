"""AppConfig for icv-tree."""

from __future__ import annotations

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _


class IcvTreeConfig(AppConfig):
    name = "icv_tree"
    label = "icv_tree"
    verbose_name = _("ICV Tree")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        """Connect signal handlers and validate settings at startup."""
        from . import (
            checks,  # noqa: F401 — registers system checks
            handlers,  # noqa: F401 — connects pre_save / post_delete handlers
        )
        from .conf import get_setting

        self._validate_settings(get_setting)

    @staticmethod
    def _validate_settings(get_setting) -> None:  # type: ignore[no-untyped-def]
        """Raise ImproperlyConfigured if any ICV_TREE_* settings are invalid.

        Validated settings (BR-TREE-036, BR-TREE-037, BR-TREE-038):
          ICV_TREE_PATH_SEPARATOR — must be exactly 1 character, not a digit
          ICV_TREE_STEP_LENGTH    — must be an int in [1, 10]
        """
        separator = get_setting("ICV_TREE_PATH_SEPARATOR", "/")
        step_length = get_setting("ICV_TREE_STEP_LENGTH", 4)

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
