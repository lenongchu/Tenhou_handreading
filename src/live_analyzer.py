"""
瀹炴椂鏌ヨ寮曟搸 - 鎸夐渶鍒嗘瀽妯″紡
鐩存帴浠?logs 琛ㄨ鍙栧畬鏁寸墝璋辨暟鎹苟瀹炴椂瑙ｆ瀽鍒嗘瀽

瑙ｆ瀽閲囩敤 tenhou6 鏍煎紡锛坱enhou-paifu-to-json锛夛紝鏀寔鍓湶绛夊畬鏁翠俊鎭€?
"""
import gc
import os
import time
import sqlite3
import gzip
import json
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from typing import List, Dict, Optional, Tuple, Callable, Union, Iterable, Iterator
from collections import Counter
import logging

from .mjlog_parser import MjlogParser, GameState
from .tenpai_utils import is_tenpai
from .equivalent_variants import (
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

# 姣忔壒浠庢暟鎹簱璇诲彇鐨勫灞€鏁般€傝秺澶у垯 SQL 娆℃暟瓒婂皯銆佺暐蹇紝浣嗗崟鎵瑰唴瀛樼嚎鎬у鍔狅紙绾?1.2GB/1000 鏉★紝5000 鏉＄害 6GB锛?
ANALYSIS_BATCH_SIZE = 400
MIN_ANALYSIS_BATCH_SIZE = 100
# 涓荤粺璁℃椂鏈€澶氫繚鐣欑殑鍖归厤鐘舵€佹潯鏁帮紙浠呯敤浜庤繑鍥炵粰鐣岄潰锛岃秴鍑洪儴鍒嗕笉淇濈暀锛岄伩鍏嶅唴瀛樻寔缁闀匡級
MATCHED_STATES_CAP = 200
SAMPLE_POOL_CAP = 3000
PARALLEL_MIN_MATCHED_STATES_PER_LOG = 4
PARALLEL_MAX_SAMPLE_POOL_PER_LOG = 64
PARALLEL_BATCH_PER_WORKER = 24
PARALLEL_IN_FLIGHT_FACTOR = 2
GC_INTERVAL_BATCHES = 4  # 每 N 批做 gc + 重连 DB，重连可释放 tenhou.db 占用的 10+GB 缓存
HIGH_MEMORY_LOAD_RATIO = 0.90
# 骞惰鍒嗘瀽鏃讹紝姣忓鐞嗗灏戞壒鍚庨噸鍚?worker 姹狅紝浠ラ噴鏀惧瓙杩涚▼鍐?Python 鎸佹湁鐨勫唴瀛?
POOL_RESTART_EVERY_BATCHES = 3


def _clamp_analysis_batch_size(batch_size: int, workers: int, use_parallel: bool) -> int:
    """Clamp analysis batch size to keep parallel memory usage bounded."""
    requested = max(MIN_ANALYSIS_BATCH_SIZE, int(batch_size))
    if not use_parallel:
        return requested

    safe_cap = max(MIN_ANALYSIS_BATCH_SIZE, workers * PARALLEL_BATCH_PER_WORKER)
    if requested > safe_cap:
        logger.info(
            "analysis_batch_size=%s is too high for workers=%s; clamped to %s to reduce memory usage",
            requested,
            workers,
            safe_cap,
        )
        return safe_cap
    return requested


def _trim_process_memory() -> None:
    """Best-effort memory trim for long runs (mainly effective on Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
    except Exception:
        pass


def _get_system_memory_load_ratio() -> Optional[float]:
    """Return system memory load ratio in [0, 1] on Windows; otherwise None."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return float(status.dwMemoryLoad) / 100.0
    except Exception:
        return None
    return None


def _should_run_memory_maintenance(batch_index: int, gc_interval_batches: Optional[int] = None) -> bool:
    """Run maintenance periodically, or early under high system memory pressure."""
    if batch_index <= 0:
        return False
    interval = gc_interval_batches if gc_interval_batches is not None else GC_INTERVAL_BATCHES
    if batch_index % max(1, interval) == 0:
        return True
    ratio = _get_system_memory_load_ratio()
    return ratio is not None and ratio >= HIGH_MEMORY_LOAD_RATIO


def _iter_pool_results_bounded(
    pool: ProcessPoolExecutor,
    fn,
    tasks: Iterable,
    max_in_flight: int,
) -> Iterator:
    """
    Submit tasks with bounded in-flight futures to avoid unbounded task buffering.
    Results are yielded as soon as futures complete.
    """
    max_in_flight = max(1, int(max_in_flight))
    task_iter = iter(tasks)
    pending = set()
    try:
        while len(pending) < max_in_flight:
            try:
                pending.add(pool.submit(fn, next(task_iter)))
            except StopIteration:
                break

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                yield fut.result()
                try:
                    pending.add(pool.submit(fn, next(task_iter)))
                except StopIteration:
                    pass
    finally:
        for fut in pending:
            fut.cancel()


def _get_raw_content(content: Union[bytes, str]) -> str:
    """灏?log 鍐呭锛堝彲鑳?gzip锛夎浆涓?UTF-8 瀛楃涓?"""
    if isinstance(content, bytes):
        try:
            return gzip.decompress(content).decode("utf-8")
        except Exception:
            return content.decode("utf-8", errors="replace")
    return content


def _is_tenhou6_json(raw: str) -> bool:
    """鍒ゆ柇鏄惁涓?tenhou6 JSON锛堝彲鍋氬壇闇插瓧绗︿覆鎼滅储锛?"""
    s = raw.strip()
    return s.startswith("{") and "games" in raw


def _format_actual_pattern(
    full_discards: List[Tuple[str, bool]],
    discard_riichi_flags: List[bool],
) -> List[str]:
    """Format discard tuples for display/storage only when needed."""
    result = []
    for i, (tile_str, is_tsumogiri) in enumerate(full_discards):
        if i < len(discard_riichi_flags) and discard_riichi_flags[i]:
            result.append(f"{tile_str}r")
        elif is_tsumogiri:
            result.append(f"{tile_str}t")
        else:
            result.append(tile_str)
    return result


def _game_at_index_contains_consumed(raw: str, game_index: int, consumed_search) -> bool:
    """
    妫€鏌?raw锛坱enhou6 JSON锛変腑绗?game_index 涓皬灞€鐨勫師濮嬪唴瀹规槸鍚﹀寘鍚?consumed銆?
    閫氳繃鎻愬彇璇ュ皬灞€鐨?JSON 鐗囨杩涜瀛楃涓叉悳绱紝閬垮厤瑙ｆ瀽/缁撴瀯闂瀵艰嚧鐨勮法灞€姹℃煋銆?
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


def _connect_db_memory_efficient(db_path: str):
    """
    创建限制内存占用的 SQLite 连接。
    避免 tenhou.db 在长时分析中占用十余 GB 内存（SQLite 缓存 + OS 文件缓存）。
    """
    conn = sqlite3.connect(db_path, timeout=60)
    cur = conn.cursor()
    cur.execute("PRAGMA cache_size = -2000")
    cur.execute("PRAGMA mmap_size = 0")
    return conn


def _ensure_log_json_column(conn) -> None:
    """纭繚 logs 琛ㄦ湁 log_json 鍒楋紙鐢ㄤ簬 tenhou6 鏍煎紡锛?"""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(logs)")
    columns = [row[1] for row in cur.fetchall()]
    if "log_json" not in columns:
        try:
            cur.execute("ALTER TABLE logs ADD COLUMN log_json BLOB")
            conn.commit()
        except Exception as e:
            logger.debug(f"娣诲姞 log_json 鍒? {e}")


def parse_log_to_game_states(content: Union[bytes, str]) -> List[GameState]:
    """
    瑙ｆ瀽瀵瑰眬鍐呭涓?GameState 鍒楄〃銆?
    浣跨敤 tenhou6 鏍煎紡瑙ｆ瀽锛圶ML 缁?tenhou-paifu-to-json 杞负 JSON 鍚庤В鏋愶級銆?

    Args:
        content: gzip 鍘嬬缉鐨?bytes锛屾垨 XML/JSON 瀛楃涓?

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
            logger.warning(f"tenhou6 JSON 瑙ｆ瀽澶辫触: {e}")
            return []

    # XML锛氶€氳繃 tenhou-paifu-to-json 杞负 tenhou6 JSON 鍚庤В鏋?
    try:
        from .tenhou6_adapter import xml_to_tenhou6_json, load_tenhou6_json
        json_str = xml_to_tenhou6_json(raw)
        if json_str:
            return load_tenhou6_json(json_str)
    except Exception as e:
        logger.warning(f"XML鈫抰enhou6 杞崲澶辫触: {e}")

    return []


def _process_one_log_grid(task: Tuple) -> Dict:
    """
    Per-log 骞惰 worker锛氬鐞嗗崟鏉＄墝璋憋紝杩斿洖璇?log 瀵瑰悇鏍肩殑澧為噺缁熻銆?
    渚?ProcessPoolExecutor 璋冪敤锛屽繀椤绘槸妯″潡绾у嚱鏁颁互鏀寔 pickle銆?
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
    exclude_south3 = params.get("exclude_south3", False)
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
            rn = getattr(round_players[0], "round_num", 0)
            if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
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
                    "bakaze": ["东", "南", "西", "北"][player_state.round_num // 4],
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
        logger.error(f"瑙ｆ瀽瀵瑰眬 {log_id} 澶辫触: {e}")
    return result


def _process_one_log_analyze(task: Tuple) -> Dict:
    """
    Per-log 骞惰 worker锛氫富鐣岄潰鍒嗘瀽锛屽鐞嗗崟鏉＄墝璋憋紝杩斿洖璇?log 鐨勫閲忕粺璁°€?
    渚?ProcessPoolExecutor 璋冪敤锛屽繀椤绘槸妯″潡绾у嚱鏁颁互鏀寔 pickle銆?
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
    exclude_south3 = params.get("exclude_south3", False)
    riichi_any = params.get("riichi_any", False)
    prior_discard_exclusion = params.get("prior_discard_exclusion")
    use_tenpai = params.get("use_tenpai", False)
    cap = params.get("cap", MATCHED_STATES_CAP)
    worker_matched_states_cap = max(
        1, min(cap, int(params.get("worker_matched_states_cap", cap)))
    )
    worker_sample_pool_cap = max(
        0, int(params.get("worker_sample_pool_cap", PARALLEL_MAX_SAMPLE_POOL_PER_LOG))
    )

    def _target_key(tiles: List[str], is_combo: bool) -> str:
        return "".join(sorted(tiles)) if is_combo else tiles[0]

    total_matches = 0
    outcome_wins = 0
    outcome_deal_ins = 0
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
                return {"total_matches": 0, "outcome_wins": 0, "outcome_deal_ins": 0,
                        "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                        "matched_states": [], "sample_pool": []}
        if consumed_search_list and _is_tenhou6_json(raw):
            if len(items) == 1:
                if not log_contains_consumed(raw, consumed_search_list[0]):
                    return {"total_matches": 0, "outcome_wins": 0, "outcome_deal_ins": 0,
                            "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                            "matched_states": [], "sample_pool": []}
            else:
                if not any(log_contains_consumed(raw, cs) for cs in consumed_search_list):
                    return {"total_matches": 0, "outcome_wins": 0, "outcome_deal_ins": 0,
                            "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                            "matched_states": [], "sample_pool": []}
        game_states = parse_log_to_game_states(raw)
        round_size = 4

        for round_start in range(0, len(game_states), round_size):
            round_players = game_states[round_start:round_start + round_size]
            if len(round_players) < round_size:
                break
            rn = getattr(round_players[0], "round_num", 0)
            if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
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
                    "bakaze": ["东", "南", "西", "北"][player_state.round_num // 4],
                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                    "calls": getattr(player_state, "calls", []),
                }
                discard_riichi_flags = [getattr(in_range[i][1], 'is_riichi_declaration', False) for i in range(len(in_range))]

                for j, (orig_i, discard) in enumerate(in_range):
                    full_discards_up_to_now = discards_precomputed[:j + 1]

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
                    rw = getattr(player_state, "round_winners", [])
                    rdi = getattr(player_state, "round_deal_in", None)
                    if rw and player_state.player_id in rw:
                        outcome_wins += 1
                    if rdi is not None and rdi == player_state.player_id:
                        outcome_deal_ins += 1
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

                    hand_discard_strings = None
                    if len(matched_states) < worker_matched_states_cap or len(sample_pool) < worker_sample_pool_cap:
                        hand_discard_strings = _format_actual_pattern(
                            full_discards_up_to_now, discard_riichi_flags[: j + 1]
                        )

                    if len(matched_states) < worker_matched_states_cap:
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

                    if len(sample_pool) < worker_sample_pool_cap:
                        visible_tiles_dict = dict(player_state.visible_tiles)
                        dora_readable = "".join(
                            MjlogParser.tile_to_string(d) for d in round_players[0].dora_indicators[:5]
                        ) if round_players[0].dora_indicators else "(none)"
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
        logger.error(f"瑙ｆ瀽瀵瑰眬 {log_id} 澶辫触: {e}")

    return {
        "total_matches": total_matches,
        "outcome_wins": outcome_wins,
        "outcome_deal_ins": outcome_deal_ins,
        "pattern_matches": pattern_matches,
        "pattern_distributions": pattern_distributions,
        "matched_states": matched_states,
        "sample_pool": sample_pool,
    }


def _empty_analysis_result(query_pattern: List[str], target_tile: str, pattern_results: Optional[List] = None) -> Dict:
    """鐢ㄦ埛鍙栨秷鏃惰繑鍥炵殑绌虹粨鏋?"""
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
    """瀹炴椂鍒嗘瀽鍣?- 鎸夐渶瑙ｆ瀽瀵瑰眬鏁版嵁"""
    
    def __init__(self, db_path: str):
        """
        鍒濆鍖栧垎鏋愬櫒

        Args:
            db_path: 鏁版嵁搴撹矾寰?
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
        call_area_constraints: Optional[List[str]] = None,  # 鍓湶鍖哄煙绾︽潫锛屾渶澶?涓?AND
        analysis_target: str = "target_count",  # "target_count"=鐩爣鐗屽瓨閲? "tenpai"=鏄惁鍚墝
        turn_range: Optional[Tuple[int, int]] = None,  # (min_turn, max_turn) 濡?(2, 8)
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        matched_states_cap: Optional[int] = None,  # 鏈€澶氫繚鐣欑殑鍖归厤鐘舵€佹潯鏁帮紝None 鐢ㄩ粯璁?MATCHED_STATES_CAP
        analysis_batch_size: Optional[int] = None,  # 姣忔壒浠庢暟鎹簱璇诲彇鐨勫灞€鏁帮紝None 鐢ㄩ粯璁?ANALYSIS_BATCH_SIZE
        exclude_south4: bool = False,  # 南四局打法随点数变化大，True 时跳过
        exclude_south3: bool = False,  # True 时跳过南三局，可与 exclude_south4 同选
        prior_discard_exclusion: Optional[str] = None,  # 鍓嶆涓嶅彲鎵擄紝濡?NOTm銆?mOR2m锛屼笌鑸嶇墝妯″紡鍚屾绛変环鍙樻崲
        max_workers: Optional[int] = None,
        gc_interval_batches: Optional[int] = None,
    ) -> Dict:
        """
        鍒嗘瀽鑸嶇墝妯″紡锛岃绠楃洰鏍囩墝鍦ㄦ墜鐗屼腑鐨勬鐜囥€?
        鏀寔澶氳垗鐗屾ā寮忥細query_items 涓换涓€鍖归厤鍗宠鍏ワ紙A or B or C锛夈€?

        Args:
            query_pattern: 鍗曟ā寮忔椂鐨勮垗鐗屽簭鍒楋紙涓?target_tile 閰嶅锛屽吋瀹规棫鎺ュ彛锛?
            target_tile: 鍗曟ā寮忔椂鐨勭洰鏍囩墝
            query_items: 澶氭ā寮?[(pattern, target), ...]锛屽 [(["7s","9s"],"6s"), (["3m","4m"],"2m")]
            dora_constraint: 瀹濈墝绾︽潫锛堟墍鏈夋ā寮忓叡鐢級
            visible_constraints: 鍙鏋氭暟绾︽潫锛堟墍鏈夋ā寮忓叡鐢級
            riichi_constraint: 绔嬬洿绾︽潫锛堟墍鏈夋ā寮忓叡鐢級
            call_constraint: 鍓湶绾︽潫锛堟墍鏈夋ā寮忓叡鐢級
            turn_range: 宸＄洰鑼冨洿锛堟墍鏈夋ā寮忓叡鐢級
            sample_limit: 鏈€澶у垎鏋愬灞€鏁帮紙None = 鍏ㄩ儴锛?
            progress_callback: 杩涘害鍥炶皟鍑芥暟 (current, total)
            should_cancel: 鍙栨秷妫€鏌ュ嚱鏁?
            total_logs_hint: 瀵瑰眬鎬绘暟棰勪及鍊?
            matched_states_cap: 鏈€澶氫繚鐣欑殑鍖归厤鐘舵€佹潯鏁帮紙鐢ㄤ簬鐣岄潰灞曠ず锛屽奖鍝嶅唴瀛橈級
            analysis_batch_size: 姣忔壒璇诲彇瀵瑰眬鏁帮紙瓒婂ぇ瓒婄渷 SQL 娆℃暟锛屼絾鍗曟壒鍐呭瓨绾?1.2GB/1000 鏉★級

        Returns:
            鍒嗘瀽缁撴灉瀛楀吀锛涘妯″紡鏃跺惈 pattern_results 鍒楄〃
        """
        t0 = time.perf_counter()
        cap = matched_states_cap if matched_states_cap is not None else MATCHED_STATES_CAP
        requested_batch_size = (
            analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        )
        if query_items is not None and len(query_items) > 0:
            items = query_items
        elif query_pattern and target_tile:
            items = [(query_pattern, target_tile)]
        else:
            return _empty_analysis_result([], "")

        first_pattern, first_target = items[0]
        consumed_search = get_consumed_search_patterns(first_pattern)
        consumed_search_list: List[List] = []  # 澶氭ā寮忔椂鏀堕泦鍚勬ā寮忕殑 consumed锛岀敤浜?log 绾ч杩囨护
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
        sample_pool_cap = SAMPLE_POOL_CAP
        total_matches = 0
        outcome_wins_total = 0
        outcome_deal_ins_total = 0
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
        batch_size = _clamp_analysis_batch_size(requested_batch_size, workers, use_parallel)
        analysis_params = None
        if use_parallel:
            worker_matched_states_cap = max(
                PARALLEL_MIN_MATCHED_STATES_PER_LOG,
                cap // max(1, workers * 4),
            )
            worker_matched_states_cap = min(cap, worker_matched_states_cap)
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
                "exclude_south3": exclude_south3,
                "riichi_any": riichi_any,
                "prior_discard_exclusion": prior_discard_exclusion,
                "use_tenpai": use_tenpai,
                "multi_target": multi_target,
                "cap": cap,
                "worker_matched_states_cap": worker_matched_states_cap,
                "worker_sample_pool_cap": min(PARALLEL_MAX_SAMPLE_POOL_PER_LOG, sample_pool_cap),
            }
            logger.info(f"寮€濮嬪垎鏋愶紙骞惰 workers={workers}锛?..")
        # total_logs 鐢ㄤ簬杩涘害鏄剧ず鍜屼覆琛屾椂鐨勬棩蹇?
        if total_logs_hint is not None and total_logs_hint > 0:
            _total_logs = total_logs_hint
        else:
            _total_logs = 0
        if sample_limit:
            _total_logs = min(_total_logs, sample_limit) if _total_logs > 0 else sample_limit
        if not use_parallel:
            logger.info(f"寮€濮嬪垎鏋?{_total_logs:,} 鍦哄灞€...")

        if should_cancel and should_cancel():
            return _empty_analysis_result(first_pattern, first_target)

        conn = _connect_db_memory_efficient(self.db_path)
        cur = conn.cursor()
        _ensure_log_json_column(conn)

        # 閬垮厤 COUNT(*) 鍦ㄥぇ搴撲笂闃诲鏁板垎閽燂紱浣跨敤 total_logs_hint 鎴?sample_limit
        total_logs = _total_logs

        # 鎵归噺璇诲彇骞跺垎鏋愶紙鐢?id 娓告爣鍒嗛〉锛岄伩鍏?OFFSET 瓒婂ぇ瓒婃參锛?
        last_id = None  # None 琛ㄧず绗竴椤碉紱涔嬪悗鐢?WHERE id < last_id
        processed = 0
        pool = ProcessPoolExecutor(max_workers=workers) if use_parallel else None
        batch_count = 0  # 姣?N 鎵归噸鍚?pool 浠ラ噴鏀?worker 鍐呭瓨
        maintenance_batch_index = 0

        while True:
            # 妫€鏌ユ槸鍚﹀彇娑?
            if should_cancel and should_cancel():
                logger.info("analysis cancelled")
                break
            
            # 璇诲彇鎵规锛氫紭鍏?log_json锛坱enhou6 JSON锛夛紝鑻ユ棤鍒欑敤 log锛圶ML锛?
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
                # 骞惰鍒嗘敮
                task_iter = ((log_id, log_content, analysis_params) for log_id, log_content in logs)
                for per_log_result in _iter_pool_results_bounded(
                    pool,
                    _process_one_log_analyze,
                    task_iter,
                    max_in_flight=max(2, workers * PARALLEL_IN_FLIGHT_FACTOR),
                ):
                    if should_cancel and should_cancel():
                        conn.close()
                        if pool:
                            pool.shutdown(wait=False)
                        return _empty_analysis_result(first_pattern, first_target)
                    total_matches += per_log_result["total_matches"]
                    outcome_wins_total += per_log_result.get("outcome_wins", 0)
                    outcome_deal_ins_total += per_log_result.get("outcome_deal_ins", 0)
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
                    # 姣忔壒鍚堝苟鍚庣珛鍗虫埅鏂紝閬垮厤璺ㄦ壒娆″唴瀛樻棤闄愬闀?
                    if len(matched_states) > cap:
                        del matched_states[cap:]
                    if len(sample_pool) > sample_pool_cap:
                        del sample_pool[sample_pool_cap:]
                    processed += 1
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                    if sample_limit and processed >= sample_limit:
                        break
                batch_count += 1
                # 瀹氭湡閲嶅惎 worker 姹狅紝閲婃斁瀛愯繘绋嬪唴 Python 鎸佹湁鐨勫唴瀛橈紙閬垮厤闀挎湡杩愯鍐呭瓨鎸佺画鏀€鍗囷級
                if batch_count >= POOL_RESTART_EVERY_BATCHES:
                    pool.shutdown(wait=True)
                    pool = ProcessPoolExecutor(max_workers=workers)
                    batch_count = 0
            else:
                # 涓茶鍒嗘敮
                for log_id, log_content in logs:
                    if should_cancel and should_cancel():
                        conn.close()
                        if pool:
                            pool.shutdown(wait=False)
                        return _empty_analysis_result(first_pattern, first_target)
                    try:
                        raw = _get_raw_content(log_content)
                        # 绔嬬洿瀹ｈ█妯″紡(r)锛氱墝璋辨棤绔嬬洿鏃跺揩閫熻烦杩?
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
                                # 澶氭ā寮忥細浠呭綋鐗岃氨涓笉鍖呭惈浠讳竴妯″紡鐨?consumed 鏃舵墠璺宠繃
                                if not any(log_contains_consumed(raw, cs) for cs in consumed_search_list):
                                    processed += 1
                                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                                        progress_callback(processed, total_logs)
                                    continue
                        game_states = parse_log_to_game_states(raw)
                    
                        # 鎸夊皬灞€鍒嗙粍锛堟瘡灞€ 4 涓帺瀹讹級
                        round_size = 4
                        # 杩欏嚑琛屾槸鍦ㄦ妸涓€灞€鐗岃氨鐨?game_states 鎸夊皬灞€鍒囧垎鎴愭瘡灞€ 4 涓帺瀹躲€?
                        for round_start in range(0, len(game_states), round_size):
                            round_players = game_states[round_start:round_start + round_size]
                            if len(round_players) < round_size:
                                break
                            rn = getattr(round_players[0], "round_num", 0)
                            if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
                                continue
                            oya = getattr(round_players[0], "oya", 0)

                            dora_str = None
                            if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                                dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                                # 鎸囧畾瀹濈墝锛氬眬绾у垽鏂紝涓嶆弧瓒冲垯璺宠繃鏁村眬
                                # 鐩墠娴嬭瘯锛屼箣鍚庡彲鑳戒篃浣滅瓑浠峰彉浣撳鐞?
                                if dora_constraint != "dora_unrelated" and dora_str != dora_constraint:
                                    continue

                            # 灞€绾?consumed 棰勮繃婊わ細鍗曟ā寮忕敤鍗曚竴 consumed锛涘妯″紡闇€鑷冲皯涓€涓ā寮忕殑 consumed 瀛樺湪
                            if consumed_search_list:
                                if len(items) == 1:
                                    if not round_has_matching_consumed(round_players, consumed_search_list[0]):
                                        continue
                                else:
                                    if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                                        continue

                            # 灞€绾у壇闇茬害鏉熼杩囨护锛歝all_constraint 涓?call_area_constraints 鑻ヤ笉鍙兘婊¤冻鍒欒烦杩囨暣灞€
                            if call_constraint or call_area_constraints:
                                if not round_could_satisfy_call_constraints(
                                    round_players, call_constraint, call_area_constraints, oya
                                ):
                                    continue

                            # 绔嬬洿瀹ｈ█妯″紡(r)锛氭湰灞€鏃犱汉绔嬬洿鏃惰烦杩?
                            if riichi_any:
                                round_has_riichi_decl = any(
                                    any(getattr(d, 'is_riichi_declaration', False) for d in p.discards)
                                    for p in round_players
                                )
                                if not round_has_riichi_decl:
                                    continue

                            # 鍒嗘瀽璇ュ眬姣忎釜鐜╁
                            for player_state in round_players:
                                # 鐜╁绾у壇闇茬害鏉熼杩囨护锛歯o_call 鏃惰鐜╁鏈夊壇闇插垯璺宠繃锛沜all_area 鏃惰鐜╁涓嶅彲鑳芥弧瓒冲垯璺宠繃
                                if call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
                                    continue
                                if call_area_constraints and not player_could_satisfy_call_area_constraints(player_state, oya, call_area_constraints):
                                    continue

                                # 鎻愬彇宸＄洰鑼冨洿鍐呯殑鑸嶇墝
                                if turn_range:
                                    min_turn, max_turn = turn_range
                                    in_range = [(i, d) for i, d in enumerate(player_state.discards)
                                                if min_turn <= d.turn <= max_turn]
                                else:
                                    in_range = [(i, d) for i, d in enumerate(player_state.discards)]
                            
                                if not in_range:
                                    continue
                            
                                # 棰勮浆鎹細浠呭鑼冨洿鍐呰垗鐗?
                                discards_precomputed = [
                                    (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                                    for _, d in in_range
                                ]
                                discarded_bases = set()
                                honor_ctx_base = {
                                    "jikaze": MjlogParser.get_jikaze(player_state.player_id, player_state.oya, player_state.round_num),
                                    "bakaze": ["东", "南", "西", "北"][player_state.round_num // 4],
                                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                                    "calls": getattr(player_state, "calls", []),
                                }
                                discard_riichi_flags = [getattr(in_range[i][1], 'is_riichi_declaration', False) for i in range(len(in_range))]

                                # 閬嶅巻鑼冨洿鍐呰垗鐗?
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
                                            # 蹇呴』鐢辨湰鐜╁瀹屾垚璇ュ壇闇诧紝鍚﹀垯浼氳法鐜╁姹℃煋锛堝 c0p4p 涓?c0p6p 鍚屽眬鏃讹級
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
                                    # 鍓嶆涓嶅彲鎵擄細turn < in_range[0].turn 鐨勮垗鐗屼笉寰楄Е纰扮姝㈤泦鍚堬紙鍙樹綋宸插惈鏄犲皠鍚庣殑 prior锛?
                                    if prior_discard_exclusion and turn_range:
                                        prior_discards = [d for d in player_state.discards if d.turn < in_range[0][1].turn]
                                        excl_str = matched_variant.get("prior_discard_exclusion")
                                        if excl_str:
                                            forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                            if any((d.tile // 4) in forbidden for d in prior_discards):
                                                continue
                                    # 瀹濈墝绾︽潫 dora_unrelated锛氬彇鍐充簬鍖归厤鍒扮殑绛変环鍙樹綋鑺辫壊
                                    if dora_constraint == "dora_unrelated" and dora_str:
                                        pattern_suit = None
                                        for elem in matched_variant["discard"]:
                                            t = elem[0] if isinstance(elem, tuple) else elem
                                            if len(t) >= 2 and t[-1] in 'mps':
                                                pattern_suit = t[-1]
                                                break
                                        if pattern_suit and dora_str[-1] == pattern_suit:
                                            continue
                                
                                    # 绔嬬洿/鍓湶绾︽潫锛氫互鍖归厤搴忓垪鏈€鍚庝竴寮犵墝鎵撳嚭鐬棿鐨勭姸鎬佷负鍑?
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
                                
                                    # 鍓湶鍖哄煙绾︽潫锛氱洰鏍囩帺瀹跺繀椤绘弧瓒虫墍鏈夋寚瀹氱殑鍓湶锛圓ND锛夛紱浠呯粺璁¤宸¤垗鐗屽墠宸插畬鎴愮殑鍓湶
                                    if call_area_constraints:
                                        if not player_satisfies_call_area_constraints(
                                            player_state, round_players, player_state.oya, call_area_constraints,
                                            current_discard_turn=discard.turn,
                                        ):
                                            continue
                                
                                    # 妫€鏌ュ彲瑙佹灇鏁扮害鏉燂紙鍚疂鐗屾寚绀虹墿锛宮jlog_parser 宸插皢鍏惰鍏?visible_tiles锛?
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
                                
                                    # 鎺掗櫎锛氳嫢鏈贰鎵撳嚭鐨勭墝灏辨槸鐩爣鐗岋紝涓嶈鍏ョ粺璁★紙涓庢棤鍓湶鎯呭舰涓€鑷达級锛涘惉鐗屾ā寮忔棤姝ゆ蹇碉紝璺宠繃
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
                                
                                    # 鍖归厤鎴愬姛锛?
                                    total_matches += 1
                                    pattern_matches[matched_idx] += 1
                                    rw = getattr(player_state, "round_winners", [])
                                    rdi = getattr(player_state, "round_deal_in", None)
                                    if rw and player_state.player_id in rw:
                                        outcome_wins_total += 1
                                    if rdi is not None and rdi == player_state.player_id:
                                        outcome_deal_ins_total += 1
                                
                                    # 鑾峰彇璇ュ贰鎵撶墝鍚庣殑鎵嬬墝蹇収锛坥rig_i 涓哄畬鏁磋垗鐗屽簭鍒椾腑鐨勪笅鏍囷級
                                    # 蹇呴』涓?list 浠ヤ繚鐣欏悓绉嶇墝鏋氭暟锛宻et 浼氬悎骞堕噸澶嶅鑷粹€?寮?p鏄剧ず涓?寮犫€?
                                    if orig_i < len(player_state.hand_tiles_history):
                                        hand_at_turn = player_state.hand_tiles_history[orig_i]
                                    else:
                                        logger.warning(
                                            f"hand history too short: turn={orig_i+1}, history_len={len(player_state.hand_tiles_history)}"
                                        )
                                        hand_at_turn = player_state.hand_tiles
                                    hand_at_turn = list(hand_at_turn)  # 鍓湰锛屼笖纭繚涓?list锛堥潪 set锛変互淇濈暀鍚岀鐗屾灇鏁?
                                
                                    # 璁＄畻鐩爣鐗?count锛堝鐩爣鏃跺姣忎釜鐩爣鍒嗗埆缁熻锛?
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
                                
                                    # 璁板綍鍖归厤鐘舵€侊紙浠呬繚鐣欏墠 cap 鏉★紝閬垮厤鍐呭瓨鎸佺画澧為暱锛?
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
                                
                                    # 涓荤粺璁℃椂椤哄甫鏀堕泦瀹屾暣鏍锋湰锛屼緵閲囨牱鐩存帴浣跨敤
                                    if len(sample_pool) < sample_pool_cap:
                                        visible_tiles_dict = dict(player_state.visible_tiles)
                                        dora_readable = "".join(
                                            MjlogParser.tile_to_string(d)
                                            for d in round_players[0].dora_indicators[:5]
                                        ) if round_players[0].dora_indicators else "(none)"
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
                                        # 鍏ュ簱鍓嶆牎楠岋細閬垮厤璇敹鎽稿垏/鎵嬪垏涓嶇鐨勬牱鏈紙濡?NOTm-2s 瑕佹眰 2s 鎵嬪垏锛?pt 涓嶅簲鍖归厤锛?
                                        qp, tt = items[matched_idx]
                                        ok, _ = verify_sample_consistency(sp_entry, qp, tt, visible_constraints)
                                        if ok:
                                            sample_pool.append(sp_entry)
                                    elif len(sample_pool) < 10:  # 浠呭湪鍓嶅嚑鏉℃椂璁板綍锛岄伩鍏嶅埛灞?
                                        logger.debug(f"鏍锋湰鍏ュ簱鏍￠獙鏈€氳繃锛岃烦杩? {sp_entry.get('actual_pattern', [])} vs {qp}")
                    
                    except Exception as e:
                        logger.error(f"瑙ｆ瀽瀵瑰眬 {log_id} 澶辫触: {e}")
                        continue
                
                    processed += 1
                
                    # 杩涘害鍥炶皟锛堟瘡 10 鍦烘洿鏂颁竴娆★紝閬垮厤闀挎椂闂存棤鍙嶉锛?
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                
                    # 杈惧埌鏍锋湰闄愬埗
                    if sample_limit and processed >= sample_limit:
                        break

            last_id = logs[-1][0]
            del logs
            maintenance_batch_index += 1
            if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches):
                gc.collect()
                conn.close()
                conn = _connect_db_memory_efficient(self.db_path)
                cur = conn.cursor()
                _ensure_log_json_column(conn)
                _trim_process_memory()

            # 杈惧埌鏍锋湰闄愬埗
            if sample_limit and processed >= sample_limit:
                break

        if pool is not None:
            pool.shutdown(wait=True)
        conn.close()

        # 骞惰鏃跺彲鑳借秴鍑?cap锛屾埅鏂?
        matched_states = matched_states[:cap]
        sample_pool = sample_pool[:sample_pool_cap]

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

        n = max(1, total_matches)
        result = {
            'total_logs_analyzed': processed,
            'total_matches': total_matches,
            'outcome_wins': outcome_wins_total,
            'outcome_deal_ins': outcome_deal_ins_total,
            'win_rate': outcome_wins_total / n,
            'deal_in_rate': outcome_deal_ins_total / n,
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

        logger.info(f"analysis complete: matched {total_matches} states")
        # 鏍锋湰涓€鑷存€ф牎楠岋細鎶芥煡鏍锋湰姹犱腑鍓嶈嫢骞叉潯锛岀‘璁?actual_pattern 鑳介噸鏂板尮閰?
        if sample_pool and total_matches > 0:
            check_n = min(20, len(sample_pool))
            fail_count = 0
            for s in sample_pool[:check_n]:
                idx = s.get("matched_pattern_idx", 0)
                qp, tt = items[idx]
                ok, err = verify_sample_consistency(s, qp, tt, visible_constraints)
                if not ok:
                    fail_count += 1
                    logger.warning(f"鏍锋湰涓€鑷存€ф牎楠屽け璐?[{s.get('log_id','')}]: {err} | actual={s.get('actual_pattern', [])}")
            if fail_count > 0:
                logger.warning(
                    f"sample consistency warning: {fail_count}/{check_n} sampled entries failed verification"
                )
            else:
                logger.info(f"鏍锋湰涓€鑷存€? 鎶芥煡 {check_n} 鏉″潎閫氳繃")
        if len(items) > 1:
            for pr in pattern_results:
                logger.info(
                    f"  {pr['pattern_str']} -> {pr['target']}: {pr['matches']:,} matches"
                )
        elif multi_target:
            for tk in target_tiles:
                pd = probability_distribution.get(tk, {})
                td = target_count_distribution.get(tk, {})
                logger.info(f"  {tk}: 0寮?{pd.get(0,0):.2f}% 1寮?{pd.get(1,0):.2f}% 2寮?{pd.get(2,0):.2f}% 3寮?{pd.get(3,0):.2f}%")
        elif use_tenpai:
            logger.info(f"  鏈惉鐗? {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 渚?")
            logger.info(f"  鍚墝: {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 渚?")
        elif is_combo:
            logger.info(f"  娌℃湁: {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 渚?")
            logger.info(f"  鏈? {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 渚?")
        else:
            logger.info(f"  鏈?寮? {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 渚?")
            logger.info(f"  鏈?寮? {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 渚?")
            logger.info(f"  鏈?寮? {probability_distribution[2]:.2f}% ({target_count_distribution[2]:,} 渚?")
            logger.info(f"  鏈?寮? {probability_distribution[3]:.2f}% ({target_count_distribution[3]:,} 渚?")
        
        return result

    def compute_pattern_outcome_rates(
        self,
        query_pattern: List[str] = None,
        target_tile: str = None,
        query_items: Optional[List[Tuple[List[str], str]]] = None,
        dora_constraint: Optional[str] = None,
        visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        call_area_constraints: Optional[List[str]] = None,
        turn_range: Optional[Tuple[int, int]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        exclude_south4: bool = False,
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[str] = None,
        max_workers: Optional[int] = None,
    ) -> Dict:
        """
        对达成特定舍牌模式的玩家，统计其最终和了率与放铳率。
        约束只作用到舍牌模式达成的时刻，达成之后到和牌之间的变化不参与约束。

        Returns:
            {"total": N, "wins": W, "deal_ins": D, "win_rate": W/N, "deal_in_rate": D/N, ...}
        """
        result = self.analyze_discard_pattern(
            query_pattern=query_pattern,
            target_tile=target_tile,
            query_items=query_items,
            dora_constraint=dora_constraint,
            visible_constraints=visible_constraints,
            riichi_constraint=riichi_constraint,
            call_constraint=call_constraint,
            call_area_constraints=call_area_constraints,
            turn_range=turn_range,
            sample_limit=sample_limit,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            exclude_south4=exclude_south4,
            exclude_south3=exclude_south3,
            prior_discard_exclusion=prior_discard_exclusion,
            max_workers=max_workers,
        )
        n = max(1, result.get("total_matches", 0))
        return {
            "total": result.get("total_matches", 0),
            "wins": result.get("outcome_wins", 0),
            "deal_ins": result.get("outcome_deal_ins", 0),
            "win_rate": result.get("outcome_wins", 0) / n,
            "deal_in_rate": result.get("outcome_deal_ins", 0) / n,
            "total_logs_analyzed": result.get("total_logs_analyzed", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "query_pattern": result.get("query_pattern", query_pattern or []),
            "query_pattern_str": result.get("query_pattern_str", ""),
            "target_tile": result.get("target_tile", target_tile),
        }

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
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[str] = None,
        max_workers: Optional[int] = None,
        gc_interval_batches: Optional[int] = None,
    ) -> Dict:
        """
        鍗曟鎵弿鎵归噺鍒嗘瀽锛氳垗鐗屾ā寮?脳 宸＄洰鑼冨洿 缃戞牸锛屼竴娆￠亶鍘嗘暟鎹簱寰楀埌鎵€鏈夊崟鍏冩牸鐨勫悎骞舵鐜囥€?
        鐢ㄤ簬鎵归噺鎶樼嚎鍥撅紝閬垮厤 N脳M 娆￠噸澶嶆壂鎻忋€?

        Returns:
            {"table": {(tr_idx, pat_idx): merged_prob}, "total_logs_analyzed": int, "elapsed_seconds": float}
        """
        t0 = time.perf_counter()
        def _target_key(tiles: List[str], is_combo: bool) -> str:
            return "".join(sorted(tiles)) if is_combo else tiles[0]

        use_tenpai = (analysis_target == "tenpai")
        keys = [0, 1] if use_tenpai else [0, 1, 2, 3]
        merge_keys_f = [k for k in merge_keys if k in keys] or keys[:2]

        # 棰勬瀯寤烘瘡鏍肩殑鍙樹綋涓庡厓鏁版嵁
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
        requested_batch_size = (
            analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        )

        # 姣忔牸鍒嗗竷: (tr_idx, pat_idx) -> {0:n, 1:n, 2:n, 3:n} 鎴?tenpai 鏃?{0:n, 1:n}
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
        batch_size = _clamp_analysis_batch_size(requested_batch_size, workers, use_parallel)
        if use_parallel:
            logger.info(f"鍗曟鎵弿鎵归噺鍒嗘瀽锛堝苟琛?workers={workers}锛? {len(patterns)} 妯″紡 脳 {len(turn_ranges)} 宸＄洰")
        else:
            logger.info(
                f"single-scan grid analysis: {len(patterns)} patterns x {len(turn_ranges)} turn ranges = {len(grid_meta)} cells"
            )

        grid_params = {
            "patterns": patterns,
            "grid_meta": grid_meta,
            "dora_constraint": dora_constraint,
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "call_area_constraints": call_area_constraints,
            "consumed_search_list": consumed_search_list,
            "exclude_south4": exclude_south4,
            "exclude_south3": exclude_south3,
            "riichi_any": riichi_any,
            "use_tenpai": use_tenpai,
            "prior_discard_exclusion": prior_discard_exclusion,
        }

        conn = _connect_db_memory_efficient(self.db_path)
        cur = conn.cursor()
        _ensure_log_json_column(conn)
        last_id = None
        processed = 0
        maintenance_batch_index = 0

        while True:
            if should_cancel and should_cancel():
                logger.info("grid analysis cancelled")
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
                task_iter = ((log_id, log_content, grid_params) for log_id, log_content in logs)
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    for per_log_result in _iter_pool_results_bounded(
                        pool,
                        _process_one_log_grid,
                        task_iter,
                        max_in_flight=max(2, workers * PARALLEL_IN_FLIGHT_FACTOR),
                    ):
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
                            rn = getattr(round_players[0], "round_num", 0)
                            if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
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
                                    "bakaze": ["东", "南", "西", "北"][player_state.round_num // 4],
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
                        logger.error(f"瑙ｆ瀽瀵瑰眬 {log_id} 澶辫触: {e}")
                        continue
                    processed += 1
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                    if sample_limit and processed >= sample_limit:
                        break

            last_id = logs[-1][0]
            del logs
            maintenance_batch_index += 1
            if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches):
                gc.collect()
                conn.close()
                conn = _connect_db_memory_efficient(self.db_path)
                cur = conn.cursor()
                _ensure_log_json_column(conn)
                _trim_process_memory()
            if sample_limit and processed >= sample_limit:
                break

        conn.close()

        # 鎸夋牸璁＄畻鍚堝苟姒傜巼涓庡畬鏁村垎甯?
        cell_combo = {(tr_idx, pat_idx): is_combo for tr_idx, pat_idx, _, _, _, is_combo in grid_meta}
        table = {}
        table_dist = {}  # 姣忔牸瀹屾暣鍒嗗竷 {(tr_idx, pat_idx): {0: pct, 1: pct, ...}}
        for (tr_idx, pat_idx), dist in grid_dist.items():
            total_m = sum(dist.values())
            is_combo_cell = cell_combo.get((tr_idx, pat_idx), False)
            k_list = [0, 1] if (use_tenpai or is_combo_cell) else [0, 1, 2, 3]
            if total_m == 0:
                prob = 0.0
                table_dist[(tr_idx, pat_idx)] = {k: 0.0 for k in k_list}
            else:
                prob = sum(dist.get(k, 0) / total_m * 100 for k in merge_keys_f if k in k_list)
                table_dist[(tr_idx, pat_idx)] = {
                    k: round(dist.get(k, 0) / total_m * 100, 2) for k in k_list
                }
            table[(tr_idx, pat_idx)] = round(prob, 2)

        logger.info(f"鎵归噺鍒嗘瀽瀹屾垚: 澶勭悊 {processed:,} 鍦哄灞€")
        return {
            "table": table,
            "table_dist": table_dist,
            "total_logs_analyzed": processed,
            "elapsed_seconds": round(time.perf_counter() - t0, 1),
        }

    def collect_verification_samples(
        self,
        query_pattern: List[str],
        target_tile: str,
        sample_count: int = 10,
        target_count_filter: Optional[int] = None,  # None=鍏ㄩ儴, 0/1/2/3=鍙敹璇ユ暟閲?
        target_tile_filter: Optional[str] = None,  # 澶氱洰鏍囨椂鎸囧畾鎸夊摢涓洰鏍囩瓫閫夛紝濡?"6s"
        dora_constraint: Optional[str] = None,
        visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        turn_range: Optional[Tuple[int, int]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        sample_pool: Optional[List[Dict]] = None,  # 涓荤粺璁℃椂棰勬敹闆嗙殑鏍锋湰姹狅紝鏈夊垯鏃犻渶浜屾閬嶅巻
        analysis_batch_size: Optional[int] = None,
        exclude_south4: bool = False,
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[str] = None,
        gc_interval_batches: Optional[int] = None,
        outcome_filter: Optional[str] = None,  # 鍓嶆涓嶅彲鎵擄紝涓庤垗鐗屾ā寮忓悓姝ョ瓑浠峰彉鎹?
    ) -> List[Dict]:
        """
        鏀堕泦楠岃瘉鏍锋湰锛岀敤浜庝汉宸ュ鐩樻牳瀵广€?
        杩斿洖鍚?log_id銆乷ya銆佹纭皬灞€鏄剧ず绛夊畬鏁翠俊鎭殑鏍锋湰鍒楄〃銆?
        鑻ヤ紶鍏?sample_pool锛堜富缁熻鏃堕鏀堕泦锛夛紝鍒欑洿鎺ヤ粠涓噰鏍凤紝鏃犻渶浜屾鍒嗘瀽銆?
        """
        requested_batch_size = (
            analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        )
        batch_size = _clamp_analysis_batch_size(requested_batch_size, workers=1, use_parallel=False)
        if sample_pool and len(sample_pool) > 0:
            # 浠庨鏀堕泦鐨勬牱鏈睜涓瓫閫夊苟鍙栧墠 N 涓紝鏃犻渶閬嶅巻鐗岃氨锛堜富鍒嗘瀽宸叉帓闄ゅ崡鍥涘眬鍒欐棤闇€鍐嶈繃婊わ級
            candidates = sample_pool
            if outcome_filter is not None:
                if outcome_filter == "win":
                    candidates = [s for s in candidates if s.get("outcome_won")]
                elif outcome_filter == "deal_in":
                    candidates = [s for s in candidates if s.get("outcome_deal_in")]
                elif outcome_filter == "neither":
                    candidates = [s for s in candidates if not s.get("outcome_won") and not s.get("outcome_deal_in")]
            if target_count_filter is not None:
                if target_tile_filter and any("target_counts" in s for s in sample_pool):
                    candidates = [s for s in sample_pool if s.get("target_counts", {}).get(target_tile_filter) == target_count_filter]
                elif any("target_counts" in s for s in sample_pool):
                    candidates = sample_pool
                else:
                    candidates = [s for s in sample_pool if s.get("target_count") == target_count_filter]
            # 澶氭ā寮忔椂锛氫簩娆℃牎楠?actual_pattern 涓庡綋鍓?query 涓€鑷达紝閬垮厤閫変腑 NOTm-2s 鍗存贩鍏?NOTm-2st 鐨勬牱鏈?
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

        conn = _connect_db_memory_efficient(self.db_path)
        cur = conn.cursor()
        _ensure_log_json_column(conn)

        # 閬垮厤鑰楁椂鐨?COUNT(*)锛屼娇鐢?total_logs_hint 鎴?sample_limit
        if total_logs_hint is not None and total_logs_hint > 0:
            total_logs = total_logs_hint
        else:
            total_logs = 0
        if sample_limit:
            total_logs = min(total_logs, sample_limit) if total_logs > 0 else sample_limit

        last_id = None  # 娓告爣鍒嗛〉锛岄伩鍏?OFFSET 瓒婂ぇ瓒婃參
        processed = 0
        maintenance_batch_index = 0

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
                        rn = getattr(round_players[0], "round_num", 0)
                        if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
                            continue
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
                                "bakaze": ["东", "南", "西", "北"][player_state.round_num // 4],
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

                                # 鎺掗櫎锛氳嫢鏈贰鎵撳嚭鐨勭墝灏辨槸鐩爣鐗岋紝涓嶈鍏ワ紙涓庝富缁熻閫昏緫涓€鑷达級
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
                                hand_at_turn = list(hh)  # 蹇呴』 list 浠ヤ繚鐣欏悓绉嶇墝鏋氭暟
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
                                ) if round_players[0].dora_indicators else "(none)"

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
                    logger.debug(f"瑙ｆ瀽 {log_id} 澶辫触: {e}")
                    continue

                if len(samples) >= sample_count:
                    break

            processed += len(logs)
            if progress_callback and processed % 500 == 0:
                progress_callback(processed, total_logs)
            last_id = logs[-1][0]
            del logs
            maintenance_batch_index += 1
            if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches):
                gc.collect()
                conn.close()
                conn = _connect_db_memory_efficient(self.db_path)
                cur = conn.cursor()
                _ensure_log_json_column(conn)
                _trim_process_memory()
            if sample_limit and processed >= sample_limit:
                break
            if len(samples) >= sample_count:
                break

        conn.close()
        return samples


def _visible_count(visible_tiles: dict, tile_str: str) -> int:
    """缁熻鏌愮墝鍦ㄥ彲瑙佺墝涓殑鏋氭暟锛?m/0p/0s 涓?5m/5p/5s 瑙嗕负涓嶅悓鐗岋級"""
    base = MjlogParser.string_to_tile(tile_str)
    equiv = MjlogParser.get_count_equivalent_bases(base)
    return sum(c for t, c in visible_tiles.items() if t // 4 in equiv)


def _fmt_target(mt) -> str:
    """鏍煎紡鍖栫洰鏍囩墝锛堝崟寮犳垨鎼瓙锛?"""
    return "-".join(mt) if isinstance(mt, list) else str(mt)


def _target_desc(s: dict, use_tenpai: bool = False) -> str:
    """鐩爣鐗屾弿杩帮細鍚墝妯″紡=鍚墝/鏈惉鐗岋紱鎼瓙=鏈?鏃犳惌瀛愶紱鍗曞紶=搴旀湁X寮犲湪鎵嬬墝锛涘鐩爣=鍚勭洰鏍囨灇鏁?"""
    if use_tenpai:
        return "tenpai" if s["target_count"] else "noten"
    tc = s.get("target_counts")
    if tc is not None:
        return " ".join(f"{k}:{v}" for k, v in sorted(tc.items()))
    if s.get("is_combo"):
        return "has_combo" if s["target_count"] else "no_combo"
    return "搴旀湁{}寮犲湪鎵嬬墝".format(s["target_count"])


def _target_display_set(mt) -> set:
    """鐩爣鐗岀殑鏄剧ず闆嗗悎锛?m/0p/0s 涓?5m/5p/5s 瑙嗕负涓嶅悓鐗岋級"""
    return set(mt) if isinstance(mt, list) else {mt}


def _actual_pattern_to_full_discards(actual_pattern: List[str]) -> List[Tuple[str, bool]]:
    """
    灏?actual_pattern锛堣垗鐗屽簭鍒楃殑灞曠ず鏍煎紡锛夎浆鍥?full_discards 鏍煎紡渚涘尮閰嶄娇鐢ㄣ€?
    "1m" -> (1m, False) 鎵嬪垏
    "1mt" -> (1m, True) 鎽稿垏
    "1mr" -> (1m, True) 绔嬬洿瀹ｈ█鐗岋紙閫氬父涓烘懜鍒囷級
    """
    result = []
    for s in actual_pattern:
        if not s or len(s) < 2:
            continue
        if s.endswith("t"):
            result.append((s[:-1], True))
        elif s.endswith("r"):
            result.append((s[:-1], True))  # 绔嬬洿瀹ｈ█閫氬父涓烘懜鍒?
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
    楠岃瘉鏍锋湰鐨?actual_pattern 鏄惁涓庢煡璇㈡ā寮忓湪鍖归厤閫昏緫涓嬩竴鑷淬€?
    鐢ㄤ簬妫€娴嬫牱鏈敓鎴愪笌鍒嗘瀽鍖归厤鏄惁鍚屾簮銆?

    Returns:
        (涓€鑷? 閿欒淇℃伅)
    """
    actual = sample.get("actual_pattern")
    if not actual:
        return (False, "鏍锋湰鏃?actual_pattern")
    try:
        full_discards = _actual_pattern_to_full_discards(actual)
        if not full_discards:
            return (False, "actual_pattern parsed to empty sequence")
        first_t = parse_multi_targets(target_tile)[0]
        variant_target = "".join(first_t[0]) if first_t[1] else first_t[0][0]
        variants = generate_equivalent_variants(query_pattern, variant_target, visible_constraints)
        # 鏍锋湰涓嶅寘鍚?honor context锛屽绾暟鐗屾ā寮忓彲鐪佺暐锛涜嫢鏈?zf/kf 浼氬彲鑳借鍒?
        ctx = {}
        matched = match_discard_to_variant(full_discards, variants, ctx)
        if matched:
            return (True, None)
        return (False, "閲嶆柊鍖归厤澶辫触锛歛ctual_pattern 鏃犳硶鍖归厤浠讳竴绛変环鍙樹綋")
    except Exception as e:
        return (False, str(e))


def _target_counts_display_set(tc: dict) -> set:
    """澶氱洰鏍囨椂锛屽悇鐩爣鐗屽強鍏剁瓑浠风墝鐨勬樉绀洪泦鍚?"""
    out = set()
    for k in (tc or {}):
        base = MjlogParser.string_to_tile(k)
        for b in MjlogParser.get_count_equivalent_bases(base):
            out.add(MjlogParser.tile_to_string(b * 4))
    return out


def _outcome_label(s: dict) -> str:
    """和铳率模式下样本的结局标签"""
    if s.get("outcome_won"):
        return "和牌"
    if s.get("outcome_deal_in"):
        return "放铳"
    return "两者皆无"


def format_samples_for_display(samples: List[Dict], query_pattern_str: str, target_tile: str,
                               analysis_target: str = "target_count") -> str:
    """Format verification samples for readable display."""
    use_tenpai = (analysis_target == "tenpai")
    use_outcome = (analysis_target == "outcome")
    is_combo = False if use_tenpai else (samples[0].get("is_combo", False) if samples else False)
    has_multi = bool(samples and samples[0].get("target_counts"))
    target_label = "和铳率" if use_outcome else (target_tile if use_tenpai else (
        f"{target_tile} (combo)" if is_combo else (f"{target_tile} (multi-target)" if has_multi else target_tile)
    ))
    lines = [
        "=" * 80,
        f"Verification Samples: {query_pattern_str} -> {target_label}",
        "=" * 80,
        "",
    ]
    for i, s in enumerate(samples, 1):
        mt = s.get("mapped_target")
        if use_outcome:
            mt_set = set()
        elif has_multi and s.get("target_counts"):
            mt_set = _target_counts_display_set(s["target_counts"])
        else:
            mt_set = _target_display_set(mt) if not use_tenpai and mt else set()
        hand_parts = []
        for t in sorted(s["hand_tiles"], key=lambda x: (x // 4, x)):
            ts = MjlogParser.tile_to_string(t)
            hand_parts.append(f"[{ts}]" if (ts in mt_set) else ts)
        hand_str = " ".join(hand_parts)
        round_display = MjlogParser.format_round_display(s["round_num"], s["honba"])
        wind = MjlogParser.get_player_wind(s["player_id"], s["oya"])
        if use_outcome:
            target_line = f"  结局:        {_outcome_label(s)}"
        elif use_tenpai:
            target_line = f"  Tenpai:      {_target_desc(s, use_tenpai)}"
        elif has_multi and s.get("target_counts"):
            target_line = f"  Target cnts: {_target_desc(s, use_tenpai)}"
        else:
            target_line = f"  Target:      {_fmt_target(mt)} ({_target_desc(s, use_tenpai)})"
        block = [
            f"[Sample {i}]",
            f"  Log ID:      {s['log_id']}",
            f"  Tenhou URL:  https://tenhou.net/4/?log={s['log_id']}&tw={s['player_id']}",
            f"  Round:       {round_display}",
            f"  Seat:        {wind}",
            f"  Turn:        {s['turn']}",
            f"  Dora:        {s['dora_readable']}",
            f"  Discards:    {' '.join(s['actual_pattern'])}",
            target_line,
            f"  Hand({len(s['hand_tiles'])}): {hand_str}",
        ]
        if not use_tenpai and not use_outcome:
            if has_multi and s.get("target_counts"):
                block.append(f"  Visible targets: {s['visible_target']}")
            else:
                block.append(
                    f"  Visible {_fmt_target(mt)}: {s['visible_target']}{'' if s.get('is_combo') else ' tiles'}"
                )
        block.append("")
        lines.extend(block)
    lines.extend([
        "=" * 80,
        f"Total samples: {len(samples)}",
        "=" * 80,
    ])
    return "\n".join(lines)


def get_database_stats(db_path: str) -> Dict:
    """
    鑾峰彇鏁版嵁搴撶粺璁′俊鎭?
    
    Args:
        db_path: 鏁版嵁搴撹矾寰?
        
    Returns:
        缁熻淇℃伅瀛楀吀
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    stats = {}
    
    # 瀵瑰眬鎬绘暟
    cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
    stats['total_logs'] = cur.fetchone()[0]
    
    # 鏁版嵁搴撳ぇ灏?
    import os
    if os.path.exists(db_path):
        stats['db_size_mb'] = os.path.getsize(db_path) / (1024 * 1024)
    
    conn.close()
    
    return stats


