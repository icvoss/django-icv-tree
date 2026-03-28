"""Performance benchmarks for icv-tree.

Measures key operations across realistic tree shapes.
Run with: pytest packages/icv-tree/tests/test_benchmarks.py -v -s

Scenarios modelled on real applications:

  CMS site tree     — 500 pages, 5 sections of ~20 pages each, 3 levels deep
  Product catalogue — 2,000 categories, 10 top-level, branching 5-8, depth 4
  Large catalogue   — 10,000 categories, same shape scaled up
  Deep nav          — 50 levels deep (max supported), narrow chain

Move scenarios reflect actual usage:
  - Drag a page (with 3 children) to a different section
  - Reorder a page within its section (sibling swap)
  - Move a category branch (20 descendants) to a different parent
"""

from __future__ import annotations

import time
from collections import deque
from contextlib import contextmanager

import pytest
from django.db import reset_queries

from icv_tree.services import check_tree_integrity, move_to, rebuild
from icv_tree.services.mutations import _compute_new_path

pytestmark = [
    pytest.mark.slow,
    pytest.mark.django_db(transaction=True),
]

SEP = "/"
STEP = 4


# ── Tree builders ────────────────────────────────────────────


def _build_balanced(model, total: int, max_depth: int = 4) -> list:
    """Build a balanced tree with BFS. Returns list of all nodes."""
    branching = 1
    while branching**max_depth < total:
        branching += 1

    root = model(
        name="root-0",
        parent=None,
        path=_compute_new_path(None, 0, SEP, STEP),
        depth=0,
        order=0,
    )
    root.save()
    nodes = [root]

    queue = deque([root])
    created = 1
    while queue and created < total:
        parent = queue.popleft()
        if parent.depth >= max_depth:
            continue
        batch = []
        for c in range(min(branching, total - created)):
            child = model(
                name=f"node-{created}",
                parent=parent,
                path=_compute_new_path(parent.path, c, SEP, STEP),
                depth=parent.depth + 1,
                order=c,
            )
            batch.append(child)
            created += 1
        model.objects.bulk_create(batch)
        for child in batch:
            child.refresh_from_db()
            nodes.append(child)
            queue.append(child)

    return nodes


def _build_cms_site(model, sections: int = 5, pages_per: int = 20) -> list:
    """Build a realistic CMS site tree.

    Structure:
        Home (root)
        ├── Section 1
        │   ├── Page 1.1
        │   │   ├── Subpage 1.1.1
        │   │   └── Subpage 1.1.2
        │   ├── Page 1.2
        │   ...
        ├── Section 2
        ...
    """
    home = model(
        name="Home",
        parent=None,
        path=_compute_new_path(None, 0, SEP, STEP),
        depth=0,
        order=0,
    )
    home.save()
    nodes = [home]

    for s in range(sections):
        section = model(
            name=f"Section-{s}",
            parent=home,
            path=_compute_new_path(home.path, s, SEP, STEP),
            depth=1,
            order=s,
        )
        section.save()
        nodes.append(section)

        page_batch = []
        for p in range(pages_per):
            page = model(
                name=f"Page-{s}-{p}",
                parent=section,
                path=_compute_new_path(section.path, p, SEP, STEP),
                depth=2,
                order=p,
            )
            page_batch.append(page)
        model.objects.bulk_create(page_batch)

        # Add 2-3 subpages under the first few pages.
        for page in page_batch[:3]:
            page.refresh_from_db()
            nodes.append(page)
            sub_batch = []
            for sp in range(3):
                sub = model(
                    name=f"Sub-{s}-{page.order}-{sp}",
                    parent=page,
                    path=_compute_new_path(page.path, sp, SEP, STEP),
                    depth=3,
                    order=sp,
                )
                sub_batch.append(sub)
            model.objects.bulk_create(sub_batch)
            nodes.extend(sub_batch)

        # Add remaining pages to nodes list.
        for page in page_batch[3:]:
            page.refresh_from_db()
            nodes.append(page)

    return nodes


def _build_deep_chain(model, depth: int = 50) -> list:
    """Build a linear chain to max depth."""
    nodes = []
    parent = None
    for i in range(depth):
        parent_path = parent.path if parent else None
        node = model(
            name=f"level-{i}",
            parent=parent,
            path=_compute_new_path(parent_path, 0, SEP, STEP),
            depth=i,
            order=0,
        )
        node.save()
        nodes.append(node)
        parent = node
    return nodes


# ── Measurement helpers ──────────────────────────────────────


@contextmanager
def timer(label: str, results: dict):
    """Record elapsed time in milliseconds."""
    reset_queries()
    start = time.perf_counter()
    yield
    elapsed_ms = (time.perf_counter() - start) * 1000
    results[label] = elapsed_ms


