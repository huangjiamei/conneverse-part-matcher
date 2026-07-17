"""
用真实 raw_responses.json 跑一遍 optimizer, 检查:
  - adapter 能否解析所有 150 条
  - 每个 preset 下 gate 分布
  - 排序结果是否合理
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from optimizer import (
    build_candidate_from_ebay,
    optimize,
    PRESETS,
)


path = "/mnt/user-data/uploads/test_dataset_V4_limit_10_raw_responses.json"

# ---------- 加载 ----------
raw_records = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if line:
            raw_records.append(json.loads(line))

# 拍平: 每个 row 的 responses 里每一条 = 一个候选
# 但为了模拟真实场景, 按 row 分组 (每 row 是一次搜索, 里面 10 条候选)
groups = []
for row in raw_records:
    group_candidates = []
    for r in row['responses']:
        try:
            c = build_candidate_from_ebay(r['raw_response'])
            group_candidates.append(c)
        except Exception as e:
            print(f"[skip] row {row.get('row_index')} item parse error: {e}")
    if group_candidates:
        groups.append((row.get('row_index'), group_candidates))

print(f"Loaded {len(groups)} search groups, "
      f"{sum(len(g[1]) for g in groups)} candidates total\n")

# ---------- 每个 preset 跑一遍 ----------
for preset_name in ["sameDayJob", "costFirst", "qualityFirst", "scheduled"]:
    print("=" * 70)
    print(f"PRESET: {preset_name}")
    print("=" * 70)

    total_input = 0
    total_eligible = 0
    total_rejected = 0
    reject_reasons = {}

    for row_index, cands in groups:
        result = optimize(cands, preset=preset_name)
        total_input += result['meta']['total_input']
        total_eligible += result['meta']['total_eligible']
        total_rejected += result['meta']['total_rejected']
        for r in result['rejected']:
            key = r['reason'].split(':')[0]
            reject_reasons[key] = reject_reasons.get(key, 0) + 1

    print(f"  Input:    {total_input}")
    print(f"  Eligible: {total_eligible} ({100*total_eligible/total_input:.1f}%)")
    print(f"  Rejected: {total_rejected} ({100*total_rejected/total_input:.1f}%)")
    print(f"  Rejection reasons:")
    for k, v in sorted(reject_reasons.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")
    print()

# ---------- 详细看第一 group 的 sameDayJob 排序 ----------
print("=" * 70)
print("详细示例: 第一个搜索, preset=sameDayJob")
print("=" * 70)
row_index, cands = groups[0]
result = optimize(cands, preset="sameDayJob")

print(f"\n合格候选 (rank ↑):")
for e in result['eligible']:
    c = e['candidate']
    print(f"  #{e['rank']} total={e['total']:.1f} "
          f"(price={e['price_score']:.1f}, quality={e['quality_score']:.1f}) "
          f"${c.price:.2f}  {c.brand}  "
          f"[{c.seller_feedback_pct:.1f}% × {c.seller_feedback_count:,}]  "
          f"{c.country}")
    print(f"     {c.title[:80]}")

print(f"\n被拒 ({len(result['rejected'])} 条):")
for r in result['rejected']:
    c = r['candidate']
    print(f"  ${c.price:.2f}  {c.brand}  [{r['reason']}]")
    print(f"     {c.title[:80]}")