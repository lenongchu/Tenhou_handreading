"""
实时查询引擎 - 按需分析模式
直接从 logs 表读取完整牌谱数据并实时解析分析

解析采用 tenhou6 格式（tenhou-paifu-to-json），支持副露等完整信息。
"""
import os
import time
import sqlite3
import gzip
import json
from concurrent.futures import ProcessPoolExecutor
from typing import List, Dict, Optional, Tuple, Callable, Union
from collections import Counter
import logging

from .mjlog_parser import MjlogParser, GameState
from .tenpai_utils import is_tenpai
from .simple_normalizer import (
    generate_equivalent_variants,
    get_forbidden_bases_from_exclusion_str,
    match_discard_to_variant,
    parse_target_tiles,
    parse_multi_targets,
    get_consumed_search_patterns,
    log_contains_consumed,
    round_has_matching_consumed,
    player_has_matching_consumed,
    player_satisfies_call_area_constraints,
    round_could_satisfy_call_constraints,
    player_could_satisfy_call_area_constraints,
    pattern_has_riichi,
)

logger = logging.getLogger(__name__)

# 每批从数据库读取的对局数。越大则 SQL 次数越少、略快，但单批内存线性增加（约 1.2GB/1000 条，5000 条约 6GB）
ANALYSIS_BATCH_SIZE = 4000
# 主统计时最多保留的匹配状态条数（仅用于返回给界面，超出部分不保留，避免内存持续增长）
MATCHED_STATES_CAP = 100


def _get_raw_content(content: Union[bytes, str]) -> str:
    """将 log 内容（可能 gzip）转为 UTF-8 字符串"""
    if isinstance(content, bytes):
        try:
            return gzip.decompress(content).decode("utf-8")
        except Exception:
            return content.decode("utf-8", errors="replace")
    return content


def _is_tenhou6_json(raw: str) -> bool:
    """判断是否为 tenhou6 JSON（可做副露字符串搜索）"""
    s = raw.strip()
    return s.startswith("{") and "games" in raw


def _game_at_index_contains_consumed(raw: str, game_index: int, consumed_search) -> bool:
    """
    检查 raw（tenhou6 JSON）中第 game_index 个小局的原始内容是否包含 consumed。
    通过提取该小局的 JSON 片段进行字符串搜索，避免解析/结构问题导致的跨局污染。
    """
    try:
        data = json.loads(raw)
        games = data.get("games", [])
        if game_index >= len(games):
            return False
        game_str = json.dumps(games[game_index], ensure_ascii=False)
        return log_contains_consumed(game_str, consumed_search)
    except Exception:
        return False


def _ensure_log_json_column(conn) -> None:
    """确保 logs 表有 log_json 列（用于 tenhou6 格式）"""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(logs)")
    columns = [row[1] for row in cur.fetchall()]
    if "log_json" not in columns:
        try:
            cur.execute("ALTER TABLE logs ADD COLUMN log_json BLOB")
            conn.commit()
        except Exception as e:
            logger.debug(f"添加 log_json 列: {e}")


def parse_log_to_game_states(content: Union[bytes, str]) -> List[GameState]:
    """
    解析对局内容为 GameState 列表。
    使用 tenhou6 格式解析（XML 经 tenhou-paifu-to-json 转为 JSON 后解析）。

    Args:
        content: gzip 压缩的 bytes，或 XML/JSON 字符串

    Returns:
        List[GameState]
    """
    raw: str
    if isinstance(content, bytes):
        try:
            raw = gzip.decompress(content).decode("utf-8")
        except Exception:
            raw = content.decode("utf-8", errors="replace")
    else:
        raw = content

    stripped = raw.strip()
    if stripped.startswith("{") and "games" in raw:
        try:
            from .tenhou6_adapter import parse_tenhou6_json
            data = json.loads(raw)
            return parse_tenhou6_json(data)
        except Exception as e:
            logger.warning(f"tenhou6 JSON 解析失败: {e}")
            return []

    # XML：通过 tenhou-paifu-to-json 转为 tenhou6 JSON 后解析
    try:
        from .tenhou6_adapter import xml_to_tenhou6_json, load_tenhou6_json
        json_str = xml_to_tenhou6_json(raw)
        if json_str:
            return load_tenhou6_json(json_str)
    except Exception as e:
        logger.warning(f"XML→tenhou6 转换失败: {e}")

    return []


def _process_one_log_grid(task: Tuple) -> Dict:
    """
    Per-log 并行 worker：处理单条牌谱，返回该 log 对各格的增量统计。
    供 ProcessPoolExecutor 调用，必须是模块级函数以支持 pickle。
    """
    log_id, log_content, params = task
    patterns = params["patterns"]
    grid_meta = params["grid_meta"]
    dora_constraint = params.get("dora_constraint")
    riichi_constraint = params.get("riichi_constraint")
    call_constraint = params.get("call_constraint")
    call_area_constraints = params.get("call_area_constraints")
    consumed_search_list = params.get("consumed_search_list", [])
    exclude_south4 = params.get("exclude_south4", False)
    riichi_any = params.get("riichi_any", False)
    use_tenpai = params.get("use_tenpai", False)
    prior_discard_exclusion = params.get("prior_discard_exclusion")

    def _target_key(tiles: List[str], is_combo: bool) -> str:
        return "".join(sorted(tiles)) if is_combo else tiles[0]

    result = {}  # (tr_idx, pat_idx) -> {0: n, 1: n, ...}

    try:
        raw = _get_raw_content(log_content)
        if riichi_any and _is_tenhou6_json(raw):
            if "riichi" not in raw and "reach" not in raw:
                return {}
        if consumed_search_list and _is_tenhou6_json(raw):
            if not any(log_contains_consumed(raw, cs) for cs in consumed_search_list):
                return {}

        game_states = parse_log_to_game_states(raw)
        round_size = 4

        for round_start in range(0, len(game_states), round_size):
            round_players = game_states[round_start:round_start + round_size]
            if len(round_players) < round_size:
                break
            if exclude_south4 and getattr(round_players[0], "round_num", 0) == 7:
                continue
            oya = getattr(round_players[0], "oya", 0)
            dora_str = None
            if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                if dora_constraint != "dora_unrelated" and dora_str != dora_constraint:
                    continue
            if consumed_search_list:
                if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                    continue
            if call_constraint or call_area_constraints:
                if not round_could_satisfy_call_constraints(
                    round_players, call_constraint, call_area_constraints, oya
                ):
                    continue
            if riichi_any:
                if not any(
                    any(getattr(d, "is_riichi_declaration", False) for d in p.discards)
                    for p in round_players
                ):
                    continue

            for player_state in round_players:
                if call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
                    continue
                if call_area_constraints and not player_could_satisfy_call_area_constraints(
                    player_state, oya, call_area_constraints
                ):
                    continue

                all_discards = [
                    (i, d) for i, d in enumerate(player_state.discards)
                    if hasattr(d, "turn") and hasattr(d, "tile")
                ]
                if not all_discards:
                    continue
                discards_precomputed = [
                    (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                    for _, d in all_discards
                ]
                discard_riichi_flags = [
                    getattr(all_discards[i][1], "is_riichi_declaration", False)
                    for i in range(len(all_discards))
                ]
                honor_ctx_base = {
                    "jikaze": MjlogParser.get_jikaze(player_state.player_id, player_state.oya, player_state.round_num),
                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                    "calls": getattr(player_state, "calls", []),
                }

                for j, (orig_i, discard) in enumerate(all_discards):
                    discard_turn = discard.turn

                    for tr_idx, pat_idx, turn_range, vars_p, multi_t, is_combo in grid_meta:
                        min_turn, max_turn = turn_range
                        if not (min_turn <= discard_turn <= max_turn):
                            continue
                        in_range_for_cell = [
                            (idx, dd) for idx, dd in all_discards
                            if min_turn <= dd.turn <= max_turn and dd.turn <= discard_turn
                        ]
                        if not in_range_for_cell or in_range_for_cell[-1][1] != discard:
                            continue
                        full_discards_up_to_now = [
                            discards_precomputed[idx] for idx, _ in in_range_for_cell
                        ]
                        riichi_flags_for_cell = [discard_riichi_flags[idx] for idx, _ in in_range_for_cell]
                        honor_ctx = {
                            **honor_ctx_base,
                            "current_discard_turn": discard_turn,
                            "discard_riichi_flags": riichi_flags_for_cell,
                        }
                        pattern, target = patterns[pat_idx]
                        cs = get_consumed_search_patterns(pattern)
                        if cs:
                            if not round_has_matching_consumed(round_players, cs):
                                continue
                            if not player_has_matching_consumed(player_state, cs):
                                continue
                        matched_variant = match_discard_to_variant(full_discards_up_to_now, vars_p, honor_ctx)
                        if not matched_variant:
                            continue
                        if prior_discard_exclusion and turn_range:
                            first_turn_in_range = in_range_for_cell[0][1].turn
                            prior_discards = [d for d in player_state.discards if d.turn < first_turn_in_range]
                            excl_str = matched_variant.get("prior_discard_exclusion")
                            if excl_str:
                                forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                if any((d.tile // 4) in forbidden for d in prior_discards):
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
                        if riichi_constraint and riichi_constraint != "any":
                            if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                                continue
                            if riichi_constraint == "no_riichi" and discard.riichi_happened:
                                continue
                        if call_constraint and call_constraint != "any":
                            if call_constraint == "has_call" and not discard.call_happened:
                                continue
                            if call_constraint == "no_call" and discard.call_happened:
                                continue
                        if call_area_constraints:
                            if not player_satisfies_call_area_constraints(
                                player_state, round_players, player_state.oya, call_area_constraints,
                                current_discard_turn=discard.turn,
                            ):
                                continue
                        mapped_target = matched_variant["target"]
                        if not use_tenpai:
                            target_equiv = set()
                            if len(multi_t) == 1:
                                if is_combo:
                                    for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target]):
                                        target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                                else:
                                    target_equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target))
                            else:
                                target_equiv = MjlogParser.get_count_equivalent_bases(
                                    MjlogParser.string_to_tile(mapped_target if isinstance(mapped_target, str) else mapped_target[0])
                                )
                                for tiles, ic in multi_t[1:]:
                                    for t in tiles:
                                        target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                            if discard.tile // 4 in target_equiv:
                                continue

                        if orig_i < len(player_state.hand_tiles_history):
                            hand_at_turn = list(player_state.hand_tiles_history[orig_i])
                        else:
                            hand_at_turn = list(player_state.hand_tiles)
                        if use_tenpai:
                            target_count = 1 if is_tenpai(hand_at_turn) else 0
                        elif len(multi_t) > 1:
                            if multi_t[0][1]:
                                target_codes = [MjlogParser.string_to_tile(t) for t in multi_t[0][0]]
                                hand_bases = [t // 4 for t in hand_at_turn]
                                target_count = 1 if all(
                                    any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                    for c in target_codes
                                ) else 0
                            else:
                                equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(multi_t[0][0][0]))
                                target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                        elif is_combo:
                            target_codes = [MjlogParser.string_to_tile(t) for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target])]
                            hand_bases = [t // 4 for t in hand_at_turn]
                            target_count = 1 if all(
                                any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                for c in target_codes
                            ) else 0
                        else:
                            equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target))
                            target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)

                        k = (tr_idx, pat_idx)
                        if k not in result:
                            result[k] = {0: 0, 1: 0, 2: 0, 3: 0} if not (use_tenpai or is_combo) else {0: 0, 1: 0}
                        if target_count in result[k]:
                            result[k][target_count] += 1
                        else:
                            result[k][target_count] = 1
    except Exception as e:
        logger.error(f"解析对局 {log_id} 失败: {e}")
    return result