def _print_results(title: str, results: dict, node_count: int):
    print(f"\n{'=' * 70}")
    print(f"  {title}  ({node_count:,} nodes)")
    print(f"{'=' * 70}")
    for label, ms in results.items():
        print(f"  {label:<45s} {ms:>8.1f} ms")
    print(f"{'=' * 70}\n")


# ── CMS site tree benchmarks ────────────────────────────────


def test_cms_site_tree(simple_tree_model):
    """Realistic CMS: ~150 pages, 5 sections, 3 levels deep."""
    model = simple_tree_model
    model.objects.all().delete()
    results = {}

    with timer("build tree", results):
        _build_cms_site(model)

    count = model.objects.count()
    home = model.objects.get(depth=0)
    sections = list(model.objects.filter(depth=1).order_by("order"))
    section_a = sections[0]
    section_b = sections[1]

    # A page with subpages (depth 2, has children).
    page_with_kids = (
        model.objects.filter(
            parent=section_a,
            depth=2,
        )
        .exclude(
            children=None,  # has at least one child via reverse relation
        )
        .first()
    )
    if page_with_kids is None:
        # Fallback: any depth-2 page under section_a.
        page_with_kids = model.objects.filter(parent=section_a, depth=2).first()

    leaf = model.objects.order_by("-depth", "path").first()

    # ── Read operations ──────────────────────────────────────

    with timer("get_descendants(home) — full site", results):
        list(home.get_descendants())

    with timer("get_descendants(section_a) — one section", results):
        list(section_a.get_descendants())

    with timer("get_ancestors(leaf) — breadcrumb", results):
        list(leaf.get_ancestors())

    with timer("get_children(section_a) — section pages", results):
        list(section_a.get_children())

    with timer("get_siblings(section_a) — top nav", results):
        list(section_a.get_siblings())

    with timer("is_leaf(leaf)", results):
        leaf.is_leaf()

    with timer("get_descendant_count(section_a)", results):
        section_a.get_descendant_count()

    # ── Realistic move: page with 3 kids to different section ─

    page_with_kids.refresh_from_db()
    subtree_size = page_with_kids.get_descendant_count()
    with timer(f"move page+{subtree_size} kids to other section", results):
        move_to(page_with_kids, section_b, "last-child")

    # ── Realistic move: reorder within same section ──────────

    pages_in_b = list(model.objects.filter(parent=section_b, depth=2).order_by("order"))
    if len(pages_in_b) >= 2:
        target_page = pages_in_b[0]
        moving_page = pages_in_b[-1]
        moving_page.refresh_from_db()
        target_page.refresh_from_db()
        with timer("reorder page within section", results):
            move_to(moving_page, target_page, "left")

    # ── Integrity and rebuild ────────────────────────────────

    with timer("check_tree_integrity", results):
        check_tree_integrity(model)

    with timer("rebuild", results):
        rebuild(model)

    _print_results("CMS site tree", results, count)
    model.objects.all().delete()

    assert results["rebuild"] < 5_000


# ── Product catalogue benchmarks ─────────────────────────────


def test_product_catalogue_2k(simple_tree_model):
    """Product catalogue: 2,000 categories, depth 4."""
    model = simple_tree_model
    model.objects.all().delete()
    results = {}

    with timer("build tree", results):
        _build_balanced(model, 2000, max_depth=4)

    count = model.objects.count()
    root = model.objects.get(depth=0)

    # Pick a mid-level category (depth 2) with children.
    mid_cat = model.objects.filter(depth=2).first()
    leaf = model.objects.order_by("-depth").first()

    # Pick a different depth-1 branch to move to.
    branches = list(model.objects.filter(depth=1).order_by("order"))
    source_branch = branches[0]
    dest_branch = branches[-1] if len(branches) > 1 else branches[0]

    # ── Reads ────────────────────────────────────────────────

    with timer("get_descendants(root) — full catalogue", results):
        list(root.get_descendants())

    with timer("get_descendants(branch) — one top category", results):
        list(source_branch.get_descendants())

    with timer("get_ancestors(leaf) — category breadcrumb", results):
        list(leaf.get_ancestors())

    with timer("get_children(mid_cat) — subcategories", results):
        list(mid_cat.get_children())

    # ── Realistic move: category branch to different parent ──

    mid_cat.refresh_from_db()
    subtree_size = mid_cat.get_descendant_count()
    with timer(f"move category+{subtree_size} descendants", results):
        move_to(mid_cat, dest_branch, "last-child")

    # ── Integrity and rebuild ────────────────────────────────

    with timer("check_tree_integrity", results):
        check_tree_integrity(model)

    with timer("rebuild", results):
        rebuild(model)

    _print_results("Product catalogue (2K)", results, count)
    model.objects.all().delete()

    assert results["rebuild"] < 10_000


