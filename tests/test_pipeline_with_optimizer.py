"""
CLI: 拿一条 source_part_info 跑完整流程 (match -> optimize).

用法:
  # 用 example-source.json (你仓库根目录那份)
  python test_pipeline_with_optimizer.py --input example-source.json

  # 或者拿 response.json 里 matcher 已经跑好的输出直接测 optimizer (跳过 matcher)
  python test_pipeline_with_optimizer.py --response response.json

  # 指定 preset
  python test_pipeline_with_optimizer.py --input example-source.json --preset qualityFirst

不需要跑 FastAPI 服务, 也不需要碰 service.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让脚本能从仓库根目录直接跑 (algorithms 和 end_to_end_part_matcher 都能 import)
sys.path.insert(0, str(Path(__file__).parent.parent))

from algorithms.optimizer import (
    PRESETS,
    build_candidate_from_matcher,
    optimize,
)


def run_matcher(source_json_path: Path, use_llm: bool = False) -> dict:
    """调 matcher pipeline. 需要 EBAY 凭证在 env."""
    from end_to_end_part_matcher.pipeline import PipelineConfig, match_source_part
    source = json.loads(source_json_path.read_text(encoding="utf-8-sig"))
    return match_source_part(source, config=PipelineConfig(use_llm=use_llm))


def load_matcher_output(response_json_path: Path) -> dict:
    """从已经跑好的 matcher 输出加载."""
    return json.loads(response_json_path.read_text(encoding="utf-8-sig"))


def print_result(matcher_result: dict, preset_name: str) -> None:
    source = matcher_result.get("source_part_info", {})
    vehicle = source.get("vehicle", {})
    candidates_raw = matcher_result.get("candidate_info_list", [])

    print("=" * 76)
    print(f"SOURCE: {vehicle.get('year')} {vehicle.get('make')} {vehicle.get('model_guess')}")
    print(f"        {source.get('part_description')}  MPN={source.get('part_number')}")
    print(f"        Matcher label={matcher_result.get('label')} "
          f"({matcher_result.get('label_source')})")
    print(f"        {len(candidates_raw)} candidates from matcher")
    print("=" * 76)

    # 只取 matcher label 正的候选进 optimizer (label != 0)
    # label=1: 匹配上; label=None: 需要复核 (LLM 关的情况下按 None 处理)
    eligible_for_optim = [
        c for c in candidates_raw
        if c.get("candidate_label") != 0
    ]
    print(f"\n{len(eligible_for_optim)} candidates pass matcher label filter, "
          f"feeding to optimizer...")

    # 检查 optimizer_fields 存不存在
    with_opt_fields = sum(1 for c in eligible_for_optim if c.get("optimizer_fields"))
    if with_opt_fields == 0 and eligible_for_optim:
        print(f"\n⚠️ 警告: 所有 candidate 都没有 optimizer_fields 字段.")
        print(f"   看起来 matcher pipeline 还是老版本 (未合并 optimizer 字段 patch).")
        print(f"   optimizer 会以退化模式运行, 缺失信号全部 None.")
    elif with_opt_fields < len(eligible_for_optim):
        print(f"\n⚠️ {with_opt_fields}/{len(eligible_for_optim)} 有 optimizer_fields")

    # 转成 Candidate
    candidates = [build_candidate_from_matcher(c) for c in eligible_for_optim]

    # 跑 optimizer
    result = optimize(candidates, preset=preset_name)

    print(f"\n{'-' * 76}")
    print(f"OPTIMIZER preset={preset_name}: "
          f"eligible={result['meta']['total_eligible']}, "
          f"rejected={result['meta']['total_rejected']}")
    print(f"{'-' * 76}\n")

    print(f"合格候选 (rank ↑):")
    for e in result["eligible"]:
        c = e["candidate"]
        print(f"  #{e['rank']:2d}  total={e['total']:5.1f}  "
              f"(price={e['price_score']:5.1f}, quality={e['quality_score']:5.1f})  "
              f"${c.price:7.2f}  "
              f"seller=[{c.seller_feedback_pct:5.1f}% × {c.seller_feedback_count:>7,}]  "
              f"{c.country or '?':2s}")
        print(f"       {c.title[:88]}")

    if result["rejected"]:
        print(f"\n被 optimizer 拒 ({len(result['rejected'])} 条):")
        for r in result["rejected"]:
            c = r["candidate"]
            print(f"  ${c.price:7.2f}  [{r['reason']}]  {c.title[:70]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", type=Path,
                     help="source_part_info JSON. 会调 matcher pipeline (需要 EBAY 凭证)")
    src.add_argument("--response", type=Path,
                     help="matcher 已经输出的 JSON, 跳过 matcher 直接测 optimizer")
    parser.add_argument("--preset", default="sameDayJob",
                        choices=list(PRESETS.keys()))
    parser.add_argument("--use-llm", action="store_true")
    args = parser.parse_args()

    if args.input:
        print(f"[matcher] running pipeline on {args.input.name}...")
        result = run_matcher(args.input, use_llm=args.use_llm)
    else:
        print(f"[matcher] loading precomputed output from {args.response.name}")
        result = load_matcher_output(args.response)

    print_result(result, args.preset)


if __name__ == "__main__":
    main()