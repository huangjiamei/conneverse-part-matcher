"""
离线校验第 2 档 (compatibility) 和第 3 档 (keyword) 的类目路由:
传入 ebay_category_id 优先, 没传才回退 JSON.

用 fake eBay client, 不碰网络, 不需要凭证:
  python tests/test_category_routing.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from end_to_end_part_matcher.pipeline import PipelineConfig, search_candidates
from end_to_end_part_matcher.utils import get_category_lookup


class FakeEbay:
    """记录每次调用, 按 category_id 决定返不返 item."""

    def __init__(self, *, categories_with_hits: set[str]) -> None:
        self.categories_with_hits = categories_with_hits
        self.compat_calls: list[str] = []
        self.keyword_calls: list[str | None] = []
        self.mpn_calls: list[str | None] = []

    def search_by_part_number(self, *, part_number: str, limit: int, category_id: str | None) -> dict[str, Any]:
        self.mpn_calls.append(category_id)
        # 裸搜 (category_id=None) 视作总有结果 (全站兜底); 带类目时按命中集判定。
        hit = category_id is None or category_id in self.categories_with_hits
        items = [{"itemId": f"m1|{category_id}|0"}] if hit else []
        return {"items": items, "searchMeta": {
            "level": "mpn", "query": part_number, "categoryId": category_id, "resultCount": len(items),
        }}

    def search_by_compatibility(self, *, query: str, category_id: str, compatibility_filter: str, limit: int) -> dict[str, Any]:
        self.compat_calls.append(category_id)
        items = [{"itemId": f"v1|{category_id}|0"}] if category_id in self.categories_with_hits else []
        return {"items": items, "searchMeta": {
            "level": "compat", "query": query, "categoryId": category_id,
            "compatFilter": compatibility_filter, "resultCount": len(items),
        }}

    def search_by_keyword(self, *, vehicle_year: str, vehicle_make: str, vehicle_model: str,
                          part_description: str, limit: int, category_id: str | None) -> dict[str, Any]:
        self.keyword_calls.append(category_id)
        # 裸搜 (category_id=None) 当作总有结果, 它是最后的兜底
        hit = category_id is None or category_id in self.categories_with_hits
        items = [{"itemId": f"k1|{category_id}|0"}] if hit else []
        return {"items": items, "searchMeta": {
            "level": "keyword", "query": part_description, "categoryId": category_id, "resultCount": len(items),
        }}


SOURCE = {
    "vehicle": {"year": "2022", "make": "Toyota", "model_guess": "Camry", "vehicle_raw": "2022 Toyota Camry", "sub_model": ""},
    "part_description": "bumper cover",
    "part_type": "",
    "part_number": "",
}


def run(*, ebay: FakeEbay, source: dict[str, Any] = SOURCE, **routing: Any) -> dict[str, Any]:
    _items, tried, category_meta = search_candidates(
        source,
        ebay=ebay,
        category_lookup=get_category_lookup(),
        config=PipelineConfig(use_llm=False),
        **routing,
    )
    return {"tried": tried, "meta": category_meta}


def check(name: str, actual: Any, expected: Any) -> None:
    status = "ok  " if actual == expected else "FAIL"
    print(f"  [{status}] {name}: {actual!r}" + ("" if actual == expected else f" != expected {expected!r}"))
    if actual != expected:
        globals()["FAILURES"] = globals().get("FAILURES", 0) + 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="       %(levelname)s %(message)s", stream=sys.stdout)

    # JSON 查表: "bumper cover" -> 262146 (supports_compat)
    json_category = get_category_lookup().lookup("bumper cover") or {}
    print(f"\nJSON lookup('bumper cover') -> category_id={json_category.get('category_id')} "
          f"supports_compat={json_category.get('supports_compat')}")

    print("\n1) 传入 mapping category, primary 就有结果 -> 只搜 primary, 不碰 JSON 类目")
    ebay = FakeEbay(categories_with_hits={"33564"})
    out = run(ebay=ebay, ebay_category_id=33564, ebay_fallback_category_ids=[33640, 38658])
    check("compat calls", ebay.compat_calls, ["33564"])
    check("compat_category_source", out["meta"]["compat_category_source"], "mapping")
    check("compat_category_id_used", out["meta"]["compat_category_id_used"], "33564")

    print("\n1b) 第 1 档 MPN 与第 3 档 keyword 都用映射表类目 (MPN 已改为与 compat 同源)")
    ebay = FakeEbay(categories_with_hits={"33564"})
    source = {**SOURCE, "part_number": "521590X915"}
    out = run(ebay=ebay, source=source, ebay_category_id=33564, ebay_fallback_category_ids=[33640])
    check("mpn tier category (mapping, 已改)", ebay.mpn_calls, ["33564"])
    check("keyword tier category (mapping)", ebay.keyword_calls, ["33564"])
    check("keyword_category_source", out["meta"]["keyword_category_source"], "mapping")
    check("keyword_category_id_used", out["meta"]["keyword_category_id_used"], "33564")

    print("\n2) primary 空 -> 依次试 fallback, 命中就停")
    ebay = FakeEbay(categories_with_hits={"38658"})
    out = run(ebay=ebay, ebay_category_id=262146, ebay_fallback_category_ids=[33640, 38658, 99999])
    check("compat calls", ebay.compat_calls, ["262146", "33640", "38658"])
    check("compat_category_id_used", out["meta"]["compat_category_id_used"], "38658")
    check("tried categorySource", [t.get("categorySource") for t in out["tried"] if t["level"] == "compat"],
          ["mapping", "fallback", "fallback"])

    print("\n3) 第 2 档全空 -> 降到第 3 档, keyword 用同一组映射表类目 (不是 JSON, 也不是裸搜)")
    ebay = FakeEbay(categories_with_hits=set())
    out = run(ebay=ebay, ebay_category_id=262146, ebay_fallback_category_ids=[33640])
    check("compat calls", ebay.compat_calls, ["262146", "33640"])
    check("compat_category_id_used", out["meta"]["compat_category_id_used"], None)
    check("keyword calls", ebay.keyword_calls, ["262146", "33640"])
    check("keyword_category_source", out["meta"]["keyword_category_source"], "mapping")
    check("没有裸搜 (None 不在 keyword calls 里)", None in ebay.keyword_calls, False)

    print("\n4) 没传 ebay_category_id (自由文本/RO PartLine) -> 两档都回退 JSON 类目")
    ebay = FakeEbay(categories_with_hits={str(json_category.get("category_id"))})
    out = run(ebay=ebay)
    check("compat calls", ebay.compat_calls, [str(json_category.get("category_id"))])
    check("compat_category_source", out["meta"]["compat_category_source"], "json")
    check("keyword calls", ebay.keyword_calls, [str(json_category.get("category_id"))])
    check("keyword_category_source", out["meta"]["keyword_category_source"], "json")
    check("category_id (JSON) 仍在 meta 里", out["meta"].get("category_id"), json_category.get("category_id"))

    print("\n5) fallback 去重 + primary 重复项过滤")
    ebay = FakeEbay(categories_with_hits=set())
    run(ebay=ebay, ebay_category_id=262146, ebay_fallback_category_ids=[262146, 33640, 33640])
    check("compat calls", ebay.compat_calls, ["262146", "33640"])

    print("\n6) 描述查不到 JSON 类目 且没传 mapping -> 第 2 档跳过, 第 3 档裸搜 (兜底不能丢)")
    ebay = FakeEbay(categories_with_hits=set())
    source = {**SOURCE, "part_description": "zzz unknown widget qqq"}
    out = run(ebay=ebay, source=source)
    check("compat calls", ebay.compat_calls, [])
    check("compat_category_source", out["meta"]["compat_category_source"], None)
    check("keyword calls", ebay.keyword_calls, [None])
    check("keyword_category_source", out["meta"]["keyword_category_source"], None)

    print("\n7) 车辆信息不全 -> 第 2 档跳过, 第 3 档照旧走 (仍用映射表类目)")
    ebay = FakeEbay(categories_with_hits={"33564"})
    source = {**SOURCE, "vehicle": {**SOURCE["vehicle"], "model_guess": ""}}
    run(ebay=ebay, source=source, ebay_category_id=33564)
    check("compat calls", ebay.compat_calls, [])
    check("keyword calls", ebay.keyword_calls, ["33564"])

    print("\n8) JSON 命中但 supports_compat=False -> 第 2 档跳过, 第 3 档仍用 JSON 类目 (原行为)")
    no_compat = next(
        (info for info in get_category_lookup().descriptions.values()
         if info.get("category_id") and not info.get("supports_compat")),
        None,
    )
    if not no_compat:
        print("  [skip] JSON 里没有 supports_compat=False 且有 category_id 的条目")
    else:
        desc = next(k for k, v in get_category_lookup().descriptions.items() if v is no_compat)
        ebay = FakeEbay(categories_with_hits={str(no_compat["category_id"])})
        out = run(ebay=ebay, source={**SOURCE, "part_description": desc})
        check(f"compat 跳过 ({desc!r})", ebay.compat_calls, [])
        check("keyword calls", ebay.keyword_calls, [str(no_compat["category_id"])])
        check("keyword_category_source", out["meta"]["keyword_category_source"], "json")

    print("\n9) MPN 档: 传 ebay_category_id -> 用映射表类目 (primary), 不用 JSON 类目")
    source = {**SOURCE, "part_number": "521590X915"}  # 描述 bumper cover 的 JSON 类目是 262146
    ebay = FakeEbay(categories_with_hits={"33564"})
    run(ebay=ebay, source=source, ebay_category_id=33564, ebay_fallback_category_ids=[33640])
    check("mpn 用 mapping primary", ebay.mpn_calls, ["33564"])
    check("mpn 没用 JSON 类目", str(json_category.get("category_id")) in ebay.mpn_calls, False)

    print("\n9b) MPN 档: mapping primary 空 -> 依次试 fallback (与 compat 同逻辑)")
    ebay = FakeEbay(categories_with_hits={"33640"})
    run(ebay=ebay, source=source, ebay_category_id=33564, ebay_fallback_category_ids=[33640])
    check("mpn primary→fallback", ebay.mpn_calls, ["33564", "33640"])

    print("\n10) MPN 档: 没传 ebay_category_id -> 回退 JSON 类目 (表二查描述)")
    ebay = FakeEbay(categories_with_hits={str(json_category.get("category_id"))})
    run(ebay=ebay, source=source)
    check("mpn 回退 JSON 类目", ebay.mpn_calls, [str(json_category.get("category_id"))])

    print("\n11) MPN 档: 既无 mapping, JSON 也查不到描述 -> 不带类目, 全站搜 (category_id=None)")
    ebay = FakeEbay(categories_with_hits=set())
    run(ebay=ebay, source={**SOURCE, "part_number": "521590X915", "part_description": "zzz unknown widget qqq"})
    check("mpn 裸搜 (None)", ebay.mpn_calls, [None])

    failures = globals().get("FAILURES", 0)
    print(f"\n{'FAILED: ' + str(failures) + ' check(s)' if failures else 'ALL CHECKS PASSED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
