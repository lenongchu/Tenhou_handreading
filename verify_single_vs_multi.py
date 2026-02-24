#!/usr/bin/env python3
"""
验证单舍牌模式与多舍牌模式统计一致性。

用法:
  py verify_single_vs_multi.py [--db PATH] [--limit N] [--pattern PATTERN ...]

示例:
  py verify_single_vs_multi.py --limit 500 --pattern "c0p4p-$" "c0p6p-$"

逻辑：
  分别以单模式运行 c0p4p-$ 和 c0p6p-$，再以多模式 [c0p4p-$, c0p6p-$] 运行。
  理论上：多模式中每个模式的 matches 应等于单模式运行该模式时的 matches。
"""
import argparse
import sys
from pathlib import Path

# 确保项目根目录在 path 中
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from src.live_analyzer import LiveAnalyzer


def main():
    ap = argparse.ArgumentParser(description="验证单模式与多模式统计一致性")
    ap.add_argument("--db", default="data/tenhou.db", help="数据库路径")
    ap.add_argument("--limit", type=int, default=500, help="分析对局上限")
    ap.add_argument("--pattern", nargs="+", required=True,
                    help='舍牌模式，如 "c0p4p-$:6p" "c0p6p-$:6p"')
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return 1

    # 格式：c0p4p-$:6p 或 c0p6p-$:3p（模式:目标）
    patterns_with_targets = []
    for p in args.pattern:
        if ":" in p:
            pattern_str, target = p.rsplit(":", 1)
        else:
            pattern_str, target = p, "6p"  # 默认目标
        seq = [e for e in pattern_str.split("-") if e]
        if not seq:
            continue
        patterns_with_targets.append((seq, target))

    if len(patterns_with_targets) < 2:
        print("至少需要 2 个模式才能对比单/多")
        return 1

    analyzer = LiveAnalyzer(str(db_path))
    single_results = {}

    print("=== 单模式运行 ===")
    for i, (seq, target) in enumerate(patterns_with_targets):
        pattern_str = "-".join(seq)
        print(f"\n[{i+1}] {pattern_str} -> {target}")
        r = analyzer.analyze_discard_pattern(
            query_items=[(seq, target)],
            sample_limit=args.limit,
        )
        single_results[i] = r["pattern_results"][0]["matches"]
        print(f"    matches: {single_results[i]:,}")

    print("\n=== 多模式运行 ===")
    query_items = list(patterns_with_targets)
    r_multi = analyzer.analyze_discard_pattern(
        query_items=query_items,
        sample_limit=args.limit,
    )
    multi_matches = [pr["matches"] for pr in r_multi["pattern_results"]]
    print(f"总匹配: {r_multi['total_matches']:,}")
    for i, pr in enumerate(r_multi["pattern_results"]):
        print(f"  {pr['pattern_str']} -> {pr['target']}: {pr['matches']:,}")

    print("\n=== 对比结果 ===")
    ok = True
    for i in range(len(patterns_with_targets)):
        single_m = single_results[i]
        multi_m = multi_matches[i]
        diff = multi_m - single_m
        status = "✓" if diff == 0 else f"差异 {diff:+d}"
        if diff != 0:
            ok = False
        print(f"  模式{i+1}: 单={single_m:,}  多={multi_m:,}  {status}")
    if ok:
        print("\n统计一致。")
    else:
        print("\n存在差异，可能与以下有关：")
        print("  - 首匹配优先：多模式下一张舍牌只归入第一个匹配的模式")
        print("  - 若两模式可能匹配同一舍牌，多模式中后一模式会偏少")
        print("  - 请检查 c0p4p 与 c0p6p 是否可能在同一玩家身上同时出现（通常不会）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
