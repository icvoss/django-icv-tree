# django-icv-tree

Materialised-path tree structures for Django.

One abstract model, one manager, one queryset. Every traversal method returns a
lazy `QuerySet` — no Python list coercions, no surprise N+1 queries. Configurable
path format, async-safe, zero tenancy coupling.

Replaces **django-mptt**, **django-treebeard** (materialized path), and
**django-polymorphic-tree** with a simpler, single-file API.

```
pip install django-icv-tree
```

## Quick start

```python
# models.py
from django.db import models
from icv_tree.models import TreeNode

class Category(TreeNode):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name
```

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "icv_tree",
    "myapp",
]
```

```bash
python manage.py makemigrations myapp
python manage.py migrate
```

```python
root = Category(name="Electronics", parent=None)
root.save()                               # path="0001", depth=0, order=0

phones = Category(name="Phones", parent=root)
phones.save()                             # path="0001/0001", depth=1, order=0

cases = Category(name="Cases", parent=phones)
cases.save()                              # path="0001/0001/0001", depth=2, order=0
```

Path, depth, and order are computed automatically on save — you never set them
manually.

## Traversal

Every method returns a lazy `QuerySet` that you can filter, slice, and chain:

```python
# Instance methods
node.get_ancestors()              # root -> ... -> parent, ordered by depth
node.get_ancestors(include_self=True)
node.get_descendants()            # depth-first, ordered by path
node.get_descendants(include_self=True)
node.get_children()               # direct children, ordered by sibling order
node.get_siblings()               # same parent, excluding self
node.get_siblings(include_self=True)
node.get_root()                   # root of this node's tree
node.get_descendant_count()       # COUNT query
node.is_root()                    # bool, no DB hit
node.is_leaf()                    # bool, EXISTS query
```

### Manager and QuerySet methods

The same traversal is available on the manager and as chainable queryset filters:

```python
# Manager
Category.objects.roots()                    # all root nodes
Category.objects.at_depth(2)                # all nodes at depth 2
Category.objects.ancestors_of(node)
Category.objects.descendants_of(node)
Category.objects.children_of(node)
Category.objects.siblings_of(node)

# QuerySet — chain with any Django filter
Category.objects.descendants_of(node).filter(is_active=True)
Category.objects.with_tree_fields()         # annotates is_root, child_count
```

## Moving nodes

```python
from icv_tree.services import move_to

move_to(node, target, position="last-child")
# or
node.move_to(target, position="first-child")
```

Positions: `first-child`, `last-child`, `left`, `right`.

Moves are atomic (`transaction.atomic`), recompute paths for the entire subtree,
and reorder siblings at both source and destination. A `node_moved` signal is
emitted after commit.

Cycle detection prevents moving a node under its own descendant.

## Rebuilding

If paths get out of sync (bulk imports, raw SQL, migrations), rebuild from the
parent FK adjacency list:

```python
Category.objects.rebuild()
# or
python manage.py icv_tree_rebuild --model=myapp.Category
```

Options:
- `--dry-run` — report what would change without writing
- `--check` — run integrity checks only, exit 1 if issues found

On PostgreSQL with `ICV_TREE_ENABLE_CTE = True`, rebuild uses a recursive CTE
for better performance on large trees.

## Integrity checks

```python
from icv_tree.services import check_tree_integrity

result = check_tree_integrity(Category)
# {
#     "orphaned_nodes": [],
#     "depth_mismatches": [],
#     "path_prefix_violations": [],
#     "duplicate_paths": [],
#     "total_issues": 0,
# }
```

Django system checks run automatically at startup:
- `icv_tree.E001` — orphaned nodes (parent references missing row)
- `icv_tree.E002` — path inconsistencies (depth mismatch, prefix violation, duplicates)

Models can opt out with `check_tree_integrity = False` on the class.

## Signals

```python
from icv_tree.signals import node_moved, tree_rebuilt

@receiver(node_moved)
def on_move(sender, instance, old_parent, new_parent, old_path, **kwargs):
    # Invalidate cache, re-index search, etc.
    pass

@receiver(tree_rebuilt)
def on_rebuild(sender, nodes_updated, nodes_unchanged, **kwargs):
    pass
