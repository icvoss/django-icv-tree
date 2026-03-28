"""
Custom migration operations for icv-tree.

PathIndex — creates an optimised database index for materialised path prefix
queries. On PostgreSQL uses text_pattern_ops / varchar_pattern_ops for
efficient LIKE 'prefix/%' scans. On other databases falls back to a standard
BTree index.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.operations.base import Operation


class PathIndex(Operation):
    """Add an optimised index on a model's path field.

    On PostgreSQL, creates a BTree index with text_pattern_ops (or
    varchar_pattern_ops for VARCHAR columns) to support efficient LIKE
    'prefix/%' queries used by get_descendants().

    On SQLite, MySQL/MariaDB, and Oracle, creates a standard index.
    The standard index is still beneficial on MySQL/MariaDB for prefix LIKE
    on VARCHAR columns.

    Usage in migrations::

        from icv_tree.operations import PathIndex

        class Migration(migrations.Migration):
            operations = [
                migrations.CreateModel(name='Page', fields=[...]),
                PathIndex(model_name='page', field_name='path'),
            ]

    Args:
        model_name: The lowercased model name (e.g., 'page').
        field_name: The name of the path field (default 'path').
        index_name: Optional custom index name. Auto-generated if not set.
    """

    reversible = True

    def __init__(
        self,
        model_name: str,
        field_name: str = "path",
        index_name: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.field_name = field_name
        self.index_name = index_name or f"{model_name}_{field_name}_prefix_idx"

    @property
    def description(self) -> str:
        return f"Add PathIndex on {self.model_name}.{self.field_name} (text_pattern_ops on PostgreSQL)"

    def state_forwards(self, app_label: str, state) -> None:  # type: ignore[no-untyped-def]
        """No model state change — this is a pure database operation."""

    def database_forwards(
        self,
        app_label: str,
        schema_editor,  # type: ignore[no-untyped-def]
        from_state,
        to_state,
    ) -> None:
        model = to_state.apps.get_model(app_label, self.model_name)
        table_name = model._meta.db_table
        column_name = model._meta.get_field(self.field_name).column

        if connection.vendor == "postgresql":
            opclass = "text_pattern_ops"
            sql = f'CREATE INDEX IF NOT EXISTS "{self.index_name}" ON "{table_name}" ("{column_name}" {opclass});'
        else:
            sql = f'CREATE INDEX IF NOT EXISTS "{self.index_name}" ON "{table_name}" ("{column_name}");'

        schema_editor.execute(sql)

    def database_backwards(
        self,
        app_label: str,
        schema_editor,  # type: ignore[no-untyped-def]
        from_state,
        to_state,
    ) -> None:
        sql = f'DROP INDEX IF EXISTS "{self.index_name}";'
        schema_editor.execute(sql)

    def deconstruct(self):  # type: ignore[no-untyped-def]
        return (
            self.__class__.__qualname__,
            [],
            {
                "model_name": self.model_name,
                "field_name": self.field_name,
                "index_name": self.index_name,
            },
        )
