"""eBay taxonomy subtree fetch + flatten.

Only used offline: pull the Parts & Accessories subtree once, dump it into
Postgres (conneverse-demo scripts/import-ebay-taxonomy.ts), done. Nothing in
the matching pipeline reads this.

eBay returns a nested `categorySubtreeNode`:

    {"category": {"categoryId": "6028", "categoryName": "Parts & Accessories"},
     "categoryTreeNodeLevel": 2,
     "parentCategoryTreeNodeHref": ".../get_category_subtree?category_id=6000",
     "childCategoryTreeNodes": [...]}          # absent on leaves
                                               # leaves carry leafCategoryTreeNode: true

We flatten it depth-first into rows with parent_id / level / full_path.
`level` is depth relative to the requested root (root = 0), NOT eBay's absolute
`categoryTreeNodeLevel` (which is 2 for 6028).
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any

from .ebay import EbayClient

# eBay Motors has its own category tree; the EBAY_US tree (id 0) has no 6028.
MOTORS_CATEGORY_TREE_ID = "100"

# eBay Motors > Parts & Accessories — excludes whole vehicles and tools.
DEFAULT_ROOT_CATEGORY_ID = 6028

PATH_SEPARATOR = "|"

# Guard against a pathological parent chain; real depth here is 2 hops.
MAX_ANCESTOR_HOPS = 8


def _category_id_from_href(href: str) -> str | None:
    """Pull `category_id` out of a parentCategoryTreeNodeHref."""
    if not href:
        return None
    query = urllib.parse.urlparse(href).query
    values = urllib.parse.parse_qs(query).get("category_id") or []
    return values[0] if values else None


def flatten_subtree(
    node: dict[str, Any],
    *,
    parent_id: int | None,
    ancestor_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Depth-first flatten. `ancestor_names` prefixes full_path above the root."""
    rows: list[dict[str, Any]] = []
    # (node, parent_id, level, path_names) — explicit stack, the tree is 2k nodes.
    stack: list[tuple[dict[str, Any], int | None, int, list[str]]] = [
        (node, parent_id, 0, list(ancestor_names or []))
    ]

    while stack:
        current, current_parent, level, prefix = stack.pop()
        category = current.get("category") or {}
        category_id = category.get("categoryId")
        if category_id is None:
            continue
        name = str(category.get("categoryName") or "")
        path = prefix + [name]
        children = current.get("childCategoryTreeNodes") or []

        rows.append({
            "id": int(category_id),
            "name": name,
            "parent_id": current_parent,
            "level": level,
            "is_leaf": not children,
            "full_path": PATH_SEPARATOR.join(path),
        })

        # reversed() so siblings come out in eBay's original order
        for child in reversed(children):
            stack.append((child, int(category_id), level + 1, path))

    return rows


def _resolve_ancestors(
    client: EbayClient,
    root_node: dict[str, Any],
    *,
    category_tree_id: str,
) -> list[str]:
    """Walk parentCategoryTreeNodeHref upward, collecting ancestor names.

    So 6028's full_path reads "eBay Motors|Parts & Accessories" rather than
    starting bare at the requested root. Costs one extra request per ancestor
    (each returns that ancestor's whole subtree, ~1MB for eBay Motors) — fine
    for a one-shot offline pull. The synthetic tree root (id 0, name "Root")
    is skipped.
    """
    names: list[str] = []
    href = str(root_node.get("parentCategoryTreeNodeHref") or "")

    for _ in range(MAX_ANCESTOR_HOPS):
        ancestor_id = _category_id_from_href(href)
        if ancestor_id in (None, "", "0"):
            break
        payload = client.get_category_subtree(
            category_id=ancestor_id,
            category_tree_id=category_tree_id,
        )
        ancestor_node = payload.get("categorySubtreeNode") or {}
        ancestor_name = str((ancestor_node.get("category") or {}).get("categoryName") or "")
        if not ancestor_name:
            break
        names.append(ancestor_name)
        href = str(ancestor_node.get("parentCategoryTreeNodeHref") or "")

    names.reverse()  # collected child→parent, full_path wants top-down
    return names


def fetch_taxonomy(
    root_category_id: int = DEFAULT_ROOT_CATEGORY_ID,
    *,
    category_tree_id: str = MOTORS_CATEGORY_TREE_ID,
    resolve_ancestors: bool = True,
    client: EbayClient | None = None,
) -> dict[str, Any]:
    """Fetch the subtree under `root_category_id` and return it flat."""
    client = client or EbayClient()
    payload = client.get_category_subtree(
        category_id=root_category_id,
        category_tree_id=category_tree_id,
    )
    root_node = payload.get("categorySubtreeNode") or {}
    if not root_node.get("category"):
        raise ValueError(f"eBay returned no categorySubtreeNode for category {root_category_id}")

    parent_href = str(root_node.get("parentCategoryTreeNodeHref") or "")
    parent_id_raw = _category_id_from_href(parent_href)
    # The requested root keeps its real parent id even though that parent is
    # outside the subtree — the importer nulls dangling refs.
    root_parent_id = int(parent_id_raw) if parent_id_raw not in (None, "", "0") else None

    ancestor_names = (
        _resolve_ancestors(client, root_node, category_tree_id=category_tree_id)
        if resolve_ancestors
        else []
    )

    categories = flatten_subtree(root_node, parent_id=root_parent_id, ancestor_names=ancestor_names)

    return {
        "categories": categories,
        "total": len(categories),
        "root_category_id": root_category_id,
        "category_tree_id": str(payload.get("categoryTreeId") or category_tree_id),
        "category_tree_version": payload.get("categoryTreeVersion"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
