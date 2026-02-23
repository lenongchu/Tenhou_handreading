#!/usr/bin/env python3
"""
验证脚本：记录「3m-1m, 目标2m, 1-6巡, 宝牌无关, 无立直, 无副露」且手牌有1张2m的对局样本

用于人工核对概率分布是否正确。输出前10条，以可读格式展示。
"""
import sqlite3
import gzip
import sys
from pathlib import Path

root = Path(__file__).parent
sys.path.insert(0, str(root))

from src.mjlog_parser import MjlogParser
from src.live_analyzer import parse_log_to_game_states
from src.simple_normalizer import generate_equivalent_variants, match_discard_to_variant


def tiles_to_readable(tiles: list, highlight_target: str = None) -> str:
    """将手牌编码列表转为可读字符串，按万筒索字排序，可选高亮目标牌"""
    if not tiles:
        return "（空）"
    base_to_str = {}
    for t in tiles:
        base = t // 4
        s = MjlogParser.tile_to_string(t)
        if base not in base_to_str:
            base_to_str[base] = []
        base_to_str[base].append(s)
    # 按 万0-8, 筒9-17, 索18-26, 字27-33 排序
    parts = []
    for base in range(34):
        if base in base_to_str:
            for s in sorted(base_to_str[base]):
                if highlight_target and s == highlight_target:
                    parts.append(f"[{s}]")  # 高亮目标牌
                else:
                    parts.append(s)
    return " ".join(parts)