def _process_one_log_analyze(task: Tuple) -> Dict:
    """
    Per-log 并行 worker：主界面分析，处理单条牌谱，返回该 log 的增量统计。
    供 ProcessPoolExecutor 调用，必须是模块级函数以支持 pickle。
    """
    log_id, log_content, params = task
    items = params["items"]
    item_variants = params["item_variants"]
    item_multi_targets = params["item_multi_targets"]
    turn_range = params.get("turn_range")
    dora_constraint = params.get("dora_constraint")
    visible_constraints = params.get("visible_constraints")
    riichi_constraint = params.get("riichi_constraint")
    call_constraint = params.get("call_constraint")
    call_area_constraints = params.get("call_area_constraints")
    consumed_search_list = params.get("consumed_search_list", [])
    exclude_south4 = params.get("exclude_south4", False)
    riichi_any = params.get("riichi_any", False)
    prior_discard_exclusion = params.get("prior_discard_exclusion")
    use_tenpai = params.get("use_tenpai", False)
    cap = params.get("cap", MATCHED_STATES_CAP)
    SAMPLE_POOL_CAP = 10000

    def _target_key(tiles: List[str], is_combo: bool) -> str:
        return "".join(sorted(tiles)) if is_combo else tiles[0]

    total_matches = 0
    pattern_matches = [0] * len(items)
    if params.get("multi_target"):
        pattern_distributions = [
            {_target_key(t[0], t[1]): ({0: 0, 1: 0} if (use_tenpai or t[1]) else {0: 0, 1: 0, 2: 0, 3: 0})
             for t in item_multi_targets[idx]}
            for idx in range(len(items))
        ]
    else:
        pattern_distributions = [
            ({0: 0, 1: 0} if (use_tenpai or iv[2]) else {0: 0, 1: 0, 2: 0, 3: 0})
            for iv in item_variants
        ]
    matched_states = []
    sample_pool = []

    try:
        raw = _get_raw_content(log_content)
        if riichi_any and _is_tenhou6_json(raw):
            if "riichi" not in raw and "reach" not in raw:
                return {"total_matches": 0, "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                        "matched_states": [], "sample_pool": []}
        if consumed_search_list and _is_tenhou6_json(raw):
            if len(items) == 1:
                if not log_contains_consumed(raw, consumed_search_list[0]):
                    return {"total_matches": 0, "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                            "matched_states": [], "sample_pool": []}
            else:
                if not any(log_contains_consumed(raw, cs) for cs in consumed_search_list):
                    return {"total_matches": 0, "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                            "matched_states": [], "sample_pool": []}
        game_states = parse_log_to_game_states(raw)
        round_size = 4

        for round_start in range(0, len(game_states), round_size):
            round_players = game_states[round_start:round_start + round_size]
            if len(round_players) < round_size:
                break
            if exclude_south4 and getattr(round_players[0], "round_num", 0) == 7:
                continue
            oya = getattr(round_players[0], "oya", 0)
            dora_str = None
            if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                if dora_constraint != "dora_unrelated" and dora_str != dora_constraint:
                    continue
            if consumed_search_list:
                if len(items) == 1:
                    if not round_has_matching_consumed(round_players, consumed_search_list[0]):
                        continue
                else:
                    if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                        continue
            if call_constraint or call_area_constraints:
                if not round_could_satisfy_call_constraints(round_players, call_constraint, call_area_constraints, oya):
                    continue
            if riichi_any:
                if not any(any(getattr(d, 'is_riichi_declaration', False) for d in p.discards) for p in round_players):
                    continue

            for player_state in round_players:
                if call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
                    continue
                if call_area_constraints and not player_could_satisfy_call_area_constraints(player_state, oya, call_area_constraints):
                    continue
                if turn_range:
                    min_turn, max_turn = turn_range
                    in_range = [(i, d) for i, d in enumerate(player_state.discards) if min_turn <= d.turn <= max_turn]
                else:
                    in_range = [(i, d) for i, d in enumerate(player_state.discards)]
                if not in_range:
                    continue

                discards_precomputed = [(MjlogParser.tile_to_string(d.tile), d.is_tsumogiri) for _, d in in_range]
                honor_ctx_base = {
                    "jikaze": MjlogParser.get_jikaze(player_state.player_id, player_state.oya, player_state.round_num),
                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                    "calls": getattr(player_state, "calls", []),
                }
                discard_riichi_flags = [getattr(in_range[i][1], 'is_riichi_declaration', False) for i in range(len(in_range))]

                for j, (orig_i, discard) in enumerate(in_range):
                    full_discards_up_to_now = discards_precomputed[:j + 1]
                    hand_discard_strings = []
                    for i, (t, ts) in enumerate(full_discards_up_to_now):
                        if i < len(discard_riichi_flags) and discard_riichi_flags[i]:
                            hand_discard_strings.append(f"{t}r")
                        elif ts:
                            hand_discard_strings.append(f"{t}t")
                        else:
                            hand_discard_strings.append(t)

                    honor_ctx = {**honor_ctx_base, "current_discard_turn": discard.turn, "discard_riichi_flags": discard_riichi_flags[: j + 1]}
                    matched_variant = None
                    matched_idx = -1
                    for idx, (vars_p, _, _) in enumerate(item_variants):
                        cs = get_consumed_search_patterns(items[idx][0])
                        if cs:
                            if not round_has_matching_consumed(round_players, cs):
                                continue
                            if not player_has_matching_consumed(player_state, cs):
                                continue
                        mv = match_discard_to_variant(full_discards_up_to_now, vars_p, honor_ctx)
                        if mv:
                            matched_variant = mv
                            matched_idx = idx
                            break
                    if not matched_variant:
                        continue
                    _, _, item_combo = item_variants[matched_idx]
                    if prior_discard_exclusion and turn_range:
                        prior_discards = [d for d in player_state.discards if d.turn < in_range[0][1].turn]
                        excl_str = matched_variant.get("prior_discard_exclusion")
                        if excl_str:
                            forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                            if any((d.tile // 4) in forbidden for d in prior_discards):
                                continue
                    if dora_constraint == "dora_unrelated" and dora_str:
                        pattern_suit = None
                        for elem in matched_variant["discard"]:
                            t = elem[0] if isinstance(elem, tuple) else elem
                            if len(t) >= 2 and t[-1] in 'mps':
                                pattern_suit = t[-1]
                                break
                        if pattern_suit and dora_str[-1] == pattern_suit:
                            continue
                    if riichi_constraint and riichi_constraint != "any":
                        if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                            continue
                        if riichi_constraint == "no_riichi" and discard.riichi_happened:
                            continue
                    if call_constraint and call_constraint != "any":
                        if call_constraint == "has_call" and not discard.call_happened:
                            continue
                        if call_constraint == "no_call" and discard.call_happened:
                            continue
                    if call_area_constraints:
                        if not player_satisfies_call_area_constraints(
                            player_state, round_players, player_state.oya, call_area_constraints,
                            current_discard_turn=discard.turn,
                        ):
                            continue
                    vc = matched_variant["visible_constraints"]
                    if vc:
                        match_visible = True
                        for tile_str, (min_count, max_count) in vc.items():
                            base_code = MjlogParser.string_to_tile(tile_str)
                            equiv_bases = MjlogParser.get_count_equivalent_bases(base_code)
                            count = sum(c for t, c in player_state.visible_tiles.items() if t // 4 in equiv_bases)
                            if not (min_count <= count <= max_count):
                                match_visible = False
                                break
                        if not match_visible:
                            continue
                    mapped_target = matched_variant["target"]
                    mt_for_item = item_multi_targets[matched_idx]
                    if not use_tenpai:
                        target_equiv = set()
                        if len(mt_for_item) == 1:
                            if item_combo:
                                for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target]):
                                    target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                            else:
                                target_equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target))
                        else:
                            target_equiv = MjlogParser.get_count_equivalent_bases(
                                MjlogParser.string_to_tile(mapped_target if isinstance(mapped_target, str) else mapped_target[0]))
                            for tiles, is_combo in mt_for_item[1:]:
                                for t in tiles:
                                    target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                        if discard.tile // 4 in target_equiv:
                            continue

                    total_matches += 1
                    pattern_matches[matched_idx] += 1
                    if orig_i < len(player_state.hand_tiles_history):
                        hand_at_turn = list(player_state.hand_tiles_history[orig_i])
                    else:
                        hand_at_turn = list(player_state.hand_tiles)
                    mt_item = item_multi_targets[matched_idx]
                    if use_tenpai:
                        target_count = 1 if is_tenpai(hand_at_turn) else 0
                        target_counts = None
                    elif len(mt_item) > 1:
                        target_counts = {}
                        for tiles, is_combo in mt_item:
                            k = _target_key(tiles, is_combo)
                            if is_combo:
                                target_codes = [MjlogParser.string_to_tile(t) for t in tiles]
                                hand_bases = [t // 4 for t in hand_at_turn]
                                target_counts[k] = 1 if all(
                                    any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                    for c in target_codes
                                ) else 0
                            else:
                                equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(tiles[0]))
                                target_counts[k] = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                        target_count = target_counts.get(_target_key(mt_item[0][0], mt_item[0][1]), 0)
                    elif item_combo:
                        target_codes = [MjlogParser.string_to_tile(t) for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target])]
                        hand_bases = [t // 4 for t in hand_at_turn]
                        target_count = 1 if all(
                            any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                            for c in target_codes
                        ) else 0
                        target_counts = None
                    else:
                        equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target))
                        target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                        target_counts = None
                    if len(mt_item) > 1 and target_counts:
                        for k, cnt in target_counts.items():
                            pattern_distributions[matched_idx][k][cnt] = pattern_distributions[matched_idx][k].get(cnt, 0) + 1
                    else:
                        pattern_distributions[matched_idx][target_count] += 1

                    if len(matched_states) < cap:
                        ms_entry = {
                            'round_num': player_state.round_num, 'turn': discard.turn,
                            'actual_pattern': hand_discard_strings, 'mapped_target': mapped_target,
                            'hand_tiles': list(hand_at_turn), 'visible_tiles': dict(player_state.visible_tiles)
                        }
                        if target_counts is not None:
                            ms_entry['target_counts'] = target_counts
                        else:
                            ms_entry['target_count'] = target_count
                        matched_states.append(ms_entry)

                    if len(sample_pool) < SAMPLE_POOL_CAP:
                        visible_tiles_dict = dict(player_state.visible_tiles)
                        dora_readable = "".join(
                            MjlogParser.tile_to_string(d) for d in round_players[0].dora_indicators[:5]
                        ) if round_players[0].dora_indicators else "（无）"
                        if len(mt_item) > 1 and target_counts:
                            visible_target = ", ".join(f"{k}:{_visible_count(visible_tiles_dict, k)}" for k in target_counts)
                        elif item_combo and isinstance(mapped_target, list):
                            visible_target = ", ".join(f"{t}:{_visible_count(visible_tiles_dict, t)}" for t in mapped_target)
                        else:
                            visible_target = str(_visible_count(visible_tiles_dict, mapped_target if isinstance(mapped_target, str) else mapped_target[0]))
                        sp_entry = {
                            "log_id": log_id, "round_num": player_state.round_num, "honba": player_state.honba,
                            "oya": player_state.oya, "player_id": player_state.player_id, "turn": discard.turn,
                            "actual_pattern": hand_discard_strings.copy(), "mapped_target": mapped_target,
                            "hand_tiles": list(hand_at_turn), "visible_tiles": visible_tiles_dict,
                            "dora_readable": dora_readable, "visible_target": visible_target,
                            "is_combo": item_combo, "matched_pattern_idx": matched_idx,
                        }
                        if target_counts is not None:
                            sp_entry["target_counts"] = target_counts
                        else:
                            sp_entry["target_count"] = target_count
                        qp, tt = items[matched_idx]
                        ok, _ = verify_sample_consistency(sp_entry, qp, tt, visible_constraints)
                        if ok:
                            sample_pool.append(sp_entry)

    except Exception as e:
        logger.error(f"解析对局 {log_id} 失败: {e}")

    return {
        "total_matches": total_matches,
        "pattern_matches": pattern_matches,
        "pattern_distributions": pattern_distributions,
        "matched_states": matched_states,
        "sample_pool": sample_pool,
    }


