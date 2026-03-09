#!/usr/bin/env python3
"""
诊断 apf-2p + 前段有打=[37]mOR[37]s 全 0 问题。
用法: python -m scripts.debug_apf_prior
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.live_analyzer import LiveAnalyzer
from src.equivalent_variants import (
    generate_equivalent_variants,
    match_discard_to_variant,
    prior_required_pattern_to_variants,
    match_discard_pattern_contained,
)
from src.mjlog_parser import MjlogParser


def main():
    pattern = "apf-2p"
    target = "2p"
    prior_req = "[37]mOR[37]s"

    # 1. 变体与 prior 解析
    parts = pattern.split("-")
    vars_p = generate_equivalent_variants(
        parts, target, None, None, None, prior_req
    )
    print(f"变体数: {len(vars_p)}")
    for i, v in enumerate(vars_p[:2]):
        print(f"  变体{i}: discard={v['discard']}, prior_req={v.get('prior_discard_required')}")

    prior_variants = prior_required_pattern_to_variants(prior_req)
    print(f"prior variants: {prior_variants}")

    # 2. 简单 prior 匹配测试
    test_prior = [("3m", False), ("东", False), ("2p", False)]
    ok = match_discard_pattern_contained(test_prior, prior_variants, {})
    print(f"prior [3m,东,2p] 匹配 [37]mOR[37]s: {ok}")

    test_prior2 = [("东", False), ("2p", False)]
    ok2 = match_discard_pattern_contained(test_prior2, prior_variants, {})
    print(f"prior [东,2p] 匹配 [37]mOR[37]s: {ok2}")

    # 3. 主模式匹配测试（无 prior 检查）
    test_full = [("3m", False), ("东", False), ("2p", False)]
    honor_ctx = {"visible_tiles": {}, "bakaze": "东", "jikaze": "东"}
    # 安牌需要 visible_tiles；空 dict 会导致 ap 失败
    mv = match_discard_to_variant(test_full, vars_p, honor_ctx)
    print(f"主模式 [3m,东,2p] 匹配 apf-2p (visible_tiles=空): {bool(mv)}")

    # 4. 调用实际分析
    root = Path(__file__).parent.parent
    cfg_path = root / "config.ini"
    db_path = str(root / "data" / "tenhou.db")
    if cfg_path.exists():
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(cfg_path, encoding="utf-8")
        if cfg.has_option("Database", "path"):
            db_path = cfg.get("Database", "path").strip()
    la = LiveAnalyzer(db_path)
    result = la.analyze(
        pattern,
        target,
        analysis_target="target_count",
        turn_ranges=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)],
        riichi_constraint="no_riichi",
        call_constraint="no_call",
        prior_discard_required=prior_req,
        sample_pool_cap=100,
        max_matches_per_log=50,
    )
    print("\n--- 实际分析结果 ---")
    if "grid" in result:
        for cell in result["grid"]:
            tr = cell.get("turn_range")
            dist = cell.get("target_count_dist") or {}
            total = sum(dist.values())
            print(f"  巡目{tr}: total={total}, dist={dist}")
    else:
        dist = result.get("target_count_dist") or {}
        total = result.get("total_matches", 0)
        print(f"  total_matches={total}, dist={dist}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
