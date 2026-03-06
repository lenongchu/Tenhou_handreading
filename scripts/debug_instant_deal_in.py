#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断脚本：即时铳率恒为 0 的根因排查。
用法: python -m scripts.debug_instant_deal_in [--limit N] [--pattern P] [--target T]

从数据库取前 N 条 log_json，检查：
1. round_payloads 是否为空
2. RoundInstantDealInAnalyzer 是否创建成功
3. 真实匹配场景下 evaluate 的返回（未找到快照 / 不在待牌 / 振听 / 无役 / deal_in_hit）
"""
import sys
import sqlite3
import gzip
import argparse

def _get_raw_content(content):
    if isinstance(content, bytes):
        try:
            return gzip.decompress(content).decode("utf-8")
        except Exception:
            return content.decode("utf-8", errors="replace")
    return content

def main():
    ap = argparse.ArgumentParser(description="即时铳率诊断")
    ap.add_argument("--limit", type=int, default=20, help="检查前 N 条牌谱")
    ap.add_argument("--pattern", type=str, default="3s-1s", help="舍牌模式，如 3s-1s")
    ap.add_argument("--target", type=str, default="3p", help="目标牌，如 3p")
    args = ap.parse_args()

    root = __import__("pathlib").Path(__file__).parent.parent
    sys.path.insert(0, str(root))

    try:
        import mahjong  # noqa: F401
        print("mahjong 库: 已安装")
    except ImportError:
        print("*** mahjong 库未安装！即时铳率依赖 mahjong 做听牌/役种判定，请运行: pip install mahjong")
        return 1

    import configparser
    cfg = configparser.ConfigParser()
    cfg_path = root / "config.ini"
    if cfg_path.exists():
        cfg.read(cfg_path, encoding="utf-8")
    db_path = root / "data" / "tenhou.db"
    if cfg.has_option("Database", "path"):
        db_path = __import__("pathlib").Path(cfg.get("Database", "path").strip())
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return 1

    from src.live_analyzer import parse_log_to_game_states, _raw_to_tenhou6_for_instant
    from src.instant_deal_in import extract_tenhou6_rounds, RoundInstantDealInAnalyzer
    from src.equivalent_variants import generate_equivalent_variants, match_discard_to_variant, split_discard_pattern
    from src.mjlog_parser import MjlogParser

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        SELECT id, COALESCE(NULLIF(log_json, ''), log) FROM logs
        WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '')
        ORDER BY id DESC LIMIT ?
    """, (args.limit,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("无牌谱数据")
        return 1

    pattern_str = args.pattern
    target = args.target
    pattern_list = split_discard_pattern(pattern_str)
    if not pattern_list:
        print(f"无效模式: {pattern_str}")
        return 1
    variants = generate_equivalent_variants(pattern_list, target, None, None, None)
    stats = {"round_payloads_empty": 0, "analyzer_ok": 0, "matches": 0, "snapshot_miss": 0, "not_in_waits": 0, "furiten": 0, "no_yaku": 0, "deal_in_hit": 0}

    for log_id, content in rows:
        raw = _get_raw_content(content)
        tenhou6_raw = _raw_to_tenhou6_for_instant(raw)
        round_payloads = extract_tenhou6_rounds(tenhou6_raw)
        game_states = parse_log_to_game_states(raw)

        if not round_payloads:
            stats["round_payloads_empty"] += 1
            print(f"[{log_id}] round_payloads 为空 (raw 前80字符: {repr(raw[:80])})")
            continue

        round_size = 4
        for round_start in range(0, min(len(game_states), len(round_payloads) * round_size), round_size):
            round_players = game_states[round_start:round_start + round_size]
            if len(round_players) < round_size:
                break
            round_idx = round_start // round_size
            round_payload = round_payloads[round_idx] if round_idx < len(round_payloads) else None
            if not round_payload:
                continue

            rp_data, rp_events = round_payload
            g0 = round_players[0]
            try:
                analyzer = RoundInstantDealInAnalyzer(rp_data, rp_events, g0.round_num, g0.oya)
            except Exception as e:
                print(f"[{log_id}] RoundInstantDealInAnalyzer 初始化失败: {e}")
                continue

            stats["analyzer_ok"] += 1
            oya = g0.oya
            for player_state in round_players:
                in_range = list(enumerate(player_state.discards))
                if not in_range:
                    continue
                discard_riichi_flags = [getattr(in_range[i][1], "is_riichi_declaration", False) for i in range(len(in_range))]
                discards_precomputed = [(MjlogParser.tile_to_string(d.tile), d.is_tsumogiri) for _, d in in_range]
                honor_ctx_base = {
                    "jikaze": MjlogParser.get_jikaze(player_state.player_id, oya, g0.round_num),
                    "bakaze": ["东", "南", "西", "北"][g0.round_num // 4],
                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, oya, g0.round_num),
                    "calls": getattr(player_state, "calls", []),
                    "visible_tiles": player_state.visible_tiles,
                    "dora_indicators": g0.dora_indicators,
                }
                for j, (orig_i, discard) in enumerate(in_range):
                    full_discards = discards_precomputed[: j + 1]
                    honor_ctx = {**honor_ctx_base, "current_discard_turn": discard.turn, "discard_riichi_flags": discard_riichi_flags[: j + 1]}
                    mv = match_discard_to_variant(full_discards, variants, honor_ctx)
                    if not mv:
                        continue
                    stats["matches"] += 1
                    mapped_target = mv["target"]
                    mapped_str = mapped_target if isinstance(mapped_target, str) else (mapped_target[0] if mapped_target else None)
                    if not mapped_str:
                        continue
                    ev = analyzer.evaluate(player_state.player_id, discard.turn, mapped_str)
                    reason = ev.get("furiten_reason", "")
                    if ev.get("deal_in_hit"):
                        stats["deal_in_hit"] += 1
                        print(f"[{log_id}] MATCH deal_in! pid={player_state.player_id} turn={discard.turn} target={mapped_str} ev={ev}")
                    elif "未找到当巡快照" in reason:
                        stats["snapshot_miss"] += 1
                        if stats["snapshot_miss"] <= 2:
                            print(f"[{log_id}] snapshot miss pid={player_state.player_id} turn={discard.turn} keys={list(analyzer.snapshots.keys())[:5]}...")
                    elif "目标牌不在当时待牌" in reason:
                        stats["not_in_waits"] += 1
                    elif "振听" in reason or ev.get("furiten_state") != "none":
                        stats["furiten"] += 1
                    elif "无役" in reason or ev.get("deal_in_point", 0) <= 0:
                        stats["no_yaku"] += 1

                    if stats["matches"] <= 5:
                        waits = ev.get("waits_snapshot", [])
                        print(f"[{log_id}] match pid={player_state.player_id} turn={discard.turn} target={mapped_str} waits={waits[:8]}{'...' if len(waits)>8 else ''} -> {reason or ev.get('furiten_state', 'ok')}")

    print("\n=== 汇总 ===")
    print(f"round_payloads 为空: {stats['round_payloads_empty']} 条")
    print(f"analyzer 创建成功: {stats['analyzer_ok']} 局")
    print(f"模式匹配数: {stats['matches']} (pattern={pattern_str} target={target})")
    print(f"未找到快照: {stats['snapshot_miss']}")
    print(f"目标不在待牌: {stats['not_in_waits']}")
    print(f"振听: {stats['furiten']}")
    print(f"无役/点0: {stats['no_yaku']}")
    print(f"deal_in_hit: {stats['deal_in_hit']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