def _empty_analysis_result(query_pattern: List[str], target_tile: str, pattern_results: Optional[List] = None) -> Dict:
    """用户取消时返回的空结果"""
    if target_tile:
        _, is_combo = parse_target_tiles(target_tile)
    else:
        is_combo = False
    dist = {0: 0, 1: 0} if is_combo else {0: 0, 1: 0, 2: 0, 3: 0}
    r = {
        'total_logs_analyzed': 0,
        'total_matches': 0,
        'target_count_distribution': dist,
        'probability_distribution': {k: 0.0 for k in dist},
        'query_pattern': query_pattern,
        'query_pattern_str': '-'.join(query_pattern) if query_pattern else '',
        'target_tile': target_tile,
        'turn_range': None,
        'variants_count': 0,
        'matched_states': [],
        'sample_pool': [],
        'is_combo': is_combo,
        'multi_pattern': False,
        'pattern_results': pattern_results or [],
        'elapsed_seconds': 0,
    }
    return r


class LiveAnalyzer:
    """实时分析器 - 按需解析对局数据"""
    
    def __init__(self, db_path: str):
        """
        初始化分析器

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
    
    def analyze_discard_pattern(
        self,
        query_pattern: List[str] = None,
        target_tile: str = None,
        query_items: Optional[List[Tuple[List[str], str]]] = None,
        dora_constraint: Optional[str] = None,
        visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,  # "any" | "has_call" | "no_call"
        call_area_constraints: Optional[List[str]] = None,  # 副露区域约束，最多4个 AND
        analysis_target: str = "target_count",  # "target_count"=目标牌存量, "tenpai"=是否听牌
        turn_range: Optional[Tuple[int, int]] = None,  # (min_turn, max_turn) 如 (2, 8)
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        matched_states_cap: Optional[int] = None,  # 最多保留的匹配状态条数，None 用默认 MATCHED_STATES_CAP
        analysis_batch_size: Optional[int] = None,  # 每批从数据库读取的对局数，None 用默认 ANALYSIS_BATCH_SIZE
        exclude_south4: bool = False,  # 南四局打法随点数变化大，True 时跳过南四局
        prior_discard_exclusion: Optional[str] = None,  # 前段不可打，如 NOTm、4mOR2m，与舍牌模式同步等价变换
        max_workers: Optional[int] = None,  # >1 时启用 per-log 并行，1 或 None 为串行
    ) -> Dict:
        """
        分析舍牌模式，计算目标牌在手牌中的概率。
        支持多舍牌模式：query_items 中任一匹配即计入（A or B or C）。

        Args:
            query_pattern: 单模式时的舍牌序列（与 target_tile 配套，兼容旧接口）
            target_tile: 单模式时的目标牌
            query_items: 多模式 [(pattern, target), ...]，如 [(["7s","9s"],"6s"), (["3m","4m"],"2m")]
            dora_constraint: 宝牌约束（所有模式共用）
            visible_constraints: 可见枚数约束（所有模式共用）
            riichi_constraint: 立直约束（所有模式共用）
            call_constraint: 副露约束（所有模式共用）
            turn_range: 巡目范围（所有模式共用）
            sample_limit: 最大分析对局数（None = 全部）
            progress_callback: 进度回调函数 (current, total)
            should_cancel: 取消检查函数
            total_logs_hint: 对局总数预估值
            matched_states_cap: 最多保留的匹配状态条数（用于界面展示，影响内存）
            analysis_batch_size: 每批读取对局数（越大越省 SQL 次数，但单批内存约 1.2GB/1000 条）

        Returns:
            分析结果字典；多模式时含 pattern_results 列表
        """
        t0 = time.perf_counter()
        cap = matched_states_cap if matched_states_cap is not None else MATCHED_STATES_CAP
        batch_size = analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        if query_items is not None and len(query_items) > 0:
            items = query_items
        elif query_pattern and target_tile:
            items = [(query_pattern, target_tile)]
        else:
            return _empty_analysis_result([], "")

        first_pattern, first_target = items[0]
        consumed_search = get_consumed_search_patterns(first_pattern)
        consumed_search_list: List[List] = []  # 多模式时收集各模式的 consumed，用于 log 级预过滤
        for p, _ in items:
            cs = get_consumed_search_patterns(p)
            if cs:
                consumed_search = consumed_search or cs
                consumed_search_list.append(cs)
        if not consumed_search_list:
            consumed_search_list = [consumed_search] if consumed_search else []
        riichi_any = any(pattern_has_riichi(p) for p, _ in items)

        def _target_key(tiles: List[str], is_combo: bool) -> str:
            return "".join(sorted(tiles)) if is_combo else tiles[0]

        def _target_str_for_variant(tiles: List[str], is_combo: bool) -> str:
            return "".join(tiles) if is_combo else tiles[0]

        item_variants = []
        item_multi_targets: List[List[Tuple[List[str], bool]]] = []
        for p, t in items:
            multi_t = parse_multi_targets(t)
            item_multi_targets.append(multi_t)
            first_t = multi_t[0]
            variant_target = _target_str_for_variant(first_t[0], first_t[1])
            vars_p = generate_equivalent_variants(p, variant_target, visible_constraints, prior_discard_exclusion)
            combo = first_t[1] if len(multi_t) == 1 else False
            item_variants.append((vars_p, t, combo))

        variants = item_variants[0][0]
        multi_targets = item_multi_targets[0]
        first_t0 = multi_targets[0]
        is_combo = first_t0[1] and len(multi_targets) == 1
        use_tenpai = (analysis_target == "tenpai")
        multi_target = len(multi_targets) > 1

        matched_states = []
        sample_pool: List[Dict] = []
        SAMPLE_POOL_CAP = 10000
        total_matches = 0
        pattern_matches = [0] * len(items)
        if multi_target:
            pattern_distributions = [
                {_target_key(t[0], t[1]): ({0: 0, 1: 0} if (use_tenpai or t[1]) else {0: 0, 1: 0, 2: 0, 3: 0})
                 for t in item_multi_targets[idx]}
                for idx in range(len(items))
            ]
        else:
            pattern_distributions = [
                ({0: 0, 1: 0} if (use_tenpai or iv[2]) else {0: 0, 1: 0, 2: 0, 3: 0})
                for iv in item_variants
            ]
        target_count_distribution = pattern_distributions[0]

        use_parallel = max_workers is not None and max_workers > 1
        workers = min(max(1, max_workers or 1), os.cpu_count() or 4)
        analysis_params = None
        if use_parallel:
            analysis_params = {
                "items": items,
                "item_variants": item_variants,
                "item_multi_targets": item_multi_targets,
                "turn_range": turn_range,
                "dora_constraint": dora_constraint,
                "visible_constraints": visible_constraints,
                "riichi_constraint": riichi_constraint,
                "call_constraint": call_constraint,
                "call_area_constraints": call_area_constraints,
                "consumed_search_list": consumed_search_list,
                "exclude_south4": exclude_south4,
                "riichi_any": riichi_any,
                "prior_discard_exclusion": prior_discard_exclusion,
                "use_tenpai": use_tenpai,
                "multi_target": multi_target,
                "cap": cap,
            }
            logger.info(f"开始分析（并行 workers={workers}）...")
        # total_logs 用于进度显示和串行时的日志
        if total_logs_hint is not None and total_logs_hint > 0:
            _total_logs = total_logs_hint
        else:
            _total_logs = 0
        if sample_limit:
            _total_logs = min(_total_logs, sample_limit) if _total_logs > 0 else sample_limit
        if not use_parallel:
            logger.info(f"开始分析 {_total_logs:,} 场对局...")

        if should_cancel and should_cancel():
            return _empty_analysis_result(first_pattern, first_target)

        conn = sqlite3.connect(self.db_path, timeout=60)
        cur = conn.cursor()
        _ensure_log_json_column(conn)

        # 避免 COUNT(*) 在大库上阻塞数分钟；使用 total_logs_hint 或 sample_limit
        total_logs = _total_logs

        # 批量读取并分析（用 id 游标分页，避免 OFFSET 越大越慢）
        last_id = None  # None 表示第一页；之后用 WHERE id < last_id
        processed = 0
        pool = ProcessPoolExecutor(max_workers=workers) if use_parallel else None

        while True:
            # 检查是否取消
            if should_cancel and should_cancel():
                logger.info("分析已取消")
                break
            
            # 读取批次：优先 log_json（tenhou6 JSON），若无则用 log（XML）
            if last_id is None:
                query = """
                    SELECT id, COALESCE(log_json, log) as content
                    FROM logs 
                    WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '')
                    ORDER BY id DESC
                    LIMIT ?
                """
                cur.execute(query, (batch_size,))
            else:
                query = """
                    SELECT id, COALESCE(log_json, log) as content
                    FROM logs 
                    WHERE ((log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != ''))
                      AND id < ?
                    ORDER BY id DESC
                    LIMIT ?
                """
                cur.execute(query, (last_id, batch_size))
            logs = cur.fetchall()
            
            if not logs:
                break

            if use_parallel and pool is not None:
                # 并行分支
                tasks = [(log_id, log_content, analysis_params) for log_id, log_content in logs]
                chunksize = max(1, len(tasks) // (workers * 4))
                for per_log_result in pool.map(_process_one_log_analyze, tasks, chunksize=chunksize):
                    if should_cancel and should_cancel():
                        conn.close()
                        if pool:
                            pool.shutdown(wait=False)
                        return _empty_analysis_result(first_pattern, first_target)
                    total_matches += per_log_result["total_matches"]
                    for i, n in enumerate(per_log_result["pattern_matches"]):
                        pattern_matches[i] += n
                    for idx, d_delta in enumerate(per_log_result["pattern_distributions"]):
                        for k, v in d_delta.items():
                            if isinstance(v, dict):
                                for c, n in v.items():
                                    pattern_distributions[idx][k][c] = pattern_distributions[idx][k].get(c, 0) + n
                            else:
                                pattern_distributions[idx][k] = pattern_distributions[idx].get(k, 0) + v
                    matched_states.extend(per_log_result["matched_states"])
                    sample_pool.extend(per_log_result["sample_pool"])
                    processed += 1
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                    if sample_limit and processed >= sample_limit:
                        break
            else:
                # 串行分支
                for log_id, log_content in logs:
                    if should_cancel and should_cancel():
                        conn.close()
                        if pool:
                            pool.shutdown(wait=False)
                        return _empty_analysis_result(first_pattern, first_target)
                    try:
                        raw = _get_raw_content(log_content)
                        # 立直宣言模式(r)：牌谱无立直时快速跳过
                        if riichi_any and _is_tenhou6_json(raw):
                            if "riichi" not in raw and "reach" not in raw:
                                processed += 1
                                if progress_callback and (processed <= 10 or processed % 10 == 0):
                                    progress_callback(processed, total_logs)
                                continue
                        if consumed_search_list and _is_tenhou6_json(raw):
                            if len(items) == 1:
                                if not log_contains_consumed(raw, consumed_search_list[0]):
                                    processed += 1
                                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                                        progress_callback(processed, total_logs)
                                    continue
                            else:
                                # 多模式：仅当牌谱中不包含任一模式的 consumed 时才跳过
                                if not any(log_contains_consumed(raw, cs) for cs in consumed_search_list):
                                    processed += 1
                                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                                        progress_callback(processed, total_logs)
                                    continue
                        game_states = parse_log_to_game_states(raw)
                    
                        # 按小局分组（每局 4 个玩家）
                        round_size = 4
                        # 这几行是在把一局牌谱的 game_states 按小局切分成每局 4 个玩家。
                        for round_start in range(0, len(game_states), round_size):
                            round_players = game_states[round_start:round_start + round_size]
                            if len(round_players) < round_size:
                                break
                            if exclude_south4 and getattr(round_players[0], "round_num", 0) == 7:
                                continue  # 南四局
                            oya = getattr(round_players[0], "oya", 0)

                            dora_str = None
                            if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                                dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                                # 指定宝牌：局级判断，不满足则跳过整局
                                # 目前测试，之后可能也作等价变体处理
                                if dora_constraint != "dora_unrelated" and dora_str != dora_constraint:
                                    continue

                            # 局级 consumed 预过滤：单模式用单一 consumed；多模式需至少一个模式的 consumed 存在
                            if consumed_search_list:
                                if len(items) == 1:
                                    if not round_has_matching_consumed(round_players, consumed_search_list[0]):
                                        continue
                                else:
                                    if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                                        continue

                            # 局级副露约束预过滤：call_constraint 与 call_area_constraints 若不可能满足则跳过整局
                            if call_constraint or call_area_constraints:
                                if not round_could_satisfy_call_constraints(
                                    round_players, call_constraint, call_area_constraints, oya
                                ):
                                    continue

                            # 立直宣言模式(r)：本局无人立直时跳过
                            if riichi_any:
                                round_has_riichi_decl = any(
                                    any(getattr(d, 'is_riichi_declaration', False) for d in p.discards)
                                    for p in round_players
                                )
                                if not round_has_riichi_decl:
                                    continue

                            # 分析该局每个玩家
                            for player_state in round_players:
                                # 玩家级副露约束预过滤：no_call 时该玩家有副露则跳过；call_area 时该玩家不可能满足则跳过
                                if call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
                                    continue
                                if call_area_constraints and not player_could_satisfy_call_area_constraints(player_state, oya, call_area_constraints):
                                    continue

                                # 提取巡目范围内的舍牌
                                if turn_range:
                                    min_turn, max_turn = turn_range
                                    in_range = [(i, d) for i, d in enumerate(player_state.discards)
                                                if min_turn <= d.turn <= max_turn]
                                else:
                                    in_range = [(i, d) for i, d in enumerate(player_state.discards)]
                            
                                if not in_range:
                                    continue
                            
                                # 预转换：仅对范围内舍牌
                                discards_precomputed = [
                                    (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                                    for _, d in in_range
                                ]
                                discarded_bases = set()
                                honor_ctx_base = {
                                    "jikaze": MjlogParser.get_jikaze(player_state.player_id, player_state.oya, player_state.round_num),
                                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                                    "calls": getattr(player_state, "calls", []),
                                }
                                discard_riichi_flags = [getattr(in_range[i][1], 'is_riichi_declaration', False) for i in range(len(in_range))]

                                # 遍历范围内舍牌
                                for j, (orig_i, discard) in enumerate(in_range):
                                    discarded_bases.add(discard.tile // 4)
                                    full_discards_up_to_now = discards_precomputed[:j+1]
                                    hand_discard_strings = []
                                    for i, (t, ts) in enumerate(full_discards_up_to_now):
                                        if i < len(discard_riichi_flags) and discard_riichi_flags[i]:
                                            hand_discard_strings.append(f"{t}r")
                                        elif ts:
                                            hand_discard_strings.append(f"{t}t")
                                        else:
                                            hand_discard_strings.append(t)

                                    honor_ctx = {
                                        **honor_ctx_base,
                                        "current_discard_turn": discard.turn,
                                        "discard_riichi_flags": discard_riichi_flags[: j + 1],
                                    }
                                    matched_variant = None
                                    matched_idx = -1
                                    for idx, (vars_p, _, _) in enumerate(item_variants):
                                        cs = get_consumed_search_patterns(items[idx][0])
                                        if cs:
                                            if not round_has_matching_consumed(round_players, cs):
                                                continue
                                            # 必须由本玩家完成该副露，否则会跨玩家污染（如 c0p4p 与 c0p6p 同局时）
                                            if not player_has_matching_consumed(player_state, cs):
                                                continue
                                        mv = match_discard_to_variant(full_discards_up_to_now, vars_p, honor_ctx)
                                        if mv:
                                            matched_variant = mv
                                            matched_idx = idx
                                            break
                                    if not matched_variant:
                                        continue
                                    _, _, item_combo = item_variants[matched_idx]
                                    # 前段不可打：turn < in_range[0].turn 的舍牌不得触碰禁止集合（变体已含映射后的 prior）
                                    if prior_discard_exclusion and turn_range:
                                        prior_discards = [d for d in player_state.discards if d.turn < in_range[0][1].turn]
                                        excl_str = matched_variant.get("prior_discard_exclusion")
                                        if excl_str:
                                            forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                            if any((d.tile // 4) in forbidden for d in prior_discards):
                                                continue
                                    # 宝牌约束 dora_unrelated：取决于匹配到的等价变体花色
                                    if dora_constraint == "dora_unrelated" and dora_str:
                                        pattern_suit = None
                                        for elem in matched_variant["discard"]:
                                            t = elem[0] if isinstance(elem, tuple) else elem
                                            if len(t) >= 2 and t[-1] in 'mps':
                                                pattern_suit = t[-1]
                                                break
                                        if pattern_suit and dora_str[-1] == pattern_suit:
                                            continue
                                
                                    # 立直/副露约束：以匹配序列最后一张牌打出瞬间的状态为准
                                    if riichi_constraint and riichi_constraint != "any":
                                        if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                                            continue
                                        if riichi_constraint == "no_riichi" and discard.riichi_happened:
                                            continue
                                    if call_constraint and call_constraint != "any":
                                        if call_constraint == "has_call" and not discard.call_happened:
                                            continue
                                        if call_constraint == "no_call" and discard.call_happened:
                                            continue
                                
                                    # 副露区域约束：目标玩家必须满足所有指定的副露（AND）；仅统计该巡舍牌前已完成的副露
                                    if call_area_constraints:
                                        if not player_satisfies_call_area_constraints(
                                            player_state, round_players, player_state.oya, call_area_constraints,
                                            current_discard_turn=discard.turn,
                                        ):
                                            continue
                                
                                    # 检查可见枚数约束（含宝牌指示物，mjlog_parser 已将其计入 visible_tiles）
                                    vc = matched_variant["visible_constraints"]
                                    if vc:
                                        match_visible = True
                                        for tile_str, (min_count, max_count) in vc.items():
                                            base_code = MjlogParser.string_to_tile(tile_str)
                                            equiv_bases = MjlogParser.get_count_equivalent_bases(base_code)
                                            count = sum(
                                                c for t, c in player_state.visible_tiles.items()
                                                if t // 4 in equiv_bases
                                            )
                                            if not (min_count <= count <= max_count):
                                                match_visible = False
                                                break
                                        if not match_visible:
                                            continue
                                
                                    # 排除：若本巡打出的牌就是目标牌，不计入统计（与无副露情形一致）；听牌模式无此概念，跳过
                                    mapped_target = matched_variant["target"]
                                    mt_for_item = item_multi_targets[matched_idx]
                                    if not use_tenpai:
                                        target_equiv = set()
                                        if len(mt_for_item) == 1:
                                            if item_combo:
                                                for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target]):
                                                    target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                                            else:
                                                target_equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target))
                                        else:
                                            target_equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target if isinstance(mapped_target, str) else mapped_target[0]))
                                            for tiles, is_combo in mt_for_item[1:]:
                                                for t in tiles:
                                                    target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                                        if discard.tile // 4 in target_equiv:
                                            continue
                                
                                    # 匹配成功！
                                    total_matches += 1
                                    pattern_matches[matched_idx] += 1
                                
                                    # 获取该巡打牌后的手牌快照（orig_i 为完整舍牌序列中的下标）
                                    # 必须为 list 以保留同种牌枚数，set 会合并重复导致“3张5p显示为1张”
                                    if orig_i < len(player_state.hand_tiles_history):
                                        hand_at_turn = player_state.hand_tiles_history[orig_i]
                                    else:
                                        logger.warning(f"手牌历史记录不足：巡目{orig_i+1}，历史长度{len(player_state.hand_tiles_history)}")
                                        hand_at_turn = player_state.hand_tiles
                                    hand_at_turn = list(hand_at_turn)  # 副本，且确保为 list（非 set）以保留同种牌枚数
                                
                                    # 计算目标牌 count（多目标时对每个目标分别统计）
                                    mt_item = item_multi_targets[matched_idx]
                                    if use_tenpai:
                                        target_count = 1 if is_tenpai(list(hand_at_turn)) else 0
                                        target_counts = None
                                    elif len(mt_item) > 1:
                                        target_counts = {}
                                        for tiles, is_combo in mt_item:
                                            k = _target_key(tiles, is_combo)
                                            if is_combo:
                                                target_codes = [MjlogParser.string_to_tile(t) for t in tiles]
                                                hand_bases = [t // 4 for t in hand_at_turn]
                                                target_counts[k] = 1 if all(
                                                    any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                                    for c in target_codes
                                                ) else 0
                                            else:
                                                equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(tiles[0]))
                                                target_counts[k] = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                                        target_count = target_counts.get(_target_key(mt_item[0][0], mt_item[0][1]), 0)
                                    elif item_combo:
                                        target_codes = [MjlogParser.string_to_tile(t) for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target])]
                                        hand_bases = [t // 4 for t in hand_at_turn]
                                        target_count = 1 if all(
                                            any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                            for c in target_codes
                                        ) else 0
                                        target_counts = None
                                    else:
                                        mapped_target_code = MjlogParser.string_to_tile(mapped_target)
                                        equiv = MjlogParser.get_count_equivalent_bases(mapped_target_code)
                                        target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                                        target_counts = None
                                    if len(mt_item) > 1 and target_counts:
                                        for k, cnt in target_counts.items():
                                            pattern_distributions[matched_idx][k][cnt] = pattern_distributions[matched_idx][k].get(cnt, 0) + 1
                                    else:
                                        pattern_distributions[matched_idx][target_count] += 1
                                
                                    # 记录匹配状态（仅保留前 cap 条，避免内存持续增长）
                                    if len(matched_states) < cap:
                                        ms_entry = {
                                            'round_num': player_state.round_num,
                                            'turn': discard.turn,
                                            'actual_pattern': hand_discard_strings,
                                            'mapped_target': mapped_target,
                                            'hand_tiles': list(hand_at_turn),
                                            'visible_tiles': dict(player_state.visible_tiles)
                                        }
                                        if target_counts is not None:
                                            ms_entry['target_counts'] = target_counts
                                        else:
                                            ms_entry['target_count'] = target_count
                                        matched_states.append(ms_entry)
                                
                                    # 主统计时顺带收集完整样本，供采样直接使用
                                    if len(sample_pool) < SAMPLE_POOL_CAP:
                                        visible_tiles_dict = dict(player_state.visible_tiles)
                                        dora_readable = "".join(
                                            MjlogParser.tile_to_string(d)
                                            for d in round_players[0].dora_indicators[:5]
                                        ) if round_players[0].dora_indicators else "（无）"
                                        if len(mt_item) > 1 and target_counts:
                                            visible_target = ", ".join(
                                                f"{k}:{_visible_count(visible_tiles_dict, k)}" for k in target_counts
                                            )
                                        elif item_combo and isinstance(mapped_target, list):
                                            visible_target = ", ".join(
                                                f"{t}:{_visible_count(visible_tiles_dict, t)}"
                                                for t in mapped_target
                                            )
                                        else:
                                            visible_target = str(_visible_count(visible_tiles_dict, mapped_target if isinstance(mapped_target, str) else mapped_target[0]))
                                        sp_entry = {
                                            "log_id": log_id,
                                            "round_num": player_state.round_num,
                                            "honba": player_state.honba,
                                            "oya": player_state.oya,
                                            "player_id": player_state.player_id,
                                            "turn": discard.turn,
                                            "actual_pattern": hand_discard_strings.copy(),
                                            "mapped_target": mapped_target,
                                            "hand_tiles": list(hand_at_turn),
                                            "visible_tiles": visible_tiles_dict,
                                            "dora_readable": dora_readable,
                                            "visible_target": visible_target,
                                            "is_combo": item_combo,
                                            "matched_pattern_idx": matched_idx,
                                        }
                                        if target_counts is not None:
                                            sp_entry["target_counts"] = target_counts
                                        else:
                                            sp_entry["target_count"] = target_count
                                        # 入库前校验：避免误收摸切/手切不符的样本（如 NOTm-2s 要求 2s 手切，2pt 不应匹配）
                                        qp, tt = items[matched_idx]
                                        ok, _ = verify_sample_consistency(sp_entry, qp, tt, visible_constraints)
                                        if ok:
                                            sample_pool.append(sp_entry)
                                    elif len(sample_pool) < 10:  # 仅在前几条时记录，避免刷屏
                                        logger.debug(f"样本入库校验未通过，跳过: {sp_entry.get('actual_pattern', [])} vs {qp}")
                    
                    except Exception as e:
                        logger.error(f"解析对局 {log_id} 失败: {e}")
                        continue
                
                    processed += 1
                
                    # 进度回调（每 10 场更新一次，避免长时间无反馈）
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                
                    # 达到样本限制
                    if sample_limit and processed >= sample_limit:
                        break
            
            last_id = logs[-1][0]  # ORDER BY id DESC，最后一条 id 最小，用于下一页游标
            
            # 达到样本限制
            if sample_limit and processed >= sample_limit:
                break
        
        if pool is not None:
            pool.shutdown(wait=True)
        conn.close()

        # 并行时可能超出 cap，截断
        matched_states = matched_states[:cap]
        sample_pool = sample_pool[:SAMPLE_POOL_CAP]

        target_count_distribution = pattern_distributions[0]
        probability_distribution = {}
        if multi_target:
            target_tiles = [_target_key(t[0], t[1]) for t in multi_targets]
            for tk in target_tiles:
                dist_t = target_count_distribution.get(tk, {0: 0, 1: 0, 2: 0, 3: 0})
                prob_t = {}
                for c in [0, 1, 2, 3]:
                    prob_t[c] = (dist_t.get(c, 0) / max(1, pattern_matches[0]) * 100) if pattern_matches[0] > 0 else 0
                probability_distribution[tk] = prob_t
        else:
            keys = [0, 1] if (use_tenpai or is_combo) else [0, 1, 2, 3]
            for count in keys:
                prob = (target_count_distribution.get(count, 0) / max(1, pattern_matches[0]) * 100) if pattern_matches[0] > 0 else 0
                probability_distribution[count] = prob

        pattern_results = []
        for idx, (p, t) in enumerate(items):
            dist = pattern_distributions[idx]
            mt = item_multi_targets[idx]
            if len(mt) > 1:
                prob = {}
                for tk in dist:
                    prob_t = {}
                    for c in [0, 1, 2, 3]:
                        prob_t[c] = (dist[tk].get(c, 0) / max(1, pattern_matches[idx]) * 100) if pattern_matches[idx] > 0 else 0
                    prob[tk] = prob_t
            else:
                prob = {}
                k = [0, 1] if (use_tenpai or item_variants[idx][2]) else [0, 1, 2, 3]
                for c in k:
                    prob[c] = (dist.get(c, 0) / max(1, pattern_matches[idx]) * 100) if pattern_matches[idx] > 0 else 0
            pattern_results.append({
                'pattern': p,
                'pattern_str': '-'.join(p),
                'target': t,
                'matches': pattern_matches[idx],
                'target_count_distribution': dist,
                'probability_distribution': prob,
                'is_combo': item_variants[idx][2],
                'multi_target': len(mt) > 1,
                'target_tiles': [_target_key(x[0], x[1]) for x in mt] if len(mt) > 1 else None,
            })

        result = {
            'total_logs_analyzed': processed,
            'total_matches': total_matches,
            'target_count_distribution': target_count_distribution,
            'probability_distribution': probability_distribution,
            'query_pattern': first_pattern,
            'query_pattern_str': '-'.join(first_pattern),
            'target_tile': first_target,
            'turn_range': turn_range,
            'variants_count': sum(len(iv[0]) for iv in item_variants),
            'matched_states': matched_states[:cap],
            'sample_pool': sample_pool,
            'is_combo': is_combo,
            'analysis_target': analysis_target,
            'multi_pattern': len(items) > 1,
            'multi_target': multi_target,
            'target_tiles': [_target_key(t[0], t[1]) for t in multi_targets] if multi_target else None,
            'pattern_results': pattern_results,
            'elapsed_seconds': round(time.perf_counter() - t0, 1),
        }

        logger.info(f"分析完成: 匹配 {total_matches} 个状态")
        # 样本一致性校验：抽查样本池中前若干条，确认 actual_pattern 能重新匹配
        if sample_pool and total_matches > 0:
            check_n = min(20, len(sample_pool))
            fail_count = 0
            for s in sample_pool[:check_n]:
                idx = s.get("matched_pattern_idx", 0)
                qp, tt = items[idx]
                ok, err = verify_sample_consistency(s, qp, tt, visible_constraints)
                if not ok:
                    fail_count += 1
                    logger.warning(f"样本一致性校验失败 [{s.get('log_id','')}]: {err} | actual={s.get('actual_pattern', [])}")
            if fail_count > 0:
                logger.warning(f"样本一致性: {check_n} 条中 {fail_count} 条校验失败，样本生成与分析可能不一致")
            else:
                logger.info(f"样本一致性: 抽查 {check_n} 条均通过")
        if len(items) > 1:
            for pr in pattern_results:
                logger.info(f"  {pr['pattern_str']}→{pr['target']}: {pr['matches']:,} 次")
        elif multi_target:
            for tk in target_tiles:
                pd = probability_distribution.get(tk, {})
                td = target_count_distribution.get(tk, {})
                logger.info(f"  {tk}: 0张 {pd.get(0,0):.2f}% 1张 {pd.get(1,0):.2f}% 2张 {pd.get(2,0):.2f}% 3张 {pd.get(3,0):.2f}%")
        elif use_tenpai:
            logger.info(f"  未听牌: {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 例)")
            logger.info(f"  听牌: {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 例)")
        elif is_combo:
            logger.info(f"  没有: {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 例)")
            logger.info(f"  有: {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 例)")
        else:
            logger.info(f"  有0张: {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 例)")
            logger.info(f"  有1张: {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 例)")
            logger.info(f"  有2张: {probability_distribution[2]:.2f}% ({target_count_distribution[2]:,} 例)")
            logger.info(f"  有3张: {probability_distribution[3]:.2f}% ({target_count_distribution[3]:,} 例)")
        
        return result

    def analyze_discard_pattern_grid(
        self,
        patterns: List[Tuple[List[str], str]],
        turn_ranges: List[Tuple[int, int]],
        merge_keys: List[int],
        analysis_target: str = "target_count",
        dora_constraint: Optional[str] = None,
        visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        call_area_constraints: Optional[List[str]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        analysis_batch_size: Optional[int] = None,
        exclude_south4: bool = False,
        prior_discard_exclusion: Optional[str] = None,
        max_workers: Optional[int] = None,  # >1 时启用 per-log 并行，1 或 None 为串行
    ) -> Dict:
        """
        单次扫描批量分析：舍牌模式 × 巡目范围 网格，一次遍历数据库得到所有单元格的合并概率。
        用于批量折线图，避免 N×M 次重复扫描。

        Returns:
            {"table": {(tr_idx, pat_idx): merged_prob}, "total_logs_analyzed": int, "elapsed_seconds": float}
        """
        t0 = time.perf_counter()
        def _target_key(tiles: List[str], is_combo: bool) -> str:
            return "".join(sorted(tiles)) if is_combo else tiles[0]

        use_tenpai = (analysis_target == "tenpai")
        keys = [0, 1] if use_tenpai else [0, 1, 2, 3]
        merge_keys_f = [k for k in merge_keys if k in keys] or keys[:2]

        # 预构建每格的变体与元数据
        grid_meta = []  # [(tr_idx, pat_idx, turn_range, item_variants, item_multi_targets, is_combo), ...]
        consumed_search_list = []
        for pat_idx, (pattern, target) in enumerate(patterns):
            multi_t = parse_multi_targets(target)
            first_t = multi_t[0]
            variant_target = ("".join(first_t[0]) if first_t[1] else first_t[0][0])
            vars_p = generate_equivalent_variants(
                pattern, variant_target, visible_constraints, prior_discard_exclusion
            )
            is_combo = first_t[1] and len(multi_t) == 1
            cs = get_consumed_search_patterns(pattern)
            if cs:
                consumed_search_list.append(cs)
            for tr_idx, turn_range in enumerate(turn_ranges):
                grid_meta.append((tr_idx, pat_idx, turn_range, vars_p, multi_t, is_combo))

        riichi_any = any(pattern_has_riichi(p) for p, _ in patterns)
        batch_size = analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE

        # 每格分布: (tr_idx, pat_idx) -> {0:n, 1:n, 2:n, 3:n} 或 tenpai 时 {0:n, 1:n}
        grid_dist = {}
        for tr_idx, pat_idx, _, _, multi_t, is_combo in grid_meta:
            k = (tr_idx, pat_idx)
            if k not in grid_dist:
                if use_tenpai or is_combo:
                    grid_dist[k] = {0: 0, 1: 0}
                else:
                    grid_dist[k] = {0: 0, 1: 0, 2: 0, 3: 0}

        if total_logs_hint is not None and total_logs_hint > 0:
            total_logs = total_logs_hint
        else:
            total_logs = 0
        if sample_limit:
            total_logs = min(total_logs, sample_limit) if total_logs > 0 else sample_limit

        use_parallel = max_workers is not None and max_workers > 1
        workers = min(max(1, max_workers or 1), os.cpu_count() or 4)
        if use_parallel:
            logger.info(f"单次扫描批量分析（并行 workers={workers}）: {len(patterns)} 模式 × {len(turn_ranges)} 巡目")
        else:
            logger.info(f"单次扫描批量分析: {len(patterns)} 模式 × {len(turn_ranges)} 巡目 = {len(grid_meta)} 格")

        grid_params = {
            "patterns": patterns,
            "grid_meta": grid_meta,
            "dora_constraint": dora_constraint,
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "call_area_constraints": call_area_constraints,
            "consumed_search_list": consumed_search_list,
            "exclude_south4": exclude_south4,
            "riichi_any": riichi_any,
            "use_tenpai": use_tenpai,
            "prior_discard_exclusion": prior_discard_exclusion,
        }

        conn = sqlite3.connect(self.db_path, timeout=60)
        cur = conn.cursor()
        _ensure_log_json_column(conn)
        last_id = None
        processed = 0

        while True:
            if should_cancel and should_cancel():
                logger.info("批量分析已取消")
                break
            if last_id is None:
                cur.execute(
                    """SELECT id, COALESCE(log_json, log) as content FROM logs
                       WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '')
                       ORDER BY id DESC LIMIT ?""",
                    (batch_size,),
                )
            else:
                cur.execute(
                    """SELECT id, COALESCE(log_json, log) as content FROM logs
                       WHERE ((log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != ''))
                         AND id < ? ORDER BY id DESC LIMIT ?""",
                    (last_id, batch_size),
                )
            logs = cur.fetchall()
            if not logs:
                break

            if use_parallel:
                tasks = [(log_id, log_content, grid_params) for log_id, log_content in logs]
                chunksize = max(1, len(tasks) // (workers * 4))
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    for per_log_result in pool.map(_process_one_log_grid, tasks, chunksize=chunksize):
                        if should_cancel and should_cancel():
                            conn.close()
                            return {"table": {}, "total_logs_analyzed": processed, "elapsed_seconds": round(time.perf_counter() - t0, 1)}
                        for (tr_idx, pat_idx), delta in per_log_result.items():
                            k = (tr_idx, pat_idx)
                            for count, n in delta.items():
                                grid_dist[k][count] = grid_dist[k].get(count, 0) + n
                        processed += 1
                        if progress_callback and (processed <= 10 or processed % 10 == 0):
                            progress_callback(processed, total_logs)
                        if sample_limit and processed >= sample_limit:
                            break
            else:
                for log_id, log_content in logs:
                    if should_cancel and should_cancel():
                        conn.close()
                        return {"table": {}, "total_logs_analyzed": processed, "elapsed_seconds": round(time.perf_counter() - t0, 1)}
                    try:
                        raw = _get_raw_content(log_content)
                        if riichi_any and _is_tenhou6_json(raw):
                            if "riichi" not in raw and "reach" not in raw:
                                processed += 1
                                if progress_callback and (processed <= 10 or processed % 10 == 0):
                                    progress_callback(processed, total_logs)
                                continue
                        if consumed_search_list and _is_tenhou6_json(raw):
                            if not any(log_contains_consumed(raw, cs) for cs in consumed_search_list):
                                processed += 1
                                if progress_callback and (processed <= 10 or processed % 10 == 0):
                                    progress_callback(processed, total_logs)
                                continue
                        game_states = parse_log_to_game_states(raw)
                        round_size = 4

                        for round_start in range(0, len(game_states), round_size):
                            round_players = game_states[round_start:round_start + round_size]
                            if len(round_players) < round_size:
                                break
                            if exclude_south4 and getattr(round_players[0], "round_num", 0) == 7:
                                continue
                            oya = getattr(round_players[0], "oya", 0)
                            dora_str = None
                            if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                                dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                                if dora_constraint != "dora_unrelated" and dora_str != dora_constraint:
                                    continue
                            if consumed_search_list:
                                if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                                    continue
                            if call_constraint or call_area_constraints:
                                if not round_could_satisfy_call_constraints(
                                    round_players, call_constraint, call_area_constraints, oya
                                ):
                                    continue
                            if riichi_any:
                                if not any(
                                    any(getattr(d, "is_riichi_declaration", False) for d in p.discards)
                                    for p in round_players
                                ):
                                    continue

                            for player_state in round_players:
                                if call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
                                    continue
                                if call_area_constraints and not player_could_satisfy_call_area_constraints(
                                    player_state, oya, call_area_constraints
                                ):
                                    continue

                                all_discards = [
                                    (i, d) for i, d in enumerate(player_state.discards)
                                    if hasattr(d, "turn") and hasattr(d, "tile")
                                ]
                                if not all_discards:
                                    continue
                                discards_precomputed = [
                                    (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                                    for _, d in all_discards
                                ]
                                discard_riichi_flags = [
                                    getattr(all_discards[i][1], "is_riichi_declaration", False)
                                    for i in range(len(all_discards))
                                ]
                                honor_ctx_base = {
                                    "jikaze": MjlogParser.get_jikaze(player_state.player_id, player_state.oya, player_state.round_num),
                                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                                    "calls": getattr(player_state, "calls", []),
                                }

                                for j, (orig_i, discard) in enumerate(all_discards):
                                    discard_turn = discard.turn

                                    for tr_idx, pat_idx, turn_range, vars_p, multi_t, is_combo in grid_meta:
                                        min_turn, max_turn = turn_range
                                        if not (min_turn <= discard_turn <= max_turn):
                                            continue
                                        in_range_for_cell = [
                                            (idx, dd) for idx, dd in all_discards
                                            if min_turn <= dd.turn <= max_turn and dd.turn <= discard_turn
                                        ]
                                        if not in_range_for_cell or in_range_for_cell[-1][1] != discard:
                                            continue
                                        full_discards_up_to_now = [
                                            discards_precomputed[idx] for idx, _ in in_range_for_cell
                                        ]
                                        riichi_flags_for_cell = [discard_riichi_flags[idx] for idx, _ in in_range_for_cell]
                                        honor_ctx = {
                                            **honor_ctx_base,
                                            "current_discard_turn": discard_turn,
                                            "discard_riichi_flags": riichi_flags_for_cell,
                                        }
                                        pattern, target = patterns[pat_idx]
                                        cs = get_consumed_search_patterns(pattern)
                                        if cs:
                                            if not round_has_matching_consumed(round_players, cs):
                                                continue
                                            if not player_has_matching_consumed(player_state, cs):
                                                continue
                                        matched_variant = match_discard_to_variant(full_discards_up_to_now, vars_p, honor_ctx)
                                        if not matched_variant:
                                            continue
                                        if prior_discard_exclusion and turn_range:
                                            first_turn_in_range = in_range_for_cell[0][1].turn
                                            prior_discards = [d for d in player_state.discards if d.turn < first_turn_in_range]
                                            excl_str = matched_variant.get("prior_discard_exclusion")
                                            if excl_str:
                                                forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                                if any((d.tile // 4) in forbidden for d in prior_discards):
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
                                        if riichi_constraint and riichi_constraint != "any":
                                            if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                                                continue
                                            if riichi_constraint == "no_riichi" and discard.riichi_happened:
                                                continue
                                        if call_constraint and call_constraint != "any":
                                            if call_constraint == "has_call" and not discard.call_happened:
                                                continue
                                            if call_constraint == "no_call" and discard.call_happened:
                                                continue
                                        if call_area_constraints:
                                            if not player_satisfies_call_area_constraints(
                                                player_state, round_players, player_state.oya, call_area_constraints,
                                                current_discard_turn=discard.turn,
                                            ):
                                                continue
                                        mapped_target = matched_variant["target"]
                                        if not use_tenpai:
                                            target_equiv = set()
                                            if len(multi_t) == 1:
                                                if is_combo:
                                                    for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target]):
                                                        target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                                                else:
                                                    target_equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target))
                                            else:
                                                target_equiv = MjlogParser.get_count_equivalent_bases(
                                                    MjlogParser.string_to_tile(mapped_target if isinstance(mapped_target, str) else mapped_target[0])
                                                )
                                                for tiles, ic in multi_t[1:]:
                                                    for t in tiles:
                                                        target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                                            if discard.tile // 4 in target_equiv:
                                                continue

                                        if orig_i < len(player_state.hand_tiles_history):
                                            hand_at_turn = list(player_state.hand_tiles_history[orig_i])
                                        else:
                                            hand_at_turn = list(player_state.hand_tiles)
                                        if use_tenpai:
                                            target_count = 1 if is_tenpai(hand_at_turn) else 0
                                        elif len(multi_t) > 1:
                                            tk = _target_key(multi_t[0][0], multi_t[0][1])
                                            mt_item = multi_t
                                            if multi_t[0][1]:
                                                target_codes = [MjlogParser.string_to_tile(t) for t in multi_t[0][0]]
                                                hand_bases = [t // 4 for t in hand_at_turn]
                                                target_count = 1 if all(
                                                    any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                                    for c in target_codes
                                                ) else 0
                                            else:
                                                equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(multi_t[0][0][0]))
                                                target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                                        elif is_combo:
                                            target_codes = [MjlogParser.string_to_tile(t) for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target])]
                                            hand_bases = [t // 4 for t in hand_at_turn]
                                            target_count = 1 if all(
                                                any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                                for c in target_codes
                                            ) else 0
                                        else:
                                            equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target))
                                            target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)

                                        k = (tr_idx, pat_idx)
                                        dist = grid_dist[k]
                                        if target_count in dist:
                                            dist[target_count] += 1
                                        else:
                                            dist[target_count] = 1

                    except Exception as e:
                        logger.error(f"解析对局 {log_id} 失败: {e}")
                        continue
                    processed += 1
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                    if sample_limit and processed >= sample_limit:
                        break

            last_id = logs[-1][0]
            if sample_limit and processed >= sample_limit:
                break

        conn.close()

        # 按格计算合并概率（tenpai/combo 仅 0,1；普通为 0,1,2,3）
        cell_combo = {(tr_idx, pat_idx): is_combo for tr_idx, pat_idx, _, _, _, is_combo in grid_meta}
        table = {}
        for (tr_idx, pat_idx), dist in grid_dist.items():
            total_m = sum(dist.values())
            if total_m == 0:
                prob = 0.0
            else:
                is_combo_cell = cell_combo.get((tr_idx, pat_idx), False)
                k_list = [0, 1] if (use_tenpai or is_combo_cell) else [0, 1, 2, 3]
                prob = sum(dist.get(k, 0) / total_m * 100 for k in merge_keys_f if k in k_list)
            table[(tr_idx, pat_idx)] = round(prob, 2)

        logger.info(f"批量分析完成: 处理 {processed:,} 场对局")
        return {"table": table, "total_logs_analyzed": processed, "elapsed_seconds": round(time.perf_counter() - t0, 1)}

    def collect_verification_samples(
        self,
        query_pattern: List[str],
        target_tile: str,
        sample_count: int = 10,
        target_count_filter: Optional[int] = None,  # None=全部, 0/1/2/3=只收该数量
        target_tile_filter: Optional[str] = None,  # 多目标时指定按哪个目标筛选，如 "6s"
        dora_constraint: Optional[str] = None,
        visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        turn_range: Optional[Tuple[int, int]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        sample_pool: Optional[List[Dict]] = None,  # 主统计时预收集的样本池，有则无需二次遍历
        analysis_batch_size: Optional[int] = None,
        exclude_south4: bool = False,  # 南四局打法随点数变化大，True 时跳过南四局
        prior_discard_exclusion: Optional[str] = None,  # 前段不可打，与舍牌模式同步等价变换
    ) -> List[Dict]:
        """
        收集验证样本，用于人工复盘核对。
        返回含 log_id、oya、正确小局显示等完整信息的样本列表。
        若传入 sample_pool（主统计时预收集），则直接从中采样，无需二次分析。
        """
        batch_size = analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        if sample_pool and len(sample_pool) > 0:
            # 从预收集的样本池中筛选并取前 N 个，无需遍历牌谱（主分析已排除南四局则无需再过滤）
            candidates = sample_pool
            if target_count_filter is not None:
                if target_tile_filter and any("target_counts" in s for s in sample_pool):
                    candidates = [s for s in sample_pool if s.get("target_counts", {}).get(target_tile_filter) == target_count_filter]
                elif any("target_counts" in s for s in sample_pool):
                    candidates = sample_pool
                else:
                    candidates = [s for s in sample_pool if s.get("target_count") == target_count_filter]
            # 多模式时：二次校验 actual_pattern 与当前 query 一致，避免选中 NOTm-2s 却混入 NOTm-2st 的样本
            verified = [s for s in candidates if verify_sample_consistency(s, query_pattern, target_tile, visible_constraints)[0]]
            return verified[:sample_count]

        variants = generate_equivalent_variants(
            query_pattern, target_tile, visible_constraints, prior_discard_exclusion
        )
        consumed_search = get_consumed_search_patterns(query_pattern)
        riichi_search = pattern_has_riichi(query_pattern)
        samples = []

        if should_cancel and should_cancel():
            return []

        conn = sqlite3.connect(self.db_path, timeout=60)
        cur = conn.cursor()
        _ensure_log_json_column(conn)

        # 避免耗时的 COUNT(*)，使用 total_logs_hint 或 sample_limit
        if total_logs_hint is not None and total_logs_hint > 0:
            total_logs = total_logs_hint
        else:
            total_logs = 0
        if sample_limit:
            total_logs = min(total_logs, sample_limit) if total_logs > 0 else sample_limit

        last_id = None  # 游标分页，避免 OFFSET 越大越慢
        processed = 0

        while True:
            if should_cancel and should_cancel():
                break
            if last_id is None:
                cur.execute(
                    "SELECT id, COALESCE(NULLIF(log_json, ''), log) FROM logs WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '') ORDER BY id DESC LIMIT ?",
                    (batch_size,),
                )
            else:
                cur.execute(
                    "SELECT id, COALESCE(NULLIF(log_json, ''), log) FROM logs WHERE ((log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '')) AND id < ? ORDER BY id DESC LIMIT ?",
                    (last_id, batch_size),
                )
            logs = cur.fetchall()
            if not logs:
                break

            for log_id, log_content in logs:
                if should_cancel and should_cancel():
                    conn.close()
                    return samples
                try:
                    raw = _get_raw_content(log_content)
                    if riichi_search and _is_tenhou6_json(raw):
                        if "riichi" not in raw and "reach" not in raw:
                            continue
                    if consumed_search and _is_tenhou6_json(raw):
                        if not log_contains_consumed(raw, consumed_search):
                            continue
                    game_states = parse_log_to_game_states(raw)
                    round_size = 4

                    for round_start in range(0, len(game_states), round_size):
                        round_players = game_states[round_start:round_start + round_size]
                        if len(round_players) < round_size:
                            break
                        if exclude_south4 and getattr(round_players[0], "round_num", 0) == 7:
                            continue  # 南四局
                        dora_str = None
                        if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                            dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                            if dora_constraint != "dora_unrelated" and dora_str != dora_constraint:
                                continue

                        if consumed_search and not round_has_matching_consumed(round_players, consumed_search):
                            continue
                        if riichi_search:
                            if not any(
                                any(getattr(d, 'is_riichi_declaration', False) for d in p.discards)
                                for p in round_players
                            ):
                                continue

                        for player_state in round_players:
                            if consumed_search and not player_has_matching_consumed(player_state, consumed_search):
                                continue
                            if turn_range:
                                min_turn, max_turn = turn_range
                                in_range = [
                                    (i, d) for i, d in enumerate(player_state.discards)
                                    if min_turn <= d.turn <= max_turn
                                ]
                            else:
                                in_range = [(i, d) for i, d in enumerate(player_state.discards)]
                            if not in_range:
                                continue

                            discards_precomputed = [
                                (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                                for _, d in in_range
                            ]
                            discarded_bases = set()
                            honor_ctx_base = {
                                "jikaze": MjlogParser.get_jikaze(player_state.player_id, player_state.oya, player_state.round_num),
                                "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                                "calls": getattr(player_state, "calls", []),
                            }
                            discard_riichi_flags = [getattr(in_range[i][1], 'is_riichi_declaration', False) for i in range(len(in_range))]

                            for j, (orig_i, discard) in enumerate(in_range):
                                discarded_bases.add(discard.tile // 4)
                                full_discards_up_to_now = discards_precomputed[:j + 1]
                                hand_discard_strings = []
                                for i, (t, ts) in enumerate(full_discards_up_to_now):
                                    if i < len(discard_riichi_flags) and discard_riichi_flags[i]:
                                        hand_discard_strings.append(f"{t}r")
                                    elif ts:
                                        hand_discard_strings.append(f"{t}t")
                                    else:
                                        hand_discard_strings.append(t)

                                honor_ctx = {
                                    **honor_ctx_base,
                                    "current_discard_turn": discard.turn,
                                    "discard_riichi_flags": discard_riichi_flags[: j + 1],
                                }
                                matched_variant = match_discard_to_variant(full_discards_up_to_now, variants, honor_ctx)
                                if not matched_variant:
                                    continue
                                if prior_discard_exclusion and turn_range:
                                    prior_discards = [d for d in player_state.discards if d.turn < in_range[0][1].turn]
                                    excl_str = matched_variant.get("prior_discard_exclusion")
                                    if excl_str:
                                        forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                        if any((d.tile // 4) in forbidden for d in prior_discards):
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

                                if riichi_constraint and riichi_constraint != "any":
                                    if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                                        continue
                                    if riichi_constraint == "no_riichi" and discard.riichi_happened:
                                        continue
                                if call_constraint and call_constraint != "any":
                                    if call_constraint == "has_call" and not discard.call_happened:
                                        continue
                                    if call_constraint == "no_call" and discard.call_happened:
                                        continue

                                vc = matched_variant["visible_constraints"]
                                if vc:
                                    match_visible = True
                                    for tile_str, (min_count, max_count) in vc.items():
                                        base_code = MjlogParser.string_to_tile(tile_str)
                                        equiv_bases = MjlogParser.get_count_equivalent_bases(base_code)
                                        count = sum(
                                            c for t, c in player_state.visible_tiles.items()
                                            if t // 4 in equiv_bases
                                        )
                                        if not (min_count <= count <= max_count):
                                            match_visible = False
                                            break
                                    if not match_visible:
                                        continue

                                # 排除：若本巡打出的牌就是目标牌，不计入（与主统计逻辑一致）
                                mapped_target = matched_variant["target"]
                                sample_is_combo = matched_variant.get("is_combo", False)
                                if sample_is_combo:
                                    target_equiv = set()
                                    for t in mapped_target:
                                        target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                                    if discard.tile // 4 in target_equiv:
                                        continue
                                else:
                                    if MjlogParser.bases_equivalent_for_count(discard.tile // 4, MjlogParser.string_to_tile(mapped_target)):
                                        continue

                                hh = (
                                    player_state.hand_tiles_history[orig_i]
                                    if orig_i < len(player_state.hand_tiles_history)
                                    else player_state.hand_tiles
                                )
                                hand_at_turn = list(hh)  # 必须 list 以保留同种牌枚数
                                if sample_is_combo:
                                    target_codes = [
                                        MjlogParser.string_to_tile(t) for t in mapped_target
                                    ]
                                    hand_bases = [t // 4 for t in hand_at_turn]
                                    target_count = 1 if all(
                                        any(hand_bases.count(b) >= 1 for b in MjlogParser.get_count_equivalent_bases(c))
                                        for c in target_codes
                                    ) else 0
                                else:
                                    mapped_target_code = MjlogParser.string_to_tile(mapped_target)
                                    equiv = MjlogParser.get_count_equivalent_bases(mapped_target_code)
                                    target_count = sum(
                                        1 for tile in hand_at_turn
                                        if tile // 4 in equiv
                                    )
                                    target_count = min(target_count, 3)

                                if target_count_filter is not None and target_count != target_count_filter:
                                    continue

                                dora_readable = "".join(
                                    MjlogParser.tile_to_string(d)
                                    for d in round_players[0].dora_indicators[:5]
                                ) if round_players[0].dora_indicators else "（无）"

                                visible_tiles_dict = dict(player_state.visible_tiles)
                                if sample_is_combo and isinstance(mapped_target, list):
                                    visible_target = ", ".join(
                                        f"{t}:{_visible_count(visible_tiles_dict, t)}"
                                        for t in mapped_target
                                    )
                                else:
                                    t0 = mapped_target[0] if isinstance(mapped_target, list) else mapped_target
                                    visible_target = str(_visible_count(visible_tiles_dict, t0))
                                sp_entry = {
                                    "log_id": log_id,
                                    "round_num": player_state.round_num,
                                    "honba": player_state.honba,
                                    "oya": player_state.oya,
                                    "player_id": player_state.player_id,
                                    "turn": discard.turn,
                                    "actual_pattern": hand_discard_strings.copy(),
                                    "mapped_target": mapped_target,
                                    "hand_tiles": list(hand_at_turn),
                                    "visible_tiles": visible_tiles_dict,
                                    "dora_readable": dora_readable,
                                    "visible_target": visible_target,
                                    "target_count": target_count,
                                    "is_combo": sample_is_combo,
                                }
                                if verify_sample_consistency(sp_entry, query_pattern, target_tile, visible_constraints)[0]:
                                    samples.append(sp_entry)
                                if len(samples) >= sample_count:
                                    break

                        if len(samples) >= sample_count:
                            break

                except Exception as e:
                    logger.debug(f"解析 {log_id} 失败: {e}")
                    continue

                if len(samples) >= sample_count:
                    break

            processed += len(logs)
            if progress_callback and processed % 500 == 0:
                progress_callback(processed, total_logs)
            last_id = logs[-1][0]  # ORDER BY id DESC，用于下一页游标
            if sample_limit and processed >= sample_limit:
                break
            if len(samples) >= sample_count:
                break

        conn.close()
        return samples


def _visible_count(visible_tiles: dict, tile_str: str) -> int:
    """统计某牌在可见牌中的枚数（0m/0p/0s 与 5m/5p/5s 视为不同牌）"""
    base = MjlogParser.string_to_tile(tile_str)
    equiv = MjlogParser.get_count_equivalent_bases(base)
    return sum(c for t, c in visible_tiles.items() if t // 4 in equiv)


def _fmt_target(mt) -> str:
    """格式化目标牌（单张或搭子）"""
    return "-".join(mt) if isinstance(mt, list) else str(mt)


def _target_desc(s: dict, use_tenpai: bool = False) -> str:
    """目标牌描述：听牌模式=听牌/未听牌；搭子=有/无搭子；单张=应有X张在手牌；多目标=各目标枚数"""
    if use_tenpai:
        return "听牌" if s["target_count"] else "未听牌"
    tc = s.get("target_counts")
    if tc is not None:
        return " ".join(f"{k}:{v}张" for k, v in sorted(tc.items()))
    if s.get("is_combo"):
        return "有搭子" if s["target_count"] else "无搭子"
    return "应有{}张在手牌".format(s["target_count"])


def _target_display_set(mt) -> set:
    """目标牌的显示集合（0m/0p/0s 与 5m/5p/5s 视为不同牌）"""
    return set(mt) if isinstance(mt, list) else {mt}


def _actual_pattern_to_full_discards(actual_pattern: List[str]) -> List[Tuple[str, bool]]:
    """
    将 actual_pattern（舍牌序列的展示格式）转回 full_discards 格式供匹配使用。
    "1m" -> (1m, False) 手切
    "1mt" -> (1m, True) 摸切
    "1mr" -> (1m, True) 立直宣言牌（通常为摸切）
    """
    result = []
    for s in actual_pattern:
        if not s or len(s) < 2:
            continue
        if s.endswith("t"):
            result.append((s[:-1], True))
        elif s.endswith("r"):
            result.append((s[:-1], True))  # 立直宣言通常为摸切
        else:
            result.append((s, False))
    return result


def verify_sample_consistency(
    sample: Dict,
    query_pattern: List[str],
    target_tile: str,
    visible_constraints: Optional[Dict] = None,
) -> Tuple[bool, Optional[str]]:
    """
    验证样本的 actual_pattern 是否与查询模式在匹配逻辑下一致。
    用于检测样本生成与分析匹配是否同源。

    Returns:
        (一致, 错误信息)
    """
    actual = sample.get("actual_pattern")
    if not actual:
        return (False, "样本无 actual_pattern")
    try:
        full_discards = _actual_pattern_to_full_discards(actual)
        if not full_discards:
            return (False, "actual_pattern 解析后为空")
        from .simple_normalizer import parse_multi_targets
        first_t = parse_multi_targets(target_tile)[0]
        variant_target = "".join(first_t[0]) if first_t[1] else first_t[0][0]
        variants = generate_equivalent_variants(query_pattern, variant_target, visible_constraints)
        # 样本不包含 honor context，对纯数牌模式可省略；若有 zf/kf 会可能误判
        ctx = {}
        matched = match_discard_to_variant(full_discards, variants, ctx)
        if matched:
            return (True, None)
        return (False, "重新匹配失败：actual_pattern 无法匹配任一等价变体")
    except Exception as e:
        return (False, str(e))


def _target_counts_display_set(tc: dict) -> set:
    """多目标时，各目标牌及其等价牌的显示集合"""
    out = set()
    for k in (tc or {}):
        base = MjlogParser.string_to_tile(k)
        for b in MjlogParser.get_count_equivalent_bases(base):
            out.add(MjlogParser.tile_to_string(b * 4))
    return out


def format_samples_for_display(samples: List[Dict], query_pattern_str: str, target_tile: str,
                               analysis_target: str = "target_count") -> str:
    """将样本格式化为可读文本，支持单张、搭子、听牌、多目标模式"""
    use_tenpai = (analysis_target == "tenpai")
    is_combo = False if use_tenpai else (samples[0].get("is_combo", False) if samples else False)
    has_multi = bool(samples and samples[0].get("target_counts"))
    target_label = target_tile if use_tenpai else (f"{target_tile} (搭子)" if is_combo else (f"{target_tile} (多目标)" if has_multi else target_tile))
    lines = [
        "=" * 80,
        f"验证样本：{query_pattern_str} → {target_label}",
        "=" * 80,
        ""
    ]
    for i, s in enumerate(samples, 1):
        mt = s["mapped_target"]
        if has_multi and s.get("target_counts"):
            mt_set = _target_counts_display_set(s["target_counts"])
        else:
            mt_set = _target_display_set(mt) if not use_tenpai else set()
        hand_parts = []
        for t in sorted(s["hand_tiles"], key=lambda x: (x // 4, x)):
            ts = MjlogParser.tile_to_string(t)
            hand_parts.append(f"[{ts}]" if (ts in mt_set) else ts)
        hand_str = " ".join(hand_parts)
        round_display = MjlogParser.format_round_display(s["round_num"], s["honba"])
        wind = MjlogParser.get_player_wind(s["player_id"], s["oya"])
        if use_tenpai:
            target_line = f"  听牌状态:   {_target_desc(s, use_tenpai)}"
        elif has_multi and s.get("target_counts"):
            target_line = f"  各目标在手牌: {_target_desc(s, use_tenpai)}"
        else:
            target_line = f"  目标牌:     {_fmt_target(mt)} ({_target_desc(s, use_tenpai)})"
        block = [
            f"【样本 {i}】",
            f"  对局ID:     {s['log_id']}",
            f"  天凤牌谱:   https://tenhou.net/4/?log={s['log_id']}&tw={s['player_id']}",
            f"  小局/本场:  {round_display}",
            f"  目标玩家:   {wind}家",
            f"  巡目:       第{s['turn']}巡",
            f"  宝牌:       {s['dora_readable']}",
            f"  舍牌序列:   {' '.join(s['actual_pattern'])} ",
            target_line,
            f"  手牌({len(s['hand_tiles'])}张): {hand_str}",
        ]
        if not use_tenpai:
            if has_multi and s.get("target_counts"):
                block.append(f"  可见各目标: {s['visible_target']} (他家舍牌+宝牌指示物)")
            else:
                block.append(f"  可见{_fmt_target(mt)}: {s['visible_target']}{'' if s.get('is_combo') else '张'} (他家舍牌+宝牌指示物)")
        block.append("")
        lines.extend(block)
    lines.extend([
        "=" * 80,
        f"共 {len(samples)} 条样本",
        "=" * 80
    ])
    return "\n".join(lines)


def get_database_stats(db_path: str) -> Dict:
    """
    获取数据库统计信息
    
    Args:
        db_path: 数据库路径
        
    Returns:
        统计信息字典
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    stats = {}
    
    # 对局总数
    cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
    stats['total_logs'] = cur.fetchone()[0]
    
    # 数据库大小
    import os
    if os.path.exists(db_path):
        stats['db_size_mb'] = os.path.getsize(db_path) / (1024 * 1024)
    
    conn.close()
    
    return stats