def test_product_catalogue_10k(simple_tree_model):
    """Large product catalogue: 10,000 categories, depth 4."""
    model = simple_tree_model
    model.objects.all().delete()
    results = {}

    with timer("build tree", results):
        _build_balanced(model, 10000, max_depth=4)

    count = model.objects.count()
    root = model.objects.get(depth=0)

    # Pick a depth-2 category with a manageable subtree.
    mid_cat = model.objects.filter(depth=2).first()
    leaf = model.objects.order_by("-depth").first()
    branches = list(model.objects.filter(depth=1).order_by("order"))
    dest_branch = branches[-1]

    # ── Reads ────────────────────────────────────────────────

    with timer("get_descendants(root) — full catalogue", results):
        list(root.get_descendants())

    with timer("get_descendants(branch) — one top category", results):
        list(branches[0].get_descendants())

    with timer("get_ancestors(leaf)", results):
        list(leaf.get_ancestors())

    with timer("get_children(mid_cat)", results):
        list(mid_cat.get_children())

    # ── Realistic move: category with ~10 descendants ────────

    mid_cat.refresh_from_db()
    subtree_size = mid_cat.get_descendant_count()
    with timer(f"move category+{subtree_size} descendants", results):
        move_to(mid_cat, dest_branch, "last-child")

    # ── Rebuild ──────────────────────────────────────────────

    with timer("check_tree_integrity", results):
        check_tree_integrity(model)

    with timer("rebuild", results):
        rebuild(model)

    _print_results("Product catalogue (10K)", results, count)
    model.objects.all().delete()

    assert results["rebuild"] < 60_000


def test_product_catalogue_100k(simple_tree_model):
    """Very large product catalogue: 100,000 categories, depth 5."""
    model = simple_tree_model
    model.objects.all().delete()
    results = {}

    with timer("build tree", results):
        _build_balanced(model, 100000, max_depth=5)

    count = model.objects.count()
    root = model.objects.get(depth=0)

    # Pick nodes at various depths for realistic queries.
    branches = list(model.objects.filter(depth=1).order_by("order"))
    mid_cat = model.objects.filter(depth=3).first()
    leaf = model.objects.order_by("-depth").first()
    dest_branch = branches[-1]

    # ── Reads ────────────────────────────────────────────────

    with timer("get_descendants(root) — full catalogue", results):
        list(root.get_descendants())

    with timer("get_descendants(branch) — one top category", results):
        list(branches[0].get_descendants())

    # Depth-2 subtree — typical "show this section" query.
    depth2_node = model.objects.filter(depth=2).first()
    with timer("get_descendants(depth2) — mid-section", results):
        list(depth2_node.get_descendants())

    with timer("get_ancestors(leaf) — breadcrumb", results):
        list(leaf.get_ancestors())

    with timer("get_children(mid_cat)", results):
        list(mid_cat.get_children())

    with timer("get_descendant_count(branch)", results):
        branches[0].get_descendant_count()

    # ── Realistic move: mid-level category with descendants ──

    mid_cat.refresh_from_db()
    subtree_size = mid_cat.get_descendant_count()
    with timer(f"move category+{subtree_size} descendants", results):
        move_to(mid_cat, dest_branch, "last-child")

    # ── Smaller move: leaf reorder ───────────────────────────

    siblings = list(model.objects.filter(parent=leaf.parent_id).order_by("order"))
    if len(siblings) >= 2:
        a, b = siblings[0], siblings[-1]
        a.refresh_from_db()
        b.refresh_from_db()
        with timer("reorder leaf within parent", results):
            move_to(b, a, "left")

    # ── Integrity and rebuild ────────────────────────────────

    with timer("check_tree_integrity", results):
        check_tree_integrity(model)

    with timer("rebuild", results):
        rebuild(model)

    _print_results("Product catalogue (100K)", results, count)
    model.objects.all().delete()

    assert results["rebuild"] < 600_000


# ── Deep chain benchmark ─────────────────────────────────────


def test_deep_chain(simple_tree_model):
    """Deep narrow tree: 50 levels, tests ancestor query at max depth."""
    model = simple_tree_model
    model.objects.all().delete()
    results = {}

    with timer("build 50-level chain", results):
        _build_deep_chain(model, 50)

    count = model.objects.count()
    root = model.objects.get(depth=0)
    leaf = model.objects.order_by("-depth").first()
    mid = model.objects.get(depth=25)

    with timer("get_ancestors(leaf) — 50 levels", results):
        list(leaf.get_ancestors())

    with timer("get_descendants(root) — full chain", results):
        list(root.get_descendants())

    with timer("get_ancestors(mid) — 25 levels", results):
        list(mid.get_ancestors())

    with timer("rebuild", results):
        rebuild(model)

    _print_results("Deep chain (50 levels)", results, count)
    model.objects.all().delete()

    assert results["rebuild"] < 5_000