```

Both signals fire after the transaction commits.

## Admin

```python
from django.contrib import admin
from icv_tree.admin import TreeAdmin

@admin.register(Category)
class CategoryAdmin(TreeAdmin, admin.ModelAdmin):
    list_display = ["name"]
```

`TreeAdmin` provides:
- Indented list display proportional to node depth
- Read-only path, depth, and order fields
- Drag-drop reordering endpoint (`POST <pk>/tree-move/`)

## Template tags

```html
{% load icv_tree %}

<!-- Recursive tree rendering -->
{% recurse_tree root_nodes %}
    <li>
        {{ node.name }}
        {% if children %}
        <ul>
            {% recurse_tree children %}
                <li>{{ node.name }}</li>
            {% end_recurse_tree %}
        </ul>
        {% endif %}
    </li>
{% end_recurse_tree %}

<!-- Breadcrumbs -->
{% tree_breadcrumbs node as crumbs %}
{% for crumb in crumbs %}
    <a href="{{ crumb.get_absolute_url }}">{{ crumb }}</a>
{% endfor %}

<!-- Filter: is_ancestor_of -->
{% if node|is_ancestor_of:current_node %}active{% endif %}
```

## Migration operation

For optimal prefix-query performance, add a `PathIndex` in your migration:

```python
from icv_tree.operations import PathIndex

class Migration(migrations.Migration):
    operations = [
        migrations.CreateModel(name="Category", fields=[...]),
        PathIndex(model_name="category", field_name="path"),
    ]
```

On PostgreSQL this creates a `text_pattern_ops` index for efficient
`LIKE 'path/%'` queries. On other databases it creates a standard B-tree index.

## Testing utilities

### Factory base classes

```python
# myapp/factories.py
import factory
from icv_tree.testing.factories import TreeNodeFactory

class CategoryFactory(TreeNodeFactory):
    class Meta:
        model = Category

    name = factory.Sequence(lambda n: f"Category {n}")

# Usage
root = CategoryFactory()
child = CategoryFactory(parent=root)
```

### Test mixin

```python
from icv_tree.testing import TreeTestMixin

class TestCategoryTree(TreeTestMixin, TestCase):

    def test_tree_is_valid(self):
        self.assert_tree_valid(Category)

    def test_ancestry(self):
        self.assert_is_ancestor_of(root, child)
        self.assert_is_descendant_of(child, root)

    def test_build_tree(self):
        nodes = self.create_tree_structure(Category, {
            "Electronics": {
                "Phones": {"Cases": {}},
                "Laptops": {},
            },
        })
        assert nodes["Cases"].depth == 2
```

### pytest fixture

```python
# conftest.py
from icv_tree.testing.fixtures import tree_integrity_checker  # noqa: F401

# tests
def test_my_tree(tree_integrity_checker):
    # ... build tree ...
    tree_integrity_checker(Category)
```

## Settings

All settings use the `ICV_TREE_*` prefix and have sensible defaults:

| Setting | Default | Description |
|---|---|---|
| `ICV_TREE_PATH_SEPARATOR` | `"/"` | Single character separating path segments. Must not be a digit. |
| `ICV_TREE_STEP_LENGTH` | `4` | Digits per path segment. 4 supports up to 9,999 siblings. Range: 1-10. |
| `ICV_TREE_MAX_PATH_LENGTH` | `255` | Max `CharField` length. With defaults: 51 levels deep. |
| `ICV_TREE_ENABLE_CTE` | `False` | Use PostgreSQL recursive CTE for rebuild. No effect on other databases. |
| `ICV_TREE_REBUILD_BATCH_SIZE` | `1000` | Nodes per `bulk_update` batch during rebuild. |
| `ICV_TREE_CHECK_ON_SAVE` | `False` | Run path validation on every save. Development only. |

**Warning:** Changing `ICV_TREE_PATH_SEPARATOR` or `ICV_TREE_STEP_LENGTH` after
data exists will invalidate all stored paths. Run `rebuild()` after changing.

## Requirements

- Python 3.11+
- Django 4.2, 5.0, or 5.1

Optional: `factory-boy` for `TreeNodeFactory`.

## Licence

MIT
