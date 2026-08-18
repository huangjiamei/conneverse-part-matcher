"""
deliveryZip (收货地感知的运费/时效) 测试。

离线部分不联网, 不需要凭证 —— 校验邮编归一化 + X-EBAY-C-ENDUSERCTX 请求头:
  python tests/test_delivery_zip.py

联网部分需要 .env 里的 EBAY_CLIENT_ID / EBAY_CLIENT_SECRET, 显式加 --live 才跑。
它拿同一个 item 分别按 94107 (SF) 和 10001 (NYC) 取一次, 对比运费/送达时间:
  python tests/test_delivery_zip.py --live
计价运费 (calculated shipping) 的 listing 两地会不一样; 全国统一价 (flat rate)
的 listing 两地相同 —— 这是预期, 不算失败。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from end_to_end_part_matcher import ebay as ebay_module
from end_to_end_part_matcher import pipeline as pipeline_module
from end_to_end_part_matcher.ebay import EbayClient
from end_to_end_part_matcher.pipeline import PipelineConfig, match_source_part
from end_to_end_part_matcher.service import MatchRequest
from end_to_end_part_matcher.utils import normalize_delivery_zip

FAILURES = 0

HEADER = "X-EBAY-C-ENDUSERCTX"
SF, NYC = "94107", "10001"


def check(name: str, actual: Any, expected: Any) -> None:
    global FAILURES
    ok = actual == expected
    if not ok:
        FAILURES += 1
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {actual!r}" + ("" if ok else f" != {expected!r}"))


class RecordingTransport:
    """替掉 ebay.request_json, 记录每次请求的 url 和 headers, 返回空壳响应。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, *, headers: Any = None, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append((url, dict(headers or {})))
        return {"itemSummaries": [{"itemId": "v1|123|0"}], "itemId": "v1|123|0", "title": "stub"}

    def headers_for(self, url_fragment: str) -> list[dict[str, str]]:
        return [h for url, h in self.calls if url_fragment in url]


def stub_client(**kwargs: Any) -> EbayClient:
    client = EbayClient(client_id="id", client_secret="secret", **kwargs)
    client._token, client._token_expires_at = "stub-token", float("inf")  # 不去换 token
    return client


def test_normalization() -> None:
    print("\n1) 邮编宽松校验: 5 位数字通过, 其余静默变 None")
    check("94107", normalize_delivery_zip("94107"), "94107")
    check("带空格", normalize_delivery_zip("  94107 "), "94107")
    check("ZIP+4 取前 5 位", normalize_delivery_zip("94107-1234"), "94107")
    check("数字类型", normalize_delivery_zip(94107), "94107")
    check("None", normalize_delivery_zip(None), None)
    check("空串", normalize_delivery_zip(""), None)
    check("4 位", normalize_delivery_zip("9410"), None)
    check("6 位", normalize_delivery_zip("941070"), None)
    check("字母", normalize_delivery_zip("SW1A 1AA"), None)
    check("注入尝试", normalize_delivery_zip("94107,zip=99999"), None)
    check("dict", normalize_delivery_zip({"zip": "94107"}), None)


def test_header_on_browse_calls() -> None:
    print("\n2) 有效邮编 -> Browse 的 search 和 getItem 都带上下文头")
    transport = RecordingTransport()
    ebay_module.request_json = transport
    client = stub_client(delivery_zip=SF)
    client.search_by_part_number(part_number="ABC123", limit=5, category_id=None)
    client.get_item("v1|123|0")

    expected = f"contextualLocation=country%3DUS%2Czip%3D{SF}"
    search_headers = transport.headers_for("/item_summary/search")
    item_headers = transport.headers_for("/buy/browse/v1/item/")
    check("search 请求数", len(search_headers), 1)
    check("search 头", search_headers[0].get(HEADER), expected)
    check("getItem 请求数", len(item_headers), 1)
    check("getItem 头", item_headers[0].get(HEADER), expected)
    check("百分号转义了内层 = 和 ,", "%3D" in expected and "%2C" in expected and "," not in expected, True)
    check("原有 marketplace 头没被顶掉", search_headers[0].get("X-EBAY-C-MARKETPLACE-ID"), "EBAY_US")

    print("\n3) Taxonomy 不是 Browse, 不带这个头")
    transport.calls.clear()
    client.get_category_subtree(category_id=6028, category_tree_id="100")
    check("taxonomy 头", transport.calls[0][1].get(HEADER), None)


def test_no_zip_keeps_current_behavior() -> None:
    print("\n4) 不传 / 传非法邮编 -> 不带头, 完全是原行为")
    for label, zip_value in [("未传", None), ("空串", ""), ("非法", "abcde")]:
        transport = RecordingTransport()
        ebay_module.request_json = transport
        client = stub_client(delivery_zip=zip_value)
        client.search_by_part_number(part_number="ABC123", limit=5, category_id=None)
        check(f"{label}: client.delivery_zip", client.delivery_zip, None)
        check(f"{label}: 请求头", transport.calls[0][1].get(HEADER), None)


