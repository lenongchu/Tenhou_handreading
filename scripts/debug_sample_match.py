#!/usr/bin/env python3
"""
诊断脚本：追踪为何某样本被采纳。
用法: python -m scripts.debug_sample_match <log_id> [round_num] [honba] [player_wind]

 round_num: 0=东1 1=东2 2=东3 3=东4 4=南1...
 honba: 本场数
 player_wind: 东/南/西/北（样本中的「目标玩家」）

示例: python -m scripts.debug_sample_match 2026020523gm-00e1-0000-8b5c0dbf 1 1 东
      (东2局 1本场 东家，对应样本)
"""
import sys
import sqlite3
import gzip

def _get_raw_content(content):
    if isinstance(content, bytes):
        try:
            return gzip.decompress(content).decode("utf-8")
        except Exception:
            return content.decode("utf-8", errors="replace")
    return content

def main():
    if len(sys.argv) < 2:
        print("用法: python -m scripts.debug_sample_match <log_id> [round_num] [honba] [player_wind]")
        print("  round_num: 0=东1 1=东2 ... ; honba: 本场; player_wind: 东/南/西/北")
        print("示例: python -m scripts.debug_sample_match 2026020523gm-00e1-0000-8b5c0dbf 1 1 东")
        return 1
    log_id = sys.argv[1]
    want_round_num = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    want_honba = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    want_wind = (sys.argv[4] if len(sys.argv) > 4 else "东").strip()

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from src.live_analyzer import parse_log_to_game_states
    from src.equivalent_variants import match_discard_to_variant, generate_equivalent_variants
    from src.mjlog_parser import MjlogParser

    db_path = __import__("pathlib").Path(__file__).parent.parent / "data" / "tenhou.db"
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT id, COALESCE(NULLIF(log_json, ''), log) FROM logs WHERE id = ?", (log_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT id, COALESCE(NULLIF(log_json, ''), log) FROM logs WHERE id LIKE ? LIMIT 1", (f"%{log_id}%",))
        row = cur.fetchone()
    conn.close()
    if not row:
        print(f"未找到 log_id={log_id}")
        return 1

    _, content = row
    raw = _get_raw_content(content)
    game_states = parse_log_to_game_states(raw)
    if not game_states:
        print("解析失败")
        return 1

    round_size = 4
    found = None
    for r in range(0, len(game_states), round_size):
        rp = game_states[r : r + round_size]
        if len(rp) < 4:
            break
        g0 = rp[0]
        if getattr(g0, "round_num", -1) == want_round_num and getattr(g0, "honba", -1) == want_honba:
            oya = getattr(g0, "oya", 0)
            winds = ["东", "南", "西", "北"]
            for i, p in enumerate(rp):
                w = MjlogParser.get_player_wind(p.player_id, oya)
                if w == want_wind:
                    found = (rp, i, oya)
                    break
            if found:
                break
    if not found:
        print(f"未找到 round_num={want_round_num} honba={want_honba} {want_wind}家")
        print("该牌谱中的局：")
        for r in range(0, min(len(game_states), 16), round_size):
            rp = game_states[r : r + round_size]
            if len(rp) < 4:
                break
            g0 = rp[0]
            rn = getattr(g0, "round_num", -1)
            hb = getattr(g0, "honba", -1)
            oy = getattr(g0, "oya", 0)
            disp = MjlogParser.format_round_display(rn, hb)
            winds = [MjlogParser.get_player_wind(p.player_id, oy) for p in rp]
            print(f"  {disp}: 东={winds[0]} 南={winds[1]} 西={winds[2]} 北={winds[3]}")
        return 1

    round_players, player_idx, oya = found
    ps = round_players[player_idx]
    discards = ps.discards
    round_disp = MjlogParser.format_round_display(want_round_num, want_honba)
    wind = MjlogParser.get_player_wind(ps.player_id, oya)
    print(f"=== Log {log_id} {round_disp} {wind}家 ===")
    print(f"舍牌数: {len(discards)}")
    for i, d in enumerate(discards[:10]):
        ts = MjlogParser.tile_to_string(d.tile)
        tsumo = "摸切" if d.is_tsumogiri else "手切"
        print(f"  [{i}] 巡{d.turn}: {ts} ({tsumo})")
    if len(discards) > 10:
        print(f"  ... 共 {len(discards)} 张")

    in_range = list(enumerate(discards))
    discard_riichi_flags = [getattr(in_range[i][1], "is_riichi_declaration", False) for i in range(len(in_range))]
    discards_precomputed = [(MjlogParser.tile_to_string(d.tile), d.is_tsumogiri) for _, d in in_range]
    print("\n--- 逐巡匹配 NOTm-2s → 4s ---")
    variants = generate_equivalent_variants(["NOTm", "2s"], "4s", None)
    for j in range(len(in_range)):
        full_discards = discards_precomputed[: j + 1]
        hand_strs = []
        for i, (t, ts) in enumerate(full_discards):
            if i < len(discard_riichi_flags) and discard_riichi_flags[i]:
                hand_strs.append(f"{t}r")
            elif ts:
                hand_strs.append(f"{t}t")
            else:
                hand_strs.append(t)
        mv = match_discard_to_variant(full_discards, variants, {})
        status = f"target={mv['target']}" if mv else "不匹配"
        print(f"  巡{j+1} 舍牌={hand_strs} -> {status}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