def visible_count_for_tile(visible_tiles: dict, tile_str: str) -> int:
    """统计某牌在可见牌中的数量"""
    base = MjlogParser.string_to_tile(tile_str)
    return sum(c for t, c in visible_tiles.items() if t // 4 == base)


def main():
    db_path = Path(__file__).parent / "data" / "tenhou.db"
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        sys.exit(1)

    # 参数：3m-1m, 目标2m, 1-6巡, 宝牌无关, 无立直, 无副露
    query_pattern = ["3m", "1m"]
    target_tile = "2m"
    turn_range = (1, 6)
    dora_constraint = "dora_unrelated"
    riichi_constraint = "no_riichi"
    call_constraint = "no_call"

    variants = generate_equivalent_variants(query_pattern, target_tile, None)

    samples = []
    conn = sqlite3.connect(str(db_path), timeout=60)
    cur = conn.cursor()

    cur.execute("SELECT id, log FROM logs WHERE log IS NOT NULL AND log != ''")
    total = 0

    for log_id, xml_content_compressed in cur:
        try:
            if isinstance(xml_content_compressed, bytes):
                xml_content = gzip.decompress(xml_content_compressed).decode("utf-8")
            else:
                xml_content = xml_content_compressed

            game_states = parse_log_to_game_states(xml_content_compressed)
            round_size = 4

            for round_start in range(0, len(game_states), round_size):
                round_players = game_states[round_start : round_start + round_size]
                if len(round_players) < round_size:
                    break

                dora_str = None
                if round_players[0].dora_indicators:
                    dora_str = MjlogParser.tile_to_string(
                        round_players[0].dora_indicators[0]
                    )

                for player_state in round_players:
                    min_turn, max_turn = turn_range
                    in_range = [
                        (i, d)
                        for i, d in enumerate(player_state.discards)
                        if min_turn <= d.turn <= max_turn
                    ]
                    if not in_range:
                        continue

                    discards_precomputed = [
                        (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                        for _, d in in_range
                    ]
                    discarded_bases = set()

                    for j, (orig_i, discard) in enumerate(in_range):
                        discarded_bases.add(discard.tile // 4)
                        full_discards_up_to_now = discards_precomputed[: j + 1]
                        hand_discard_strings = [
                            t for t, ts in full_discards_up_to_now if not ts
                        ]
                        if not hand_discard_strings:
                            continue

                        matched_variant = match_discard_to_variant(
                            full_discards_up_to_now, variants
                        )
                        if not matched_variant:
                            continue

                        if dora_constraint == "dora_unrelated" and dora_str:
                            pattern_suit = None
                            for elem in matched_variant["discard"]:
                                t = elem[0] if isinstance(elem, tuple) else elem
                                if len(t) >= 2 and t[-1] in "mps":
                                    pattern_suit = t[-1]
                                    break
                            if pattern_suit and dora_str[-1] == pattern_suit:
                                continue

                        if riichi_constraint == "no_riichi" and discard.riichi_happened:
                            continue
                        if call_constraint == "no_call" and discard.call_happened:
                            continue

                        if orig_i < len(player_state.hand_tiles_history):
                            hand_at_turn = player_state.hand_tiles_history[orig_i]
                        else:
                            hand_at_turn = player_state.hand_tiles

                        mapped_target = matched_variant["target"]
                        mapped_target_code = MjlogParser.string_to_tile(mapped_target)

                        if mapped_target_code in discarded_bases:
                            continue

                        target_count = sum(
                            1
                            for tile in hand_at_turn
                            if tile // 4 == mapped_target_code
                        )
                        target_count = min(target_count, 3)

                        # 只记录 target_count == 1 的样本
                        if target_count != 1:
                            continue

                        # 宝牌
                        dora_readable = (
                            "".join(
                                MjlogParser.tile_to_string(d)
                                for d in round_players[0].dora_indicators[:5]
                            )
                            if round_players[0].dora_indicators
                            else "（无）"
                        )

                        visible_2m = visible_count_for_tile(
                            dict(player_state.visible_tiles), mapped_target
                        )

                        samples.append(
                            {
                                "log_id": log_id,
                                "round_num": player_state.round_num,
                                "honba": player_state.honba,
                                "oya": player_state.oya,
                                "player_id": player_state.player_id,
                                "turn": discard.turn,
                                "actual_pattern": hand_discard_strings.copy(),
                                "mapped_target": mapped_target,
                                "hand_tiles": list(hand_at_turn),
                                "visible_tiles": dict(player_state.visible_tiles),
                                "dora_readable": dora_readable,
                                "visible_target": visible_2m,
                                "target_count": target_count,
                            }
                        )

                        if len(samples) >= 10:
                            break

                if len(samples) >= 10:
                    break

        except Exception as e:
            continue

        if len(samples) >= 10:
            break

    conn.close()

    # 可读输出（同时写入文件，避免控制台编码问题）
    out_lines = []
    def out(s=""):
        out_lines.append(s)
        print(s)

    out("=" * 80)
    out("验证样本：3m-1m → 2m，1-6巡，宝牌无关，无立直，无副露，手牌恰有1张2m")
    out("=" * 80)
    out()

    for i, s in enumerate(samples, 1):
        hand_str = tiles_to_readable(s["hand_tiles"], s["mapped_target"])
        round_display = MjlogParser.format_round_display(s["round_num"], s["honba"])
        wind = MjlogParser.get_player_wind(s["player_id"], s.get("oya", 0))
        out(f"【样本 {i}】")
        out(f"  对局ID:     {s['log_id']}")
        out(f"  天凤牌谱:   https://tenhou.net/0/?log={s['log_id']}")
        out(f"  小局/本场:  {round_display}")
        out(f"  目标玩家:   {wind}家 ({s['player_id']}号)")
        out(f"  巡目:       第{s['turn']}巡")
        out(f"  宝牌:       {s['dora_readable']}")
        out(f"  舍牌序列:   {' '.join(s['actual_pattern'])}  (仅手切)")
        out(f"  目标牌:     {s['mapped_target']} (应有{s.get('target_count', 1)}张在手牌)")
        out(f"  手牌({len(s['hand_tiles'])}张): {hand_str}")
        out(f"  可见{s['mapped_target']}: {s['visible_target']}张 (他家舍牌+宝牌指示物等)")
        out()
    out("=" * 80)
    out(f"共记录 {len(samples)} 条样本，请据此人工核对牌谱。")
    out("=" * 80)

    # 写入文件（UTF-8）
    out_path = root / "verify_samples.txt"
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"\n结果已保存至: {out_path}")


if __name__ == "__main__":
    main()
