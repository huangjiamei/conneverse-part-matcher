"""
Optimizer V2 离线回归测试 —— 断言全部对照 algorithms/OPTIMIZER.md。

不联网, 不需要 eBay 凭证:
  python tests/test_optimizer_v2.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from algorithms.optimizer import (
    Candidate,
    GatesConfig,
    PRESETS,
    build_candidate_from_ebay,
    build_candidate_from_matcher,
    compute_landed_anchor,
    gate_check,
    get_preset,
    landed_cost,
    optimize,
    price_score,
    quality_breakdown,
    quality_score,
    returns_subscore,
    speed_score,
    warranty_subscore,
)
from algorithms.optimizer.ebay_adapter import _parse_warranty_years
from algorithms.optimizer.warranty import LIFETIME_MONTHS, parse_warranty

FAILURES: list[str] = []


def check(name: str, actual: Any, expected: Any) -> None:
    ok = actual == expected
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}: {actual!r}" + ("" if ok else f"  != {expected!r}"))
    if not ok:
        FAILURES.append(name)


def check_close(name: str, actual: float, expected: float, tol: float = 0.05) -> None:
    ok = actual is not None and abs(actual - expected) <= tol
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name}: {actual:.2f}" + ("" if ok else f"  != {expected} (±{tol})"))
    if not ok:
        FAILURES.append(name)


def C(item_id: str = "x", **kw: Any) -> Candidate:
    """默认是一个"干净"的候选: 全新、卖家达标、在库, 便于单独测某一个信号。"""
    base = dict(
        item_id=item_id, title=item_id, price=100.0, shipping_cost=0.0, condition_id=1000,
        availability_status="IN_STOCK", seller_feedback_pct=99.6, seller_feedback_count=5000,
        sold_qty=0, returns_accepted=True, return_period_days=30, delivery_days_max=5,
    )
    base.update(kw)
    return Candidate(**base)


# =====================================================================
def test_quality_doc_examples() -> None:
    """§三.3 的 X / Y 两行, 四个子分和总分都要对上。"""
    print("\n§三.3 质量分 · 文档 X/Y 例子")
    X = C("X", seller_feedback_pct=99.8, top_rated=True, condition_id=1000,
          warranty_years=3.0, returns_accepted=True, return_period_days=30, sold_qty=300)
    bx = quality_breakdown(X)
    check("X seller", bx["seller"], 100.0)
    check("X condition", bx["condition"], 100.0)
    check("X assurance", bx["assurance"], 88.0)
    check("X popularity", bx["popularity"], 100.0)
    check_close("X 质量分 (文档 98)", quality_score(X), 98.0, tol=0.3)

    # "无保修" = warranty 字段缺失 (→50), 不是明确写 None; 退货窗口未知 → 60
    Y = C("Y", seller_feedback_pct=99.0, top_rated=False, condition_id=3000,
          warranty_years=None, returns_accepted=True, return_period_days=None, sold_qty=0)
    by = quality_breakdown(Y)
    check("Y seller", by["seller"], 70.0)
    check("Y condition", by["condition"], 75.0)
    check("Y assurance", by["assurance"], 54.0)
    check("Y popularity", by["popularity"], 50.0)
    check_close("Y 质量分 (文档 67)", quality_score(Y), 67.0, tol=0.3)


def test_quality_subscores() -> None:
    print("\n§三.3 质量分 · 子分档位")
    check("好评率 <98 → 60-30", quality_breakdown(C(seller_feedback_pct=97.0))["seller"], 30.0)
    check("好评率 98-99 → 60", quality_breakdown(C(seller_feedback_pct=98.5))["seller"], 60.0)
    check("好评率 99-99.5 → 70", quality_breakdown(C(seller_feedback_pct=99.2))["seller"], 70.0)
    check("好评率 ≥99.5 → 80", quality_breakdown(C(seller_feedback_pct=99.9))["seller"], 80.0)
    check("top_rated +20", quality_breakdown(C(seller_feedback_pct=99.9, top_rated=True))["seller"], 100.0)
    check("好评率缺失 → 中性 50", quality_breakdown(C(seller_feedback_pct=0.0))["seller"], 50.0)

    for cid, want in [(1000, 100.0), (1500, 90.0), (2500, 82.0), (3000, 75.0), (5999, 75.0), (6000, 60.0), (None, 50.0)]:
        check(f"condition {cid} 桶", quality_breakdown(C(condition_id=cid))["condition"], want)

    print("  warranty 档位 (含 adapter 解析链路)")
    for raw, want_years, want_score, note in [
        ("None", 0.0, 30.0, "明确无"),
        ("none", 0.0, 30.0, "明确无"),
        ("No", 0.0, 30.0, "明确无"),
        ("Yes", 0.5, 40.0, "<1年"),
        ("6 Months", 0.5, 40.0, "<1年"),
        ("60 Day", 60 / 365, 40.0, "<1年"),
        ("1 Year", 1.0, 60.0, "≥1年"),
        ("3 Years", 3.0, 80.0, "≥3年"),
        ("Lifetime", 99.0, 100.0, "Lifetime"),
    ]:
        years = _parse_warranty_years(raw)
        check(f'adapter "{raw}" → {want_years:.3g} 年', round(years, 6), round(want_years, 6))
        check(f'  → warranty 子分 ({note})', warranty_subscore(years), want_score)
    check("warranty 字段缺失 → 中性 50 (与'明确无'区分)", warranty_subscore(None), 50.0)
    check('"None" 全链路: 字符串 → 子分 30',
          quality_breakdown(C(warranty_years=_parse_warranty_years("None")))["warranty"], 30.0)

    check("warranty Lifetime+30天退货", quality_breakdown(C(warranty_years=99.0))["assurance"], 100.0)
    check("warranty 明确无(0)+30天退货", quality_breakdown(C(warranty_years=0.0))["assurance"], 58.0)
    check("明确不接受退货 → returns 0", quality_breakdown(C(warranty_years=1.0, returns_accepted=False))["assurance"], 36.0)
    check("退货信息缺失 → returns 中性 50",
          quality_breakdown(C(warranty_years=1.0, returns_accepted=None, return_period_days=None))["assurance"], 56.0)
    check("接受但窗口未知 → 60",
          quality_breakdown(C(warranty_years=1.0, returns_accepted=True, return_period_days=None))["assurance"], 60.0)

    check("sold=0 → 中性 50", quality_breakdown(C(sold_qty=0))["popularity"], 50.0)
    check_close("sold=50 → 满分", quality_breakdown(C(sold_qty=50))["popularity"], 100.0)
    check_close("sold=7 → 中间档", quality_breakdown(C(sold_qty=7))["popularity"], 76.5, tol=0.5)


def test_price_and_anchor() -> None:
    print("\n§三.1 价格分 + 离群保护")
    rows = [("A", 74, 0.0, 100.0), ("B", 60, 20.0, 92.5), ("C", 120, 0.0, 61.7), ("D", 200, None, 50.0)]
    cands = [C(n, price=p, shipping_cost=s) for n, p, s, _ in rows]
    anchor = compute_landed_anchor([landed_cost(c) for c in cands])
    check("anchor (文档表)", anchor, 74.0)
    for (name, _, _, want), c in zip(rows, cands):
        check_close(f"{name} price_score", price_score(landed_cost(c), anchor), want, tol=0.1)

    check("winsorize 触发: [3,74,80] → 次低", compute_landed_anchor([3.0, 74.0, 80.0]), 74.0)
    check("winsorize 不触发: [50,74,80] → 最低", compute_landed_anchor([50.0, 74.0, 80.0]), 50.0)
    check("边界 0.6x 不触发: [44.4,74,80]", compute_landed_anchor([44.4, 74.0, 80.0]), 44.4)
    check("边界 略低于 0.6x 触发: [44.3,74,80]", compute_landed_anchor([44.3, 74.0, 80.0]), 74.0)

    # 离群保护需要 >=3 条有价候选: 两条时没有参照群, 判不出谁离群
    check("n=2 低价<0.6×次低 → 不启用保护, 取最低", compute_landed_anchor([149.0, 918.10]), 149.0)
    check("n=3 同样情形 → 启用保护, 取次低", compute_landed_anchor([149.0, 918.10, 950.0]), 918.10)
    check("n=2 时价格分仍有分辨率",
          round(price_score(918.10, compute_landed_anchor([149.0, 918.10])), 1), 16.2)
    check("全缺 landed → None", compute_landed_anchor([None, None]), None)
    check("单候选 → 自己当锚 → 100", price_score(74.0, compute_landed_anchor([74.0])), 100.0)
    check("landed 缺失 → 中性 50", price_score(None, 74.0), 50.0)
    check("price<=0 → 0", price_score(0.0, 74.0), 0.0)
    check("运费缺失不当免运 (landed=None)", landed_cost(C(price=200, shipping_cost=None)), None)


def test_speed() -> None:
    print("\n§三.2 速度分")
    for d, want in [(4, 83.3), (6, 66.7), (11, 25.0)]:
        check_close(f"{d} 天", speed_score(d), want, tol=0.1)
    check("缺预估 → 中性 50", speed_score(None), 50.0)
    check("≤2 天封顶 100", speed_score(1), 100.0)
    check("≥14 天保底 0", speed_score(30), 0.0)


def test_gates() -> None:
    print("\n§四 第 1 层 gate")
    default = GatesConfig()
    check("7000 For parts 排除 (功能性, 固定不可关)",
          gate_check(C(condition_id=7000), default), "condition:for_parts")
    print("  新/旧件不再进 gate: 除 7000 外任何件况都放行")
    for cid in (1000, 1500, 1750, 2000, 2500, 3000, 4000, 5999, 6000, None):
        check(f"condition_id={cid} 放行", gate_check(C(condition_id=cid), default), None)
    check("GatesConfig 不再有 require_new / allow_used 字段",
          [f for f in ("require_new", "allow_used") if hasattr(default, f)], [])

    check("好评率 97.9 拒", gate_check(C(seller_feedback_pct=97.9), default), "seller_feedback:97.9%")
    check("好评率 98.0 放行", gate_check(C(seller_feedback_pct=98.0), default), None)
    check("评价数 99 拒", gate_check(C(seller_feedback_count=99), default), "seller_count:99")
    check("评价数 100 放行", gate_check(C(seller_feedback_count=100), default), None)
    check("评价数 0 (缺信号) 放行", gate_check(C(seller_feedback_count=0), default), None)

    print("  warranty ≥1年 (TOLERANT: 只踢能证明不达标的)")
    check("明确无保修 → 砍", gate_check(C(warranty_none=True), default), "warranty:none")
    check("6 个月 → 砍", gate_check(C(warranty_months=6.0), default), "warranty:6mo")
    check("11 个月 → 砍", gate_check(C(warranty_months=11.0), default), "warranty:11mo")
    check("12 个月 → 放行", gate_check(C(warranty_months=12.0), default), None)
    check("36 个月 → 放行", gate_check(C(warranty_months=36.0), default), None)
    check("Lifetime (1200) → 放行", gate_check(C(warranty_months=LIFETIME_MONTHS), default), None)
    check("缺失 (months=None, none=False) → 放行",
          gate_check(C(warranty_months=None, warranty_none=False), default), None)
    check("含糊 'Yes' (years=0.5 但 months=None) → 放行",
          gate_check(C(warranty_years=0.5, warranty_months=None), default), None)
    check("关掉开关后不 gate", gate_check(C(warranty_none=True), GatesConfig(require_warranty=False)), None)

    print("  退货政策污染: 解析后当没填, 不该被 warranty gate 砍")
    for raw in ("30 Days Return Accepted", "See Return Policy",
                "30-day returns accepted. Item must be original and packaged."):
        w = parse_warranty(raw)
        check(f"{raw[:28]!r} → months=None", w.months, None)
        check(f"  → 不被 gate", gate_check(C(warranty_months=w.months, warranty_none=w.is_none), default), None)

    rush = GatesConfig(max_delivery_days=3)
    check("到货 5d 超 3d 截止", gate_check(C(delivery_days_max=5), rush), "delivery:5d>3d")
    check("到货 3d 卡线放行", gate_check(C(delivery_days_max=3), rush), None)
    check("到货缺失 + 截止 → 放行", gate_check(C(delivery_days_max=None), rush), None)

    check("OUT_OF_STOCK 拒", gate_check(C(availability_status="OUT_OF_STOCK"), default), "stock:out_of_stock")
    check("OUT_OF_STOCK 不要求在库时放行", gate_check(C(availability_status="OUT_OF_STOCK"), GatesConfig(require_in_stock=False)), None)

    check("country 默认不 gate", gate_check(C(country="CN"), default), None)
    check("country 开合规开关才 gate", gate_check(C(country="CN"), GatesConfig(require_domestic=True)), "country:CN")
    check("fitment 无数据 → 休眠", gate_check(C(fitment_complaint_rate=None), default), None)
    check("fitment 有数据超阈值 → 拒",
          gate_check(C(fitment_complaint_rate=0.5, fitment_review_sample=20), default), "fitment_risk:50%")


def test_presets() -> None:
    print("\n§五 preset 表")
    # 四档 gate 现在完全相同 (只有 in_stock), 差别全在权重
    expected = {
        "Rush":     ((15, 60, 25), dict(require_in_stock=True, max_delivery_days=None)),
        "Balanced": ((35, 30, 35), dict(require_in_stock=True, max_delivery_days=None)),
        "Budget":   ((60, 10, 30), dict(require_in_stock=True, max_delivery_days=None)),
        "Premium":  ((15, 25, 60), dict(require_in_stock=True, max_delivery_days=None)),
    }
    for name, (weights, gates) in expected.items():
        p = get_preset(name)
        check(f"{name} 权重 p/s/q", (p.weights.price, p.weights.speed, p.weights.quality), weights)
        actual_gates = {k: getattr(p.gates, k) for k in gates}
        check(f"{name} gate", actual_gates, gates)
        check(f"{name} 卖家门槛统一 98/100",
              (p.gates.min_seller_feedback_pct, p.gates.min_seller_feedback_count), (98.0, 100))
        check(f"{name} 不绑 US only", p.gates.require_domestic, False)
        check(f"{name} 不绑到货硬截止", p.gates.max_delivery_days, None)

    print("\n  二手件: 四档全部放行 (件况只影响打分, 不进 gate)")
    used = C("used", condition_id=3000)
    new_other = C("newother", condition_id=1500)
    for name in ("Rush", "Balanced", "Budget", "Premium"):
        check(f"{name} 对 Used(3000)", gate_check(used, get_preset(name).gates), None)
        check(f"{name} 对 New other(1500)", gate_check(new_other, get_preset(name).gates), None)

    print("  For parts(7000): 四档一律排除")
    for_parts = C("junk", condition_id=7000)
    for name in ("Rush", "Balanced", "Budget", "Premium"):
        check(f"{name} 对 For parts(7000)", gate_check(for_parts, get_preset(name).gates), "condition:for_parts")

    print("  warranty: 四档共享同一条门槛 (≥1年, TOLERANT)")
    no_warranty = C("nowarranty", warranty_none=True)
    short_warranty = C("short", warranty_months=6.0)
    unknown_warranty = C("unknown", warranty_months=None, warranty_none=False)
    for name in ("Rush", "Balanced", "Budget", "Premium"):
        g = get_preset(name).gates
        check(f"{name} require_warranty", (g.require_warranty, g.min_warranty_months), (True, 12.0))
        check(f"{name} 对明确无保修", gate_check(no_warranty, g), "warranty:none")
        check(f"{name} 对 6 个月", gate_check(short_warranty, g), "warranty:6mo")
        check(f"{name} 对缺失", gate_check(unknown_warranty, g), None)

    print("  backorder: 四档一律拒 (in_stock 全档打开, Budget 也不再放行)")
    backorder = C("backorder", availability_status="OUT_OF_STOCK")
    for name in ("Rush", "Balanced", "Budget", "Premium"):
        check(f"{name} 对 OUT_OF_STOCK", gate_check(backorder, get_preset(name).gates), "stock:out_of_stock")

    print("  件况分仍然偏新 (软降权代替硬排除)")
    check("New(1000) 件况分", quality_breakdown(C(condition_id=1000))["condition"], 100.0)
    check("Used(3000) 件况分", quality_breakdown(C(condition_id=3000))["condition"], 75.0)

    print("\n  老 preset 名别名 (向后兼容)")
    for legacy, target in [("sameDayJob", "Rush"), ("costFirst", "Budget"),
                           ("qualityFirst", "Premium"), ("scheduled", "Balanced")]:
        check(f"{legacy} → {target}", get_preset(legacy).name, target)
        check(f"{legacy} 仍在 PRESETS 里 (service.py 的校验)", legacy in PRESETS, True)


def test_optimize_end_to_end() -> None:
    print("\n第 3 层加权 + optimize() 返回结构")
    cands = [
        C("A", price=74, shipping_cost=0.0, delivery_days_max=2),
        C("B", price=60, shipping_cost=20.0, delivery_days_max=9),
        C("C", price=120, shipping_cost=0.0, delivery_days_max=5, top_rated=True),
    ]
    r = optimize(cands, preset="Balanced")
    check("全部通过 gate", r["meta"]["total_eligible"], 3)
    check("landed_anchor", r["meta"]["landed_anchor"], 74.0)
    check("min_eligible_price 老别名同值", r["meta"]["min_eligible_price"], r["meta"]["landed_anchor"])
    check("权重归一化到 1", round(sum(r["meta"]["weights"].values()), 6), 1.0)
    check("eligible 带 speed_score", all("speed_score" in e for e in r["eligible"]), True)
    check("rank 连续", [e["rank"] for e in r["eligible"]], [1, 2, 3])
    check("按 total 降序", r["eligible"] == sorted(r["eligible"], key=lambda e: -e["total"]), True)

    top = r["eligible"][0]
    manual = (0.35 * top["price_score"] + 0.30 * top["speed_score"] + 0.35 * top["quality_score"])
    check_close("total = wp·p + ws·s + wq·q", top["total"], manual, tol=0.001)

    print("\n  Rush 不再硬过滤慢货, 靠 speed=60 排序")
    rush = optimize(cands, preset="Rush")
    check("三条全留", rush["meta"]["total_eligible"], 3)
    check("没有 delivery 拒因", [x["reason"] for x in rush["rejected"]], [])
    check("最快的 (2 天) 排第一", rush["eligible"][0]["candidate"].item_id, "A")
    check("最慢的 (9 天) 垫底", rush["eligible"][-1]["candidate"].item_id, "B")

    print("\n  硬截止改成独立开关: 高级入口显式设才生效")
    hard = optimize(cands, preset="Rush", gates=GatesConfig(require_in_stock=True, max_delivery_days=3))
    check("显式设 3 天后只剩 A", [e["candidate"].item_id for e in hard["eligible"]], ["A"])
    check("其余按 delivery 拒", sorted(x["reason"] for x in hard["rejected"]), ["delivery:5d>3d", "delivery:9d>3d"])

    print("\n  老调用方 (只给 price/quality, 无 speed)")
    old = optimize(cands, weights_price=80, weights_quality=20)
    check("speed 权重退化为 0", old["meta"]["weights"]["speed"], 0.0)
    check("price 权重 0.8", round(old["meta"]["weights"]["price"], 6), 0.8)
    old_top = old["eligible"][0]
    check_close("total 不含速度维", old_top["total"],
                0.8 * old_top["price_score"] + 0.2 * old_top["quality_score"], tol=0.001)
    check("老调用仍有 price_score/quality_score",
          all(k in old_top for k in ("price_score", "quality_score", "total", "rank")), True)

    print("\n  被 gate 掉的低价件不该拉低锚点")
    with_junk = cands + [C("JUNK", price=3, shipping_cost=0.0, condition_id=7000)]
    r2 = optimize(with_junk, preset="Balanced")
    check("JUNK 被 gate", len(r2["rejected"]), 1)
    check("锚点仍是 74 (不是 3)", r2["meta"]["landed_anchor"], 74.0)

    check("空输入不炸", optimize([], preset="Balanced")["meta"]["total_eligible"], 0)


def test_adapters() -> None:
    print("\nadapter 数据层 (Phase 1)")
    e = build_candidate_from_ebay({
        "itemId": "v1|1|0", "title": "t", "conditionId": "1000", "condition": "New",
        "price": {"value": "74.00"}, "shippingOptions": [{"shippingCost": {"value": "0.00"}}],
    })
    check("eBay conditionId → int", e.condition_id, 1000)
    check("eBay 免运费 = 0.0", e.shipping_cost, 0.0)

    check("eBay 有 returnTerms → bool",
          build_candidate_from_ebay({"itemId": "v1|1|0", "title": "t", "price": {"value": "1"},
                                     "returnTerms": {"returnsAccepted": False}}).returns_accepted, False)

    e2 = build_candidate_from_ebay({"itemId": "v1|2|0", "title": "t", "conditionId": "7000", "price": {"value": "200"}})
    check("eBay 无 shippingOptions → None", e2.shipping_cost, None)
    check("eBay 7000 抽到", e2.condition_id, 7000)
    check("eBay 无 returnTerms → None (不是 False)", e2.returns_accepted, None)

    m = build_candidate_from_matcher({
        "item_id": "x", "title": "t", "condition": "For parts or not working",
        "price": {"value": "10"}, "optimizer_fields": {"shipping_cost": None},
    })
    check("matcher 按字符串反推 7000", m.condition_id, 7000)
    check("matcher 运费缺失 → None", m.shipping_cost, None)

    m2 = build_candidate_from_matcher({
        "item_id": "x", "title": "t", "condition": "New other (see details)",
        "price": {"value": "10"}, "optimizer_fields": {"shipping_cost": "5.5", "condition_id": 2500},
    })
    check("matcher 优先用 optimizer_fields.condition_id", m2.condition_id, 2500)
    check("matcher 运费 0 与缺失区分", m2.shipping_cost, 5.5)

    check("matcher 无 returns_accepted key → None", m.returns_accepted, None)
    check("matcher returns_accepted=False → False",
          build_candidate_from_matcher({"item_id": "x", "title": "t", "price": {"value": "1"},
                                        "optimizer_fields": {"returns_accepted": False}}).returns_accepted, False)

    m3 = build_candidate_from_matcher({"item_id": "x", "title": "t", "condition": "没见过的说法", "price": {"value": "10"}})
    check("认不出的件况 → None (gate 放行)", m3.condition_id, None)


def main() -> None:
    test_quality_doc_examples()
    test_quality_subscores()
    test_price_and_anchor()
    test_speed()
    test_gates()
    test_presets()
    test_optimize_end_to_end()
    test_adapters()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项 — {FAILURES}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