def test_pipeline_and_service_layer() -> None:
    print("\n5) service 层: deliveryZip / delivery_zip 都收, 垃圾值不报 422")
    base = {
        "source_part_info": {
            "vehicle": {"year": "2022", "make": "Toyota", "model_guess": "Camry"},
            "part_description": "front bumper cover",
        },
    }
    check("camelCase", MatchRequest(**{**base, "deliveryZip": SF}).delivery_zip, SF)
    check("snake_case", MatchRequest(**{**base, "delivery_zip": SF}).delivery_zip, SF)
    check("缺省", MatchRequest(**base).delivery_zip, None)
    check("垃圾值不报错", MatchRequest(**{**base, "deliveryZip": "not-a-zip"}).delivery_zip, None)
    check("数字", MatchRequest(**{**base, "deliveryZip": 10001}).delivery_zip, "10001")
    check("未知参数被忽略", MatchRequest(**{**base, "totally_unknown": 1}).delivery_zip, None)

    print("\n6) pipeline: config.delivery_zip 一路传到 eBay 请求头, 并回写 meta")
    transport = RecordingTransport()
    ebay_module.request_json = transport
    original_client_factory = pipeline_module.EbayClient
    try:
        pipeline_module.EbayClient = stub_client  # match_source_part 内部自建 client
        record = match_source_part(
            base["source_part_info"],
            config=PipelineConfig(use_llm=False, delivery_zip=NYC),
        )
    finally:
        pipeline_module.EbayClient = original_client_factory

    browse_headers = [h for url, h in transport.calls if "/buy/browse/" in url]
    expected = f"contextualLocation=country%3DUS%2Czip%3D{NYC}"
    check("Browse 请求都带头", all(h.get(HEADER) == expected for h in browse_headers) and bool(browse_headers), True)
    check("meta.delivery_zip_used", record["dataset_meta"]["delivery_zip_used"], NYC)


LIVE_QUERIES = ["brake rotor", "bumper cover", "headlight assembly", "tailgate"]


def _shipping_snapshot(detail: dict[str, Any]) -> dict[str, Any]:
    opt = (detail.get("shippingOptions") or [{}])[0]
    return {
        "cost": (opt.get("shippingCost") or {}).get("value"),
        "cost_type": opt.get("shippingCostType"),
        "delivery_min": opt.get("minEstimatedDeliveryDate"),
        "delivery_max": opt.get("maxEstimatedDeliveryDate"),
    }


def test_live_two_zips() -> None:
    """同一批 item 分别按两个远距离邮编取一次, 对比运费和送达时间。

    样本要够杂: 免运 + 全国统一价的 listing 两地本来就一样, 只挑几条很容易全是这种,
    看起来像没生效。所以跨几个品类各取一批, 再看整体有多少条随邮编变。
    """
    print("\n7) [live] 同一批 item, 94107 (SF) vs 10001 (NYC)")
    probe = EbayClient()
    sf_client, nyc_client = EbayClient(delivery_zip=SF), EbayClient(delivery_zip=NYC)

    item_ids: list[str] = []
    for query in LIVE_QUERIES:
        found = probe.search_by_keyword(
            vehicle_year="", vehicle_make="", vehicle_model="",
            part_description=query, limit=5, category_id=None,
        )["items"]
        item_ids += [str(i["itemId"]) for i in found if i.get("itemId")]
    if not item_ids:
        print("  [skip] eBay 没返回候选")
        return

    cost_differs = date_differs = identical = 0
    for item_id in item_ids:
        sf, nyc = _shipping_snapshot(sf_client.get_item(item_id)), _shipping_snapshot(nyc_client.get_item(item_id))
        if sf == nyc:
            identical += 1
            continue
        cost_differs += sf["cost"] != nyc["cost"]
        date_differs += (sf["delivery_min"], sf["delivery_max"]) != (nyc["delivery_min"], nyc["delivery_max"])
        print(f"  {item_id} ({sf['cost_type']})\n    SF  {sf}\n    NYC {nyc}")

    total = len(item_ids)
    print(f"\n  共 {total} 条: 运费不同 {cost_differs}, 时效不同 {date_differs}, 两地完全一致 {identical}")
    print("  一致的多半是免运 / 全国统一价 listing —— 预期就不随邮编变。")
    check("至少有 listing 随邮编变 (说明上下文头生效)", cost_differs + date_differs > 0, True)

    print("\n8) [live] 不传邮编 = 原来的通用行为 (只看不炸)")
    generic = _shipping_snapshot(EbayClient().get_item(item_ids[0]))
    print(f"  generic: {generic}")
    check("不传邮编照常拿到 shippingOptions", "cost" in generic, True)


def main() -> None:
    original_request_json = ebay_module.request_json
    try:
        test_normalization()
        test_header_on_browse_calls()
        test_no_zip_keeps_current_behavior()
        test_pipeline_and_service_layer()
    finally:
        ebay_module.request_json = original_request_json

    if "--live" in sys.argv:
        test_live_two_zips()

    print(f"\n{'FAILED: ' + str(FAILURES) + ' check(s)' if FAILURES else 'ALL CHECKS PASSED'}")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
