"""
实时查询引擎 - 按需分析模式
直接从 logs 表读取完整牌谱数据并实时解析分析

解析采用 tenhou6 格式（tenhou-paifu-to-json），支持副露等完整信息。
"""
import gc
import os
import time
import sqlite3
import gzip
import json
import threading
import queue
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
    split_discard_pattern,
    _transform_tile_with_mapping,
    get_consumed_search_patterns,
    log_contains_consumed,
    round_has_matching_consumed,
    player_has_matching_consumed,
    player_satisfies_call_area_constraints,
    round_could_satisfy_call_constraints,
    player_could_satisfy_call_area_constraints,
    player_could_satisfy_any_call_area_constraints,
    pattern_has_riichi,
)
from .instant_deal_in import RoundInstantDealInAnalyzer, extract_tenhou6_rounds

logger = logging.getLogger(__name__)

# 每批从数据库读取的对局数。越大则 SQL 次数越少、略快，但单批内存线性增加（约 1.2GB/1000 条，5000 条约 6GB）
ANALYSIS_BATCH_SIZE = 5000
MIN_ANALYSIS_BATCH_SIZE = 100
# 主统计时最多保留的匹配状态条数（仅用于返回给界面，超出部分不保留，避免内存持续增长）
MATCHED_STATES_CAP = 200
SAMPLE_POOL_CAP = 3000
PARALLEL_MIN_MATCHED_STATES_PER_LOG = 4
PARALLEL_MAX_SAMPLE_POOL_PER_LOG = 512  # 提高以保留更多可铳样本，避免单局多匹配时样本池截断
PARALLEL_BATCH_PER_WORKER = 500
PARALLEL_IN_FLIGHT_FACTOR = 2
GC_INTERVAL_BATCHES = 20  # 降低频率，因为现在有管理员强制清理
HIGH_MEMORY_LOAD_RATIO = 0.95
# 并行分析时，每处理多少批后重启worker 池
POOL_RESTART_EVERY_BATCHES = 10
# 即时铳率分析时每批最多读取条数
DEAL_IN_INSTANT_BATCH_SIZE = 8000


def _get_variant_pattern_suit(matched_variant: Optional[Dict]) -> Optional[str]:
    """从变体舍牌中取第一个数牌/赤五的花色，用于假想振听牌的等价变换。无则返回 None。"""
    if not matched_variant:
        return None
    discard = matched_variant.get("discard") or []
    for elem in discard:
        t = elem[0] if isinstance(elem, tuple) else elem
        if isinstance(t, str):
            if t in ("0m", "0p", "0s"):
                return t[-1]
            if len(t) >= 2 and t[-1] in "mps" and (t[0].isdigit() or t in ("0m", "0p", "0s")):
                return t[-1]
            if t.startswith("@r:") and len(t) > 3:
                sub = t[3:]
                if sub in ("0m", "0p", "0s") or (len(sub) >= 2 and sub[-1] in "mps"):
                    return sub[-1]
    return None


def _should_exclude_for_hypothetical_furiten(
    round_instant_analyzer, player_id: int, turn: int,
    hypothetical_furiten_list: List[str], mapping: Optional[Dict[str, str]],
    mapped_targets_to_skip: Optional[Union[str, List[str]]] = None,
    matched_variant: Optional[Dict] = None,
) -> bool:
    """若假想振听牌（且非目标牌）也会放铳，返回 True（应从主铳率中排除）"""
    return len(_get_hypothetical_furiten_deal_in_tiles(
        round_instant_analyzer, player_id, turn,
        hypothetical_furiten_list, mapping, mapped_targets_to_skip, matched_variant,
    )) > 0


def _get_hypothetical_furiten_deal_in_tiles(
    round_instant_analyzer, player_id: int, turn: int,
    hypothetical_furiten_list: List[str], mapping: Optional[Dict[str, str]],
    mapped_targets_to_skip: Optional[Union[str, List[str]]] = None,
    matched_variant: Optional[Dict] = None,
) -> List[str]:
    """返回在该时点会放铳的假想振听牌列表（原输入牌符，用于按牌统计排除数）。
    假想振听牌做等价变换：数牌/赤五按当前变体的 pattern suit 变换（与目标牌一致），字牌仍用 mapping。
    """
    if not hypothetical_furiten_list or round_instant_analyzer is None:
        return []
    mapping = mapping or {"m": "m", "p": "p", "s": "s"}
    variant_suit = _get_variant_pattern_suit(matched_variant)
    skip_set = set()
    if mapped_targets_to_skip is not None:
        skip_set = (
            {mapped_targets_to_skip}
            if isinstance(mapped_targets_to_skip, str)
            else set(mapped_targets_to_skip)
        )
    out = []
    for tile in hypothetical_furiten_list:
        if variant_suit and (tile in ("0m", "0p", "0s") or (len(tile) >= 2 and tile[-1] in "mps" and tile[0].isdigit())):
            mapped = (tile[0] if tile[0].isdigit() else "0") + variant_suit
        else:
            mapped = _transform_tile_with_mapping(tile, mapping)
        if mapped in skip_set:
            continue
        ev = round_instant_analyzer.evaluate(player_id, turn, mapped)
        if ev.get("deal_in_hit"):
            out.append(tile)
    return out


def _is_deal_in_hit_sample(sample: Dict) -> bool:
    """判断样本是否为可铳样本（任一目标牌即时可铳）"""
    if sample.get("deal_in_hit"):
        return True
    for ev in (sample.get("instant_eval_multi") or {}).values():
        if ev.get("deal_in_hit"):
            return True
    return False


class BackgroundLogFetcher:
    """后台对局预取器，用于在计算时并行读取数据库 I/O"""
    def __init__(self, db_path, batch_size, last_id=None):
        self.db_path = db_path
        self.batch_size = batch_size
        self.last_id = last_id
        self.queue = queue.Queue(maxsize=2)  # 预取 2 批，平衡内存与 I/O 覆盖
        self.stop_event = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            # 后台线程开启独立连接
            conn = _connect_db_memory_efficient(self.db_path)
            cur = conn.cursor()
            last_id = self.last_id
            
            while not self.stop_event.is_set():
                if last_id is None:
                    query = """
                        SELECT id, COALESCE(log_json, log) as content
                        FROM logs 
                        WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '')
                        ORDER BY id DESC
                        LIMIT ?
                    """
                    cur.execute(query, (self.batch_size,))
                else:
                    query = """
                        SELECT id, COALESCE(log_json, log) as content
                        FROM logs 
                        WHERE ((log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != ''))
                          AND id < ?
                        ORDER BY id DESC
                        LIMIT ?
                    """
                    cur.execute(query, (last_id, self.batch_size))
                
                rows = cur.fetchall()
                if not rows:
                    self.queue.put(None)  # 结束标志
                    break
                
                last_id = rows[-1][0]
                self.queue.put(rows)
            conn.close()
        except Exception as e:
            logger.error(f"后台预取线程出错: {e}")
            self.error = e
            self.queue.put(None)

    def next_batch(self):
        if self.error:
            raise self.error
        return self.queue.get()

    def stop(self):
        self.stop_event.set()
        try:
            while not self.queue.empty():
                self.queue.get_nowait()
        except:
            pass

def _tile_str_eq(a: str, b: str) -> bool:
    """牌字符串相等（东/1z 等字牌格式统一比较）"""
    if a == b:
        return True
    try:
        return MjlogParser.string_to_tile(a) == MjlogParser.string_to_tile(b)
    except (ValueError, TypeError):
        return False


def _dora_matches_constraint(dora_str: str, dora_constraint: str) -> bool:
    """
    宝牌是否满足约束（支持花色等价：数牌 4s ≡ 4m ≡ 4p）。
    用于「宝牌为 X」约束在等价变体下的正确匹配。
    """
    if dora_str == dora_constraint:
        return True
    # 数牌：同数字不同花色视为等价（1-9m/p/s）
    if len(dora_constraint) == 2 and dora_constraint[-1] in "mps" and dora_constraint[0].isdigit():
        if len(dora_str) == 2 and dora_str[-1] in "mps" and dora_str[0].isdigit():
            return dora_constraint[0] == dora_str[0]
    # 赤五：0m/0p/0s 等价
    if dora_constraint in ("0m", "0p", "0s") and dora_str in ("0m", "0p", "0s"):
        return True
    return False


def _opponent_riichi_happened(discard) -> bool:
    """Whether any non-self player has declared riichi by this discard timing."""
    return bool(getattr(discard, "opponent_riichi_happened", getattr(discard, "riichi_happened", False)))


def _clamp_analysis_batch_size(batch_size: int, workers: int, use_parallel: bool) -> int:
    """Clamp analysis batch size to keep parallel memory usage bounded."""
    requested = max(MIN_ANALYSIS_BATCH_SIZE, int(batch_size))
    # 现在有管理员权限清理 Standby，放宽限制，以 GUI 设定的数值为准
    return requested


def _trim_process_memory() -> None:
    """Best-effort memory trim for long runs (mainly effective on Windows)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        # 1. 清理当前进程的 Working Set（减少任务管理器中的“内存”占用）
        ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
        
        # 2. 尝试提示内核清理系统文件缓存的工作集（针对 Standby 8GB 问题）
        # 参数 -1, -1, 0 尝试强制系统刷新文件缓存
        ctypes.windll.kernel32.SetSystemFileCacheSize(-1, -1, 0)
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


def _should_run_memory_maintenance(batch_index: int, gc_interval_batches: Optional[int] = None, is_instant_mode: bool = False) -> bool:
    """Run maintenance periodically, or early under high system memory pressure."""
    if batch_index <= 0:
        return False
    interval = gc_interval_batches if gc_interval_batches is not None else GC_INTERVAL_BATCHES
    # 即时铳率模式下，由于现在有管理员权限清理 Standby，不需要每个 batch 都清理
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
    """将log 内容转为 gzip 解压后的 UTF-8 字符串"""
    if isinstance(content, bytes):
        try:
            return gzip.decompress(content).decode("utf-8")
        except Exception:
            return content.decode("utf-8", errors="replace")
    return content


def _is_tenhou6_json(raw: str) -> bool:
    """判断是否为 tenhou6 JSON，可做假显字符串搜索"""
    s = raw.strip()
    return s.startswith("{") and "games" in raw


def _raw_to_tenhou6_for_instant(raw: str) -> str:
    """
    将 raw 转为 tenhou6 JSON 字符串，供 extract_tenhou6_rounds 使用。
    若已是 tenhou6 则直接返回；若为 XML 则尝试转换（与 parse_log_to_game_states 一致）。
    否则返回 raw（extract_tenhou6_rounds 对非 tenhou6 会返回 []）。
    """
    if _is_tenhou6_json(raw):
        return raw
    try:
        from .tenhou6_adapter import xml_to_tenhou6_json
        json_str = xml_to_tenhou6_json(raw)
        return json_str if json_str else raw
    except Exception:
        return raw


def _format_actual_pattern(
    full_discards: List[Tuple[str, bool]],
    discard_riichi_flags: List[bool],
) -> List[str]:
    """Format discard tuples for display/storage only when needed."""
    result = []
    for i, (tile_str, is_tsumogiri) in enumerate(full_discards):
        is_riichi = i < len(discard_riichi_flags) and discard_riichi_flags[i]
        if is_riichi:
            if is_tsumogiri:
                result.append(f"{tile_str}tr")  # 摸切立直 (tsumogiri riichi)
            else:
                result.append(f"{tile_str}r")   # 手切立直 (tedashi riichi)
        elif is_tsumogiri:
            result.append(f"{tile_str}t")
        else:
            result.append(tile_str)
    return result


def _game_at_index_contains_consumed(raw: str, game_index: int, consumed_search) -> bool:
    """
    检查 raw（tenhou6 JSON）中第game_index 小局的原始内容是否包含consumed。
    通过提取该小局的JSON 片段进行字符串搜索，避免解析/结构问题导致的跨局污染。
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
    # 恢复性能：增加 SQLite 内部页面缓存
    conn.execute("PRAGMA cache_size = -20000")
    # 启用 Memory Mapped I/O (mmap)，将数据库文件映射到内存地址空间
    # 只要内存足够，这将显著提升读取 BLOB/JSON 字段的速度。这里设为 4GB
    conn.execute("PRAGMA mmap_size = 4294967296")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA read_uncommitted = True")
    return conn


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
            logger.debug(f"添加 log_json 列 {e}")


def parse_log_to_game_states(content: Union[bytes, str]) -> List[GameState]:
    """
    解析对局内容为 GameState 列表。
    使用 tenhou6 格式解析（XML 与 tenhou-paifu-to-json 转为 JSON 后解析）。

    Args:
        content: gzip 压缩的bytes，或 XML/JSON 字符串

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

    # XML锛氶通过 tenhou-paifu-to-json 转为 tenhou6 JSON 后解析
    try:
        from .tenhou6_adapter import xml_to_tenhou6_json, load_tenhou6_json
        json_str = xml_to_tenhou6_json(raw)
        if json_str:
            return load_tenhou6_json(json_str)
    except Exception as e:
        logger.warning(f"XML鈫抰enhou6 转换失败: {e}")

    return []


def _process_one_log_grid(task: Tuple) -> Dict:
    """
    Per-log 并行 worker：处理单条牌谱，返回该log 对各格的增量统计。
    例 ProcessPoolExecutor 调用锛屽繀椤绘槸模块级函数以支持 pickle。
    """
    log_id, log_content, params = task
    patterns = params["patterns"]
    grid_meta = params["grid_meta"]
    dora_constraint = params.get("dora_constraint")
    dora_position_spec = params.get("dora_position_spec") or []
    riichi_constraint = params.get("riichi_constraint")
    call_constraint = params.get("call_constraint")
    call_area_constraints = params.get("call_area_constraints")
    call_area_constraint_sets = params.get("call_area_constraint_sets")
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
                if dora_constraint not in ("dora_unrelated", "dora_matches_position") and not _dora_matches_constraint(dora_str, dora_constraint):
                    continue
            if consumed_search_list:
                if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                    continue
            if call_constraint or call_area_constraints or call_area_constraint_sets:
                if not round_could_satisfy_call_constraints(
                    round_players, call_constraint,
                    None if call_area_constraint_sets else call_area_constraints,
                    oya, call_area_constraint_sets=call_area_constraint_sets
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
                if call_area_constraint_sets:
                    if not player_could_satisfy_any_call_area_constraints(player_state, oya, call_area_constraint_sets):
                        continue
                elif call_area_constraints and not player_could_satisfy_call_area_constraints(
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
                    "visible_tiles": player_state.visible_tiles,
                    "dora_indicators": player_state.dora_indicators,
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
                        if prior_discard_exclusion:
                            excl_str = matched_variant.get("prior_discard_exclusion")
                            if excl_str:
                                forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                start_idx = matched_variant.get("matched_start_index")
                                if start_idx is not None:
                                    prior_tiles = full_discards_up_to_now[:start_idx]
                                    if any(MjlogParser.string_to_tile(t[0]) // 4 in forbidden for t in prior_tiles):
                                        continue
                                elif turn_range:
                                    first_turn_in_range = in_range_for_cell[0][1].turn
                                    prior_discards = [d for d in player_state.discards if d.turn < first_turn_in_range]
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
                        if dora_constraint == "dora_matches_position" and dora_position_spec and dora_str:
                            pos_to_tile = matched_variant.get("position_to_tile") or {}
                            skip_match = False
                            for pos in dora_position_spec:
                                tile_at_pos = pos_to_tile.get(pos)
                                if tile_at_pos is None or not _tile_str_eq(tile_at_pos, dora_str):
                                    skip_match = True
                                    break
                            if skip_match:
                                continue
                        if riichi_constraint and riichi_constraint != "any":
                            if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                                continue
                            if riichi_constraint == "no_riichi" and _opponent_riichi_happened(discard):
                                continue
                        if call_constraint and call_constraint != "any":
                            if call_constraint == "has_call" and not discard.call_happened:
                                continue
                            if call_constraint == "no_call" and discard.call_happened:
                                continue
                        _ca = matched_variant.get("call_area_constraints") or call_area_constraints
                        if _ca:
                            if not player_satisfies_call_area_constraints(
                                player_state, round_players, player_state.oya, _ca,
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
    Per-log 并行 worker：主界面分析，处理单条牌谱，返回该log 的增量统计。
    例 ProcessPoolExecutor 调用，模块级函数以支持 pickle。
    """
    log_id, log_content, params = task
    items = params["items"]
    item_variants = params["item_variants"]
    item_multi_targets = params["item_multi_targets"]
    turn_range = params.get("turn_range")
    dora_constraint = params.get("dora_constraint")
    dora_position_spec = params.get("dora_position_spec") or []
    visible_constraints = params.get("visible_constraints")
    riichi_constraint = params.get("riichi_constraint")
    call_constraint = params.get("call_constraint")
    call_area_constraints = params.get("call_area_constraints")
    call_area_constraint_sets = params.get("call_area_constraint_sets")
    consumed_search_list = params.get("consumed_search_list", [])
    exclude_south4 = params.get("exclude_south4", False)
    exclude_south3 = params.get("exclude_south3", False)
    riichi_any = params.get("riichi_any", False)
    prior_discard_exclusion = params.get("prior_discard_exclusion")
    use_tenpai = params.get("use_tenpai", False)
    use_deal_in_instant = params.get("use_deal_in_instant", False)
    multi_target = params.get("multi_target", False)
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
    deal_in_hits = 0
    deal_in_point_sum = 0
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
    instant_deal_in_dist = {}
    excluded_due_to_hypothetical_furiten = 0
    hypothetical_furiten_list = params.get("hypothetical_furiten_list") or []
    excluded_due_to_hypothetical_furiten_by_tile = {t: 0 for t in hypothetical_furiten_list} if hypothetical_furiten_list else {}
    if use_deal_in_instant and multi_target:
        for tk in [_target_key(t[0], t[1]) for t in item_multi_targets[0]]:
            instant_deal_in_dist[tk] = {"hits": 0, "points": 0}
    # 多模式 + 多目标即时铳率：按模式分别统计，供前端按模式展示各目标牌铳率/铳点/铳度
    instant_deal_in_dist_per_pattern = []
    if use_deal_in_instant and multi_target and len(items) > 1:
        for idx in range(len(items)):
            d = {}
            for t in item_multi_targets[idx]:
                tk = _target_key(t[0], t[1])
                d[tk] = {"hits": 0, "points": 0}
            instant_deal_in_dist_per_pattern.append(d)

    try:
        raw = _get_raw_content(log_content)
        if riichi_any and _is_tenhou6_json(raw):
            if "riichi" not in raw and "reach" not in raw:
                return {"total_matches": 0, "outcome_wins": 0, "outcome_deal_ins": 0,
                        "deal_in_hits": 0, "deal_in_point_sum": 0, "excluded_due_to_hypothetical_furiten": 0,
                        "excluded_due_to_hypothetical_furiten_by_tile": {},
                        "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                        "matched_states": [], "sample_pool": [], "instant_deal_in_dist": instant_deal_in_dist,
                        "instant_deal_in_dist_per_pattern": instant_deal_in_dist_per_pattern}
        if consumed_search_list and _is_tenhou6_json(raw):
            if len(items) == 1:
                if not log_contains_consumed(raw, consumed_search_list[0]):
                    return {"total_matches": 0, "outcome_wins": 0, "outcome_deal_ins": 0,
                            "deal_in_hits": 0, "deal_in_point_sum": 0, "excluded_due_to_hypothetical_furiten": 0,
                            "excluded_due_to_hypothetical_furiten_by_tile": {},
                            "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                            "matched_states": [], "sample_pool": [], "instant_deal_in_dist": instant_deal_in_dist,
                            "instant_deal_in_dist_per_pattern": instant_deal_in_dist_per_pattern}
            else:
                if not any(log_contains_consumed(raw, cs) for cs in consumed_search_list):
                    return {"total_matches": 0, "outcome_wins": 0, "outcome_deal_ins": 0,
                            "deal_in_hits": 0, "deal_in_point_sum": 0, "excluded_due_to_hypothetical_furiten": 0,
                            "excluded_due_to_hypothetical_furiten_by_tile": {},
                            "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                            "matched_states": [], "sample_pool": [], "instant_deal_in_dist": instant_deal_in_dist,
                            "instant_deal_in_dist_per_pattern": instant_deal_in_dist_per_pattern}
        round_payloads = extract_tenhou6_rounds(_raw_to_tenhou6_for_instant(raw)) if use_deal_in_instant else []
        game_states = parse_log_to_game_states(raw)
        round_size = 4

        for round_start in range(0, len(game_states), round_size):
            round_players = game_states[round_start:round_start + round_size]
            if len(round_players) < round_size:
                break
            round_idx = round_start // round_size
            round_payload = round_payloads[round_idx] if round_idx < len(round_payloads) else None
            round_instant_analyzer = None
            rn = getattr(round_players[0], "round_num", 0)
            if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
                continue
            oya = getattr(round_players[0], "oya", 0)
            dora_str = None
            if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                if dora_constraint not in ("dora_unrelated", "dora_matches_position") and not _dora_matches_constraint(dora_str, dora_constraint):
                    continue
            if consumed_search_list:
                if len(items) == 1:
                    if not round_has_matching_consumed(round_players, consumed_search_list[0]):
                        continue
                else:
                    if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                        continue
            if call_constraint or call_area_constraints or call_area_constraint_sets:
                if not round_could_satisfy_call_constraints(
                    round_players, call_constraint,
                    None if call_area_constraint_sets else call_area_constraints,
                    oya, call_area_constraint_sets=call_area_constraint_sets
                ):
                    continue
            if riichi_any:
                if not any(any(getattr(d, 'is_riichi_declaration', False) for d in p.discards) for p in round_players):
                    continue

            for player_state in round_players:
                if call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
                    continue
                if call_area_constraint_sets:
                    if not player_could_satisfy_any_call_area_constraints(player_state, oya, call_area_constraint_sets):
                        continue
                elif call_area_constraints and not player_could_satisfy_call_area_constraints(player_state, oya, call_area_constraints):
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
                    "visible_tiles": player_state.visible_tiles,
                    "dora_indicators": player_state.dora_indicators,
                }
                discard_riichi_flags = [getattr(in_range[i][1], "is_riichi_declaration", False) for i in range(len(in_range))]

                for j, (orig_i, discard) in enumerate(in_range):
                    full_discards_up_to_now = discards_precomputed[: j + 1]

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
                    if prior_discard_exclusion:
                        excl_str = matched_variant.get("prior_discard_exclusion")
                        if excl_str:
                            forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                            start_idx = matched_variant.get("matched_start_index")
                            if start_idx is not None:
                                prior_tiles = full_discards_up_to_now[:start_idx]
                                if any(MjlogParser.string_to_tile(t[0]) // 4 in forbidden for t in prior_tiles):
                                    continue
                            elif turn_range:
                                prior_discards = [d for d in player_state.discards if d.turn < in_range[0][1].turn]
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
                    if dora_constraint == "dora_matches_position" and dora_position_spec and dora_str:
                        pos_to_tile = matched_variant.get("position_to_tile") or {}
                        skip_match = False
                        for pos in dora_position_spec:
                            tile_at_pos = pos_to_tile.get(pos)
                            if tile_at_pos is None or not _tile_str_eq(tile_at_pos, dora_str):
                                skip_match = True
                                break
                        if skip_match:
                            continue
                    if riichi_constraint and riichi_constraint != "any":
                        if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                            continue
                        if riichi_constraint == "no_riichi" and _opponent_riichi_happened(discard):
                            continue
                    if call_constraint and call_constraint != "any":
                        if call_constraint == "has_call" and not discard.call_happened:
                            continue
                        if call_constraint == "no_call" and discard.call_happened:
                            continue
                    _ca = matched_variant.get("call_area_constraints") or call_area_constraints
                    if _ca:
                        if not player_satisfies_call_area_constraints(
                            player_state, round_players, player_state.oya, _ca,
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
                    mapped_target_str = mapped_target if isinstance(mapped_target, str) else (mapped_target[0] if mapped_target else None)
                    mt_for_item = item_multi_targets[matched_idx]
                    if not use_tenpai and not use_deal_in_instant:
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
                        equiv = MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(mapped_target if isinstance(mapped_target, str) else mapped_target[0]))
                        target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                        target_counts = None

                    instant_eval = {}
                    instant_eval_multi = {}
                    if use_deal_in_instant:
                        if round_instant_analyzer is None and round_payload:
                            try:
                                rp_data, rp_events = round_payload
                                norm_oya = params.get("instant_normalize_oya_ron_to_ko", False)
                                round_instant_analyzer = RoundInstantDealInAnalyzer(
                                    rp_data,
                                    rp_events,
                                    player_state.round_num,
                                    player_state.oya,
                                    normalize_oya_ron_to_ko=norm_oya,
                                )
                            except Exception:
                                logger.exception("即时铳率引擎初始化失败（本局跳过）")
                                round_instant_analyzer = None
                        excluded_this_match_by_hypothetical_furiten = False
                        if round_instant_analyzer:
                            if multi_target:
                                # 变体里单目标存成字符串、多目标存成列表，统一为列表后与 mt_item 一一对应
                                m_targets = matched_variant["target"]
                                if isinstance(m_targets, str):
                                    m_targets = [m_targets]
                                if len(m_targets) == len(mt_item):
                                    for tiles, is_combo in mt_item:
                                        if is_combo:
                                            continue
                                        tk = _target_key(tiles, is_combo)
                                        mapped_t = m_targets[mt_item.index((tiles, is_combo))]
                                        ev = round_instant_analyzer.evaluate(
                                            player_state.player_id, discard.turn, mapped_t
                                        )
                                        instant_eval_multi[tk] = ev
                                    any_hit = any(ev.get("deal_in_hit") for ev in instant_eval_multi.values())
                                    excluded_this_match_by_hypothetical_furiten = False
                                    if any_hit and hypothetical_furiten_list:
                                        tiles_deal_in = _get_hypothetical_furiten_deal_in_tiles(
                                            round_instant_analyzer, player_state.player_id, discard.turn,
                                            hypothetical_furiten_list, matched_variant.get("mapping"),
                                            mapped_targets_to_skip=m_targets,
                                            matched_variant=matched_variant,
                                        )
                                        if tiles_deal_in:
                                            excluded_due_to_hypothetical_furiten += 1
                                            excluded_this_match_by_hypothetical_furiten = True
                                            for t in tiles_deal_in:
                                                excluded_due_to_hypothetical_furiten_by_tile[t] = excluded_due_to_hypothetical_furiten_by_tile.get(t, 0) + 1
                                    if not excluded_this_match_by_hypothetical_furiten:
                                        for tk, ev in instant_eval_multi.items():
                                            if ev.get("deal_in_hit"):
                                                instant_deal_in_dist[tk]["hits"] += 1
                                                instant_deal_in_dist[tk]["points"] += int(ev.get("deal_in_point", 0))
                                                if instant_deal_in_dist_per_pattern and tk in instant_deal_in_dist_per_pattern[matched_idx]:
                                                    instant_deal_in_dist_per_pattern[matched_idx][tk]["hits"] += 1
                                                    instant_deal_in_dist_per_pattern[matched_idx][tk]["points"] += int(ev.get("deal_in_point", 0))
                                    first_tk = _target_key(mt_item[0][0], mt_item[0][1])
                                    instant_eval = instant_eval_multi.get(first_tk, {})
                                else:
                                    raise ValueError(
                                        "多目标即时铳率：变体 target 与当前条目标数量不一致 "
                                        "(len(m_targets)=%s, len(mt_item)=%s, matched_idx=%s)"
                                        % (len(m_targets), len(mt_item), matched_idx)
                                    )
                            else:
                                if mapped_target_str:
                                    instant_eval = round_instant_analyzer.evaluate(
                                        player_state.player_id, discard.turn, mapped_target_str
                                    )
                        excluded_this_match_by_hypothetical_furiten = False
                        if instant_eval.get("deal_in_hit"):
                            if hypothetical_furiten_list:
                                tiles_deal_in = _get_hypothetical_furiten_deal_in_tiles(
                                    round_instant_analyzer, player_state.player_id, discard.turn,
                                    hypothetical_furiten_list, matched_variant.get("mapping"),
                                    mapped_targets_to_skip=mapped_target_str,
                                    matched_variant=matched_variant,
                                )
                                if tiles_deal_in:
                                    excluded_due_to_hypothetical_furiten += 1
                                    excluded_this_match_by_hypothetical_furiten = True
                                    for t in tiles_deal_in:
                                        excluded_due_to_hypothetical_furiten_by_tile[t] = excluded_due_to_hypothetical_furiten_by_tile.get(t, 0) + 1
                                else:
                                    deal_in_hits += 1
                                    deal_in_point_sum += int(instant_eval.get("deal_in_point", 0))
                            else:
                                deal_in_hits += 1
                                deal_in_point_sum += int(instant_eval.get("deal_in_point", 0))

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
                        if use_deal_in_instant:
                            deal_in_hit_val = False if excluded_this_match_by_hypothetical_furiten else bool(instant_eval.get("deal_in_hit"))
                            ms_entry.update({
                                "deal_in_hit": deal_in_hit_val,
                                "deal_in_point": 0 if excluded_this_match_by_hypothetical_furiten else int(instant_eval.get("deal_in_point", 0)),
                                "furiten_state": instant_eval.get("furiten_state", "none"),
                                "furiten_reason": instant_eval.get("furiten_reason", ""),
                                "waits_snapshot": instant_eval.get("waits_snapshot", []),
                            })
                            if multi_target:
                                if excluded_this_match_by_hypothetical_furiten:
                                    ms_entry["instant_eval_multi"] = {k: {**v, "deal_in_hit": False, "deal_in_point": 0} for k, v in instant_eval_multi.items()}
                                else:
                                    ms_entry["instant_eval_multi"] = instant_eval_multi
                        matched_states.append(ms_entry)

                    is_deal_in = use_deal_in_instant and not excluded_this_match_by_hypothetical_furiten and (
                        instant_eval.get("deal_in_hit")
                        or any(ev.get("deal_in_hit") for ev in (instant_eval_multi or {}).values())
                    )
                    if len(sample_pool) < worker_sample_pool_cap or is_deal_in:
                        visible_tiles_dict = dict(player_state.visible_tiles)
                        dora_readable = "".join(
                            MjlogParser.tile_to_string(d) for d in round_players[0].dora_indicators[:5]
                        ) if round_players[0].dora_indicators else "(none)"
                        if len(mt_item) > 1 and target_counts:
                            if isinstance(mapped_target, list):
                                visible_target = ", ".join(f"{t}:{_visible_count(visible_tiles_dict, t)}" for t in mapped_target)
                            else:
                                visible_target = ", ".join(f"{k}:{_visible_count(visible_tiles_dict, k)}" for k in target_counts)
                        elif item_combo and isinstance(mapped_target, list):
                            visible_target = ", ".join(f"{t}:{_visible_count(visible_tiles_dict, t)}" for t in mapped_target)
                        else:
                            visible_target = str(_visible_count(visible_tiles_dict, mapped_target if isinstance(mapped_target, str) else mapped_target[0]))
                        call_area = _format_call_area_display(
                            getattr(player_state, "calls", []) or [], discard.turn
                        )
                        sp_entry = {
                            "log_id": log_id, "round_num": player_state.round_num, "honba": player_state.honba,
                            "oya": player_state.oya, "player_id": player_state.player_id, "turn": discard.turn,
                            "actual_pattern": hand_discard_strings.copy(), "mapped_target": mapped_target,
                            "hand_tiles": list(hand_at_turn), "visible_tiles": visible_tiles_dict,
                            "dora_indicators": list(player_state.dora_indicators),
                            "dora_readable": dora_readable, "visible_target": visible_target,
                            "call_area": call_area, "is_combo": item_combo, "matched_pattern_idx": matched_idx,
                            "outcome_won": bool(rw and player_state.player_id in rw),
                            "outcome_deal_in": bool(rdi is not None and rdi == player_state.player_id),
                        }
                        if target_counts is not None:
                            sp_entry["target_counts"] = target_counts
                        else:
                            sp_entry["target_count"] = target_count
                        if use_deal_in_instant:
                            sp_deal_in_hit = False if excluded_this_match_by_hypothetical_furiten else bool(instant_eval.get("deal_in_hit"))
                            sp_entry.update({
                                "deal_in_hit": sp_deal_in_hit,
                                "deal_in_point": 0 if excluded_this_match_by_hypothetical_furiten else int(instant_eval.get("deal_in_point", 0)),
                                "furiten_state": instant_eval.get("furiten_state", "none"),
                                "furiten_reason": instant_eval.get("furiten_reason", ""),
                                "waits_snapshot": instant_eval.get("waits_snapshot", []),
                            })
                            if multi_target:
                                if excluded_this_match_by_hypothetical_furiten:
                                    sp_entry["instant_eval_multi"] = {k: {**v, "deal_in_hit": False, "deal_in_point": 0} for k, v in instant_eval_multi.items()}
                                else:
                                    sp_entry["instant_eval_multi"] = instant_eval_multi
                        # 主分析已匹配成功，直接入库；不再调用 verify_sample_consistency 避免误过滤
                        if len(sample_pool) < worker_sample_pool_cap:
                            sample_pool.append(sp_entry)
                        else:
                            for i, s in enumerate(sample_pool):
                                if not _is_deal_in_hit_sample(s):
                                    sample_pool[i] = sp_entry
                                    break

    except Exception as e:
        logger.error(f"解析对局 {log_id} 失败: {e}")
        if isinstance(e, ValueError):
            raise

    return {
        "total_matches": total_matches,
        "outcome_wins": outcome_wins,
        "outcome_deal_ins": outcome_deal_ins,
        "deal_in_hits": deal_in_hits,
        "deal_in_point_sum": deal_in_point_sum,
        "excluded_due_to_hypothetical_furiten": excluded_due_to_hypothetical_furiten,
        "excluded_due_to_hypothetical_furiten_by_tile": excluded_due_to_hypothetical_furiten_by_tile,
        "pattern_matches": pattern_matches,
        "pattern_distributions": pattern_distributions,
        "matched_states": matched_states,
        "sample_pool": sample_pool,
        "instant_deal_in_dist": instant_deal_in_dist,
        "instant_deal_in_dist_per_pattern": instant_deal_in_dist_per_pattern if instant_deal_in_dist_per_pattern else None,
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
        'deal_in_hits': 0,
        'deal_in_rate': 0.0,
        'deal_in_point_sum': 0,
        'deal_in_point_avg': 0.0,
        'deal_in_intensity': 0.0,
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
    """实时分析器- 按需解析对局数据"""
    
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
        dora_position_spec: Optional[List[int]] = None,
        visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,  # "any" | "has_call" | "no_call"
        call_area_constraints: Optional[List[str]] = None,  # 副露区域约束，最多个AND
        analysis_target: str = "target_count",  # "target_count"=目标牌存在 "tenpai"=是否听牌
        turn_range: Optional[Tuple[int, int]] = None,  # (min_turn, max_turn) 如(2, 8)
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        matched_states_cap: Optional[int] = None,  # 最多保留的匹配状态条数，None 用默认MATCHED_STATES_CAP
        analysis_batch_size: Optional[int] = None,  # 每批从数据库读取的对局数，None 用默认ANALYSIS_BATCH_SIZE
        exclude_south4: bool = False,  # 南四局打法随点数变化大，True 时跳过
        exclude_south3: bool = False,  # True 时跳过南三局，可与 exclude_south4 同选
        prior_discard_exclusion: Optional[str] = None,  # 前段不可打，如NOTm。mOR2m锛屼笌舍牌模式同步等价变换
        hypothetical_furiten_tiles: Optional[str] = None,  # 假想振听牌，如 6p 或 6p,7p；若也会放铳则不计入主铳率
        max_workers: Optional[int] = None,
        gc_interval_batches: Optional[int] = None,
        instant_use_theory_point_only: Optional[bool] = True,  # 即时铳率：True=平均铳点仅按理论点（表宝牌），False=可考虑里宝模拟（若已实现）
        instant_normalize_oya_ron_to_ko: bool = False,  # 即时铳率：True=亲家和牌时铳点按子家算（折半），统一统计口径
    ) -> Dict:
        """
        分析舍牌模式，计算目标牌在手牌中的概率。
        支持多舍牌模式：query_items 中任一匹配即计入（A or B or C锛夈€?

        Args:
            query_pattern: 单模式时的舍牌序列（与 target_tile 配，兼容旧接口）
            target_tile: 单模式时的目标牌
            query_items: 多模式时的 [(pattern, target), ...]，如 [(["7s","9s"],"6s"), (["3m","4m"],"2m")]
            dora_constraint: 宝牌约束（所有模式共用）
            visible_constraints: 可见枚数约束（所有模式共用）
            riichi_constraint: 立直约束（所有模式共用）
            call_constraint: 副露约束（所有模式共用）
            turn_range: 巡目范围（所有模式共用）
            sample_limit: 最大分析对局数（None = 全部）
            progress_callback: 进度回调函数 (current, total)
            should_cancel: 取消检查函数
            total_logs_hint: 对局总数预计值
            matched_states_cap: 最多保留的匹配状态条数（用于界面展示锛屽影响内存）
            analysis_batch_size: 每批读取对局数（越大越省 SQL 次数，但单条内存线 1.2GB/1000 条）

        Returns:
            分析结果字典；多模式时的 pattern_results 列表
        """
        t0 = time.perf_counter()
        cap = matched_states_cap if matched_states_cap is not None else MATCHED_STATES_CAP
        requested_batch_size = (
            analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        )
        if query_items is not None and len(query_items) > 0:
            items = query_items
        elif query_pattern and target_tile:
            # 确保舍牌模式为列表，否则等价变换会失败（字符串会被逐字符解析）
            pattern_list = (
                split_discard_pattern(query_pattern)
                if isinstance(query_pattern, str)
                else list(query_pattern)
            )
            items = [(pattern_list, target_tile)]
        else:
            return _empty_analysis_result([], "")

        first_pattern, first_target = items[0]
        consumed_search = get_consumed_search_patterns(first_pattern)
        consumed_search_list: List[List] = []  # 多模式时收集各模式的 consumed，用于log 级预过滤
        for p, _ in items:
            cs = get_consumed_search_patterns(p)
            if cs:
                consumed_search = consumed_search or cs
                consumed_search_list.append(cs)
        if not consumed_search_list:
            consumed_search_list = [consumed_search] if consumed_search else []
        riichi_any = any(pattern_has_riichi(p) for p, _ in items)
        use_deal_in_instant = (analysis_target == "deal_in_instant")
        hypothetical_furiten_list: List[str] = []
        if hypothetical_furiten_tiles and hypothetical_furiten_tiles.strip():
            hypothetical_furiten_list = [x.strip() for x in hypothetical_furiten_tiles.replace(",", " ").split() if x.strip()]

        def _target_key(tiles: List[str], is_combo: bool) -> str:
            return "".join(sorted(tiles)) if is_combo else tiles[0]

        def _target_str_for_variant(tiles: List[str], is_combo: bool) -> str:
            return "".join(tiles) if is_combo else tiles[0]

        item_variants = []
        item_multi_targets: List[List[Tuple[List[str], bool]]] = []
        for p, t in items:
            multi_t = parse_multi_targets(t)
            if use_deal_in_instant:
                if multi_t[0][1]:
                    raise ValueError("即时铳率分析不支持 combo 目标牌（如 4s-5s），请使用逗号分隔的多目标（如 4s,5s）")
                # 现在允许 len(multi_t) > 1 了
                pass
            item_multi_targets.append(multi_t)
            first_t = multi_t[0]
            # 多目标时传入完整目标串（如 3p,4p），使变体 target 为列表，供即时铳率逐目标评估
            variant_target = (
                ",".join(t[0][0] for t in multi_t)
                if len(multi_t) > 1
                else _target_str_for_variant(first_t[0], first_t[1])
            )
            vars_p = generate_equivalent_variants(p, variant_target, visible_constraints, prior_discard_exclusion, call_area_constraints)
            combo = first_t[1] if len(multi_t) == 1 else False
            item_variants.append((vars_p, t, combo))

        variants = item_variants[0][0]
        # 收集副露区域约束的等价变体组（每变体一套），用于预过滤
        _call_area_sets = []
        if call_area_constraints:
            seen = set()
            for v in variants:
                ca = v.get("call_area_constraints")
                if ca and tuple(ca) not in seen:
                    seen.add(tuple(ca))
                    _call_area_sets.append(ca)
        call_area_constraint_sets = _call_area_sets if _call_area_sets else None
        multi_targets = item_multi_targets[0]
        first_t0 = multi_targets[0]
        is_combo = first_t0[1] and len(multi_targets) == 1
        use_tenpai = (analysis_target == "tenpai")
        multi_target = len(multi_targets) > 1

        matched_states = []
        sample_pool: List[Dict] = []
        sample_pool_cap = SAMPLE_POOL_CAP
        total_matches = 0
        deal_in_hits_total = 0
        deal_in_point_sum_total = 0
        excluded_due_to_hypothetical_furiten = 0
        excluded_due_to_hypothetical_furiten_by_tile: Dict[str, int] = {
            t: 0 for t in hypothetical_furiten_list
        } if hypothetical_furiten_list else {}
        # 即时铳率多目标分布：target_key -> {"hits": n, "points": n}
        instant_deal_in_dist = {}
        if use_deal_in_instant and multi_target:
            for tk in [_target_key(t[0], t[1]) for t in multi_targets]:
                instant_deal_in_dist[tk] = {"hits": 0, "points": 0}
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
        # 多模式 + 多目标即时铳率：按模式分别统计，用于每个 pattern_results 的 multi_instant_stats
        pattern_instant_dists: List[Dict[str, Dict[str, int]]] = []
        if len(items) > 1 and use_deal_in_instant and multi_target:
            for idx in range(len(items)):
                d = {}
                for t in item_multi_targets[idx]:
                    tk = _target_key(t[0], t[1])
                    d[tk] = {"hits": 0, "points": 0}
                pattern_instant_dists.append(d)
        target_count_distribution = pattern_distributions[0]

        use_parallel = (max_workers is not None and max_workers > 1)
        workers = min(max(1, max_workers or 1), os.cpu_count() or 4)
        if use_deal_in_instant and (max_workers or 1) > 1:
            logger.info(f"即时铳率模式启用并行分析（workers={workers}）")
        elif use_deal_in_instant:
            logger.info("即时铳率模式串行分析（完整事件重放+振听判定）")
        batch_size = _clamp_analysis_batch_size(requested_batch_size, workers, use_parallel)
        # 串行视为 workers=1 的并行，复用同一套 analysis_params 与 _process_one_log_analyze
        if use_parallel:
            worker_matched_states_cap = max(
                PARALLEL_MIN_MATCHED_STATES_PER_LOG,
                cap // max(1, workers * 4),
            )
            worker_matched_states_cap = min(cap, worker_matched_states_cap)
            worker_sample_pool_cap = min(PARALLEL_MAX_SAMPLE_POOL_PER_LOG, sample_pool_cap)
        else:
            worker_matched_states_cap = cap
            worker_sample_pool_cap = sample_pool_cap
        analysis_params = {
            "items": items,
            "item_variants": item_variants,
            "item_multi_targets": item_multi_targets,
            "turn_range": turn_range,
            "dora_constraint": dora_constraint,
            "dora_position_spec": dora_position_spec or [],
            "visible_constraints": visible_constraints,
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "call_area_constraints": call_area_constraints,
            "call_area_constraint_sets": call_area_constraint_sets,
            "consumed_search_list": consumed_search_list,
            "exclude_south4": exclude_south4,
            "exclude_south3": exclude_south3,
            "riichi_any": riichi_any,
            "prior_discard_exclusion": prior_discard_exclusion,
            "use_tenpai": use_tenpai,
            "use_deal_in_instant": use_deal_in_instant,
            "multi_target": multi_target,
            "hypothetical_furiten_list": hypothetical_furiten_list,
            "instant_use_theory_point_only": instant_use_theory_point_only if use_deal_in_instant else True,
            "instant_normalize_oya_ron_to_ko": instant_normalize_oya_ron_to_ko if use_deal_in_instant else False,
            "cap": cap,
            "worker_matched_states_cap": worker_matched_states_cap,
            "worker_sample_pool_cap": worker_sample_pool_cap,
        }
        if use_parallel:
            logger.info(f"开始分析（并行 workers={workers}）..")
        # total_logs 用于进度显示和串行时的日志
        if total_logs_hint is not None and total_logs_hint > 0:
            _total_logs = total_logs_hint
        else:
            _total_logs = 0
        if sample_limit:
            _total_logs = min(_total_logs, sample_limit) if _total_logs > 0 else sample_limit
        if not use_parallel:
            logger.info(f"开始分析{_total_logs:,} 场对局...")

        if should_cancel and should_cancel():
            return _empty_analysis_result(first_pattern, first_target)

        conn = _connect_db_memory_efficient(self.db_path)
        cur = conn.cursor()
        _ensure_log_json_column(conn)

        # 避免 COUNT(*) 鍦ㄥぇ搴撲笂罚秒等燂紱使用 total_logs_hint 鎴?sample_limit
        total_logs = _total_logs

        # 批量读取并分析（用id 游标分页），避免OFFSET 越大越慢）
        last_id = None  # None 表示第一页；之后用WHERE id < last_id
        processed = 0
        pool = ProcessPoolExecutor(max_workers=workers) if use_parallel else None
        batch_count = 0  # 每 N 批重启pool 以释放 worker 内容瓨
        maintenance_batch_index = 0

        # 启动后台预取线程，掩盖数据库 I/O 延迟
        fetcher = BackgroundLogFetcher(self.db_path, batch_size, last_id=None)
        
        while True:
            # 检查是否取消
            if should_cancel and should_cancel():
                logger.info("analysis cancelled")
                break
            
            # 从预取队列获取一批对局数据（如果后台还没读完，这里会阻塞等待，但通常已经预取好了）
            logs = fetcher.next_batch()
            if not logs:
                break
            
            if use_parallel and pool is not None:
                # 并行分支
                task_iter = ((log_id, log_content, analysis_params) for log_id, log_content in logs)
                for per_log_result in _iter_pool_results_bounded(
                    pool,
                    _process_one_log_analyze,
                    task_iter,
                    max_in_flight=max(2, workers * PARALLEL_IN_FLIGHT_FACTOR),
                ):
                    if should_cancel and should_cancel():
                        fetcher.stop()
                        conn.close()
                        gc.collect()
                        _trim_process_memory()
                        if pool:
                            pool.shutdown(wait=False)
                        return _empty_analysis_result(first_pattern, first_target)
                    total_matches += per_log_result["total_matches"]
                    outcome_wins_total += per_log_result.get("outcome_wins", 0)
                    outcome_deal_ins_total += per_log_result.get("outcome_deal_ins", 0)
                    deal_in_hits_total += per_log_result.get("deal_in_hits", 0)
                    deal_in_point_sum_total += per_log_result.get("deal_in_point_sum", 0)
                    excluded_due_to_hypothetical_furiten += per_log_result.get("excluded_due_to_hypothetical_furiten", 0)
                    for t, c in per_log_result.get("excluded_due_to_hypothetical_furiten_by_tile", {}).items():
                        excluded_due_to_hypothetical_furiten_by_tile[t] = excluded_due_to_hypothetical_furiten_by_tile.get(t, 0) + c
                    if use_deal_in_instant and multi_target:
                        wid = per_log_result.get("instant_deal_in_dist", {})
                        for tk, stats in wid.items():
                            if tk in instant_deal_in_dist:
                                instant_deal_in_dist[tk]["hits"] += stats.get("hits", 0)
                                instant_deal_in_dist[tk]["points"] += stats.get("points", 0)
                    if pattern_instant_dists:
                        per_pat = per_log_result.get("instant_deal_in_dist_per_pattern") or []
                        for idx, pat_dist in enumerate(per_pat):
                            if idx < len(pattern_instant_dists):
                                for tk, st in pat_dist.items():
                                    if tk in pattern_instant_dists[idx]:
                                        pattern_instant_dists[idx][tk]["hits"] += st.get("hits", 0)
                                        pattern_instant_dists[idx][tk]["points"] += st.get("points", 0)
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
                    # 每批合并后立即截断，避免跨批次内存无限增长
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
                # 定期重启 worker 池，释放子进程内 Python 持有的内存
                if batch_count >= POOL_RESTART_EVERY_BATCHES:
                    pool.shutdown(wait=True)
                    pool = ProcessPoolExecutor(max_workers=workers)
                    batch_count = 0
            else:
                # 串行 = 单 worker，复用 _process_one_log_analyze（与并行同一算法）
                for log_id, log_content in logs:
                    if should_cancel and should_cancel():
                        fetcher.stop()
                        conn.close()
                        gc.collect()
                        _trim_process_memory()
                        if pool:
                            pool.shutdown(wait=False)
                        return _empty_analysis_result(first_pattern, first_target)
                    try:
                        per_log_result = _process_one_log_analyze((log_id, log_content, analysis_params))
                    except Exception as e:
                        logger.error(f"解析对局 {log_id} 失败: {e}")
                        if isinstance(e, ValueError):
                            raise
                        continue
                    total_matches += per_log_result["total_matches"]
                    outcome_wins_total += per_log_result.get("outcome_wins", 0)
                    outcome_deal_ins_total += per_log_result.get("outcome_deal_ins", 0)
                    deal_in_hits_total += per_log_result.get("deal_in_hits", 0)
                    deal_in_point_sum_total += per_log_result.get("deal_in_point_sum", 0)
                    excluded_due_to_hypothetical_furiten += per_log_result.get("excluded_due_to_hypothetical_furiten", 0)
                    for t, c in per_log_result.get("excluded_due_to_hypothetical_furiten_by_tile", {}).items():
                        excluded_due_to_hypothetical_furiten_by_tile[t] = excluded_due_to_hypothetical_furiten_by_tile.get(t, 0) + c
                    if use_deal_in_instant and multi_target:
                        wid = per_log_result.get("instant_deal_in_dist", {})
                        for tk, stats in wid.items():
                            if tk in instant_deal_in_dist:
                                instant_deal_in_dist[tk]["hits"] += stats.get("hits", 0)
                                instant_deal_in_dist[tk]["points"] += stats.get("points", 0)
                    if pattern_instant_dists:
                        per_pat = per_log_result.get("instant_deal_in_dist_per_pattern") or []
                        for idx, pat_dist in enumerate(per_pat):
                            if idx < len(pattern_instant_dists):
                                for tk, st in pat_dist.items():
                                    if tk in pattern_instant_dists[idx]:
                                        pattern_instant_dists[idx][tk]["hits"] += st.get("hits", 0)
                                        pattern_instant_dists[idx][tk]["points"] += st.get("points", 0)
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
                    if len(matched_states) > cap:
                        del matched_states[cap:]
                    if len(sample_pool) > sample_pool_cap:
                        del sample_pool[sample_pool_cap:]
                    processed += 1
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                    if sample_limit and processed >= sample_limit:
                        break

            # 批次结束后的维护（不要在此处调用 fetcher.stop()，否则会杀死预取线程导致下一批永远取不到）
            maintenance_batch_index += 1
            if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches, use_deal_in_instant):
                gc.collect()
                try:
                    conn.execute("PRAGMA shrink_memory")
                except:
                    pass
                conn.close()
                conn = _connect_db_memory_efficient(self.db_path)
                cur = conn.cursor()
                _ensure_log_json_column(conn)
                _trim_process_memory()

            # 达到样本限制
            if sample_limit and processed >= sample_limit:
                break

        if pool is not None:
            pool.shutdown(wait=True)
        fetcher.stop()
        conn.close()
        gc.collect()
        _trim_process_memory()

        # 并行时可能超过 cap，截断
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
            pr_entry = {
                'pattern': p,
                'pattern_str': '-'.join(p),
                'target': t,
                'matches': pattern_matches[idx],
                'target_count_distribution': dist,
                'probability_distribution': prob,
                'is_combo': item_variants[idx][2],
                'multi_target': len(mt) > 1,
                'target_tiles': [_target_key(x[0], x[1]) for x in mt] if len(mt) > 1 else None,
            }
            if pattern_instant_dists and idx < len(pattern_instant_dists) and len(mt) > 1:
                n_idx = max(1, pattern_matches[idx])
                multi_instant_stats_idx = {}
                for tk, st in pattern_instant_dists[idx].items():
                    hits = st["hits"]
                    rate = hits / n_idx if n_idx > 0 else 0.0
                    p_avg = st["points"] / hits if hits > 0 else 0.0
                    multi_instant_stats_idx[tk] = {
                        "hits": hits,
                        "rate": rate,
                        "point_avg": p_avg,
                        "intensity": rate * p_avg,
                    }
                pr_entry["multi_instant_stats"] = multi_instant_stats_idx
            pattern_results.append(pr_entry)

        n = max(1, total_matches)
        instant_deal_in_rate = (deal_in_hits_total / n) if total_matches > 0 else 0.0
        instant_deal_in_point_avg = (
            deal_in_point_sum_total / max(1, deal_in_hits_total)
            if deal_in_hits_total > 0 else 0.0
        )
        instant_deal_in_intensity = instant_deal_in_rate * instant_deal_in_point_avg

        # 即时铳率：如果是多目标，合并详细信息
        multi_instant_stats = {}
        if use_deal_in_instant and multi_target:
            for tk, stats in instant_deal_in_dist.items():
                hits = stats["hits"]
                rate = hits / n if n > 0 else 0.0
                p_avg = stats["points"] / hits if hits > 0 else 0.0
                multi_instant_stats[tk] = {
                    "hits": hits,
                    "rate": rate,
                    "point_avg": p_avg,
                    "intensity": rate * p_avg
                }

        result = {
            'total_logs_analyzed': processed,
            'total_matches': total_matches,
            'deal_in_hits': deal_in_hits_total,
            'excluded_due_to_hypothetical_furiten': excluded_due_to_hypothetical_furiten,
            'excluded_due_to_hypothetical_furiten_by_tile': excluded_due_to_hypothetical_furiten_by_tile,
            'deal_in_point_sum': deal_in_point_sum_total,
            'deal_in_point_avg': instant_deal_in_point_avg,
            'deal_in_intensity': instant_deal_in_intensity,
            'instant_use_theory_point_only': instant_use_theory_point_only if use_deal_in_instant else None,
            'multi_instant_stats': multi_instant_stats if use_deal_in_instant and multi_target else None,
            'outcome_wins': outcome_wins_total,
            'outcome_deal_ins': outcome_deal_ins_total,
            'win_rate': outcome_wins_total / n,
            'deal_in_rate': instant_deal_in_rate if use_deal_in_instant else (outcome_deal_ins_total / n),
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
            'instant_use_theory_point_only': instant_use_theory_point_only if use_deal_in_instant else None,
            'instant_normalize_oya_ron_to_ko': instant_normalize_oya_ron_to_ko if use_deal_in_instant else None,
        }

        logger.info(f"analysis complete: matched {total_matches} states")
        # 样本一致性校验：抽查样本池中前若干条，确认 actual_pattern 能重新匹配
        if sample_pool and total_matches > 0:
            check_n = min(20, len(sample_pool))
            fail_count = 0
            for s in sample_pool[:check_n]:
                idx = s.get("matched_pattern_idx", 0)
                qp, tt = items[idx]
                ok, err = verify_sample_consistency(
                    s, qp, tt, visible_constraints,
                    prior_discard_exclusion=prior_discard_exclusion,
                    call_area_constraints=call_area_constraints,
                )
                if not ok:
                    fail_count += 1
                    logger.warning(f"样本一致性校验失败[{s.get('log_id','')}]: {err} | actual={s.get('actual_pattern', [])}")
            if fail_count > 0:
                logger.warning(
                    f"sample consistency warning: {fail_count}/{check_n} sampled entries failed verification"
                )
            else:
                logger.info(f"样本一致性 抽查 {check_n} 次均通过")
        if len(items) > 1:
            for pr in pattern_results:
                logger.info(
                    f"  {pr['pattern_str']} -> {pr['target']}: {pr['matches']:,} matches"
                )
        elif use_deal_in_instant:
            logger.info(
                "  即时铳率: %.2f%% (%s/%s)",
                result.get("deal_in_rate", 0.0) * 100,
                result.get("deal_in_hits", 0),
                total_matches,
            )
            logger.info("  平均铳点: %.1f", result.get("deal_in_point_avg", 0.0))
            logger.info("  铳度: %.2f", result.get("deal_in_intensity", 0.0))
        elif multi_target:
            for tk in target_tiles:
                pd = probability_distribution.get(tk, {})
                td = target_count_distribution.get(tk, {})
                logger.info(f"  {tk}: 0寮?{pd.get(0,0):.2f}% 1寮?{pd.get(1,0):.2f}% 2寮?{pd.get(2,0):.2f}% 3寮?{pd.get(3,0):.2f}%")
        elif use_tenpai:
            logger.info(f"  鏈惉牌 {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 例")
            logger.info(f"  听牌: {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 例")
        elif is_combo:
            logger.info(f"  娌℃湁: {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 例")
            logger.info(f"  鏈? {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 例")
        else:
            logger.info(f"  鏈?寮? {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 例")
            logger.info(f"  鏈?寮? {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 例")
            logger.info(f"  鏈?寮? {probability_distribution[2]:.2f}% ({target_count_distribution[2]:,} 例")
            logger.info(f"  鏈?寮? {probability_distribution[3]:.2f}% ({target_count_distribution[3]:,} 例")
        
        return result

    def compute_pattern_outcome_rates(
        self,
        query_pattern: List[str] = None,
        target_tile: str = None,
        query_items: Optional[List[Tuple[List[str], str]]] = None,
        dora_constraint: Optional[str] = None,
        dora_position_spec: Optional[List[int]] = None,
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
            dora_position_spec=dora_position_spec,
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

    def compute_instant_deal_in_metrics(
        self,
        query_pattern: List[str] = None,
        target_tile: str = None,
        query_items: Optional[List[Tuple[List[str], str]]] = None,
        dora_constraint: Optional[str] = None,
        dora_position_spec: Optional[List[int]] = None,
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
        gc_interval_batches: Optional[int] = None,
    ) -> Dict:
        """
        即时铳率统计：仅看命中样本当巡时点，不看整局终局结果。
        """
        result = self.analyze_discard_pattern(
            query_pattern=query_pattern,
            target_tile=target_tile,
            query_items=query_items,
            dora_constraint=dora_constraint,
            dora_position_spec=dora_position_spec,
            visible_constraints=visible_constraints,
            riichi_constraint=riichi_constraint,
            call_constraint=call_constraint,
            call_area_constraints=call_area_constraints,
            analysis_target="deal_in_instant",
            turn_range=turn_range,
            sample_limit=sample_limit,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            exclude_south4=exclude_south4,
            exclude_south3=exclude_south3,
            prior_discard_exclusion=prior_discard_exclusion,
            max_workers=max_workers,
            gc_interval_batches=gc_interval_batches,
        )
        return {
            "analysis_target": "deal_in_instant",
            "total_matches": result.get("total_matches", 0),
            "deal_in_hits": result.get("deal_in_hits", 0),
            "deal_in_rate": result.get("deal_in_rate", 0.0),
            "deal_in_point_sum": result.get("deal_in_point_sum", 0),
            "deal_in_point_avg": result.get("deal_in_point_avg", 0.0),
            "deal_in_intensity": result.get("deal_in_intensity", 0.0),
            "multi_instant_stats": result.get("multi_instant_stats"),
            "total_logs_analyzed": result.get("total_logs_analyzed", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "query_pattern": result.get("query_pattern", query_pattern or []),
            "query_pattern_str": result.get("query_pattern_str", ""),
            "target_tile": result.get("target_tile", target_tile),
            "sample_pool": result.get("sample_pool", []),
        }

    def analyze_discard_pattern_grid(
        self,
        patterns: List[Tuple[List[str], str]],
        turn_ranges: List[Tuple[int, int]],
        merge_keys: List[int],
        analysis_target: str = "target_count",
        dora_constraint: Optional[str] = None,
        dora_position_spec: Optional[List[int]] = None,
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
        单次扫描批量分析：舍牌模式 × 巡目范围 网格，一次遍历数据库得到所有单格格的合并率。
        用于批量折线图，避免 N×M 次重复扫描。

        Returns:
            {"table": {(tr_idx, pat_idx): merged_prob}, "total_logs_analyzed": int, "elapsed_seconds": float}
        """
        t0 = time.perf_counter()
        def _target_key(tiles: List[str], is_combo: bool) -> str:
            return "".join(sorted(tiles)) if is_combo else tiles[0]

        use_tenpai = (analysis_target == "tenpai")
        # 是否为「即时铳率」分析模式；用于 _should_run_memory_maintenance 区分内存维护策略（即时铳率下不必每批都做 GC）
        use_deal_in_instant = (analysis_target == "deal_in_instant")
        keys = [0, 1] if use_tenpai else [0, 1, 2, 3]
        merge_keys_f = [k for k in merge_keys if k in keys] or keys[:2]

        # 棰勬瀯寤烘瘡鏍肩殑变体与元数据
        grid_meta = []  # [(tr_idx, pat_idx, turn_range, item_variants, item_multi_targets, is_combo), ...]
        consumed_search_list = []
        for pat_idx, (pattern, target) in enumerate(patterns):
            multi_t = parse_multi_targets(target)
            first_t = multi_t[0]
            variant_target = ("".join(first_t[0]) if first_t[1] else first_t[0][0])
            vars_p = generate_equivalent_variants(
                pattern, variant_target, visible_constraints, prior_discard_exclusion, call_area_constraints
            )
            is_combo = first_t[1] and len(multi_t) == 1
            cs = get_consumed_search_patterns(pattern)
            if cs:
                consumed_search_list.append(cs)
            for tr_idx, turn_range in enumerate(turn_ranges):
                grid_meta.append((tr_idx, pat_idx, turn_range, vars_p, multi_t, is_combo))

        # 副露区域约束等价变体组（从首个模式的变体中收集）
        _gca_sets = []
        if call_area_constraints and grid_meta:
            first_vars = grid_meta[0][3]
            seen = set()
            for v in first_vars:
                ca = v.get("call_area_constraints")
                if ca and tuple(ca) not in seen:
                    seen.add(tuple(ca))
                    _gca_sets.append(ca)
        grid_call_area_sets = _gca_sets if _gca_sets else None

        riichi_any = any(pattern_has_riichi(p) for p, _ in patterns)
        requested_batch_size = (
            analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        )

        # 姣忔牸分布: (tr_idx, pat_idx) -> {0:n, 1:n, 2:n, 3:n} 鎴?tenpai 鏃?{0:n, 1:n}
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
            logger.info(f"单次扫描批量分析锛堝苟琛?workers={workers}） {len(patterns)} 模式 脳 {len(turn_ranges)} 巡目")
        else:
            logger.info(
                f"single-scan grid analysis: {len(patterns)} patterns x {len(turn_ranges)} turn ranges = {len(grid_meta)} cells"
            )

        grid_params = {
            "patterns": patterns,
            "grid_meta": grid_meta,
            "dora_constraint": dora_constraint,
            "dora_position_spec": dora_position_spec or [],
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "call_area_constraints": call_area_constraints,
            "call_area_constraint_sets": grid_call_area_sets,
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
                            gc.collect()
                            _trim_process_memory()
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
                call_area_constraint_sets = grid_call_area_sets
                for log_id, log_content in logs:
                    if should_cancel and should_cancel():
                        conn.close()
                        gc.collect()
                        _trim_process_memory()
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
                                if dora_constraint not in ("dora_unrelated", "dora_matches_position") and not _dora_matches_constraint(dora_str, dora_constraint):
                                    continue
                            if consumed_search_list:
                                if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                                    continue
                            if call_constraint or call_area_constraints or call_area_constraint_sets:
                                if not round_could_satisfy_call_constraints(
                                    round_players, call_constraint,
                                    None if call_area_constraint_sets else call_area_constraints,
                                    oya, call_area_constraint_sets=call_area_constraint_sets
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
                                if call_area_constraint_sets:
                                    if not player_could_satisfy_any_call_area_constraints(player_state, oya, call_area_constraint_sets):
                                        continue
                                elif call_area_constraints and not player_could_satisfy_call_area_constraints(
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
                                    "visible_tiles": player_state.visible_tiles,
                                    "dora_indicators": getattr(round_players[0], "dora_indicators", None) or getattr(player_state, "dora_indicators", []),
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
                                        if prior_discard_exclusion:
                                            excl_str = matched_variant.get("prior_discard_exclusion")
                                            if excl_str:
                                                forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                                start_idx = matched_variant.get("matched_start_index")
                                                if start_idx is not None:
                                                    prior_tiles = full_discards_up_to_now[:start_idx]
                                                    if any(MjlogParser.string_to_tile(t[0]) // 4 in forbidden for t in prior_tiles):
                                                        continue
                                                elif turn_range:
                                                    first_turn_in_range = in_range_for_cell[0][1].turn
                                                    prior_discards = [d for d in player_state.discards if d.turn < first_turn_in_range]
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
                                        if dora_constraint == "dora_matches_position" and dora_position_spec and dora_str:
                                            pos_to_tile = matched_variant.get("position_to_tile") or {}
                                            skip_match = False
                                            for pos in dora_position_spec:
                                                tile_at_pos = pos_to_tile.get(pos)
                                                if tile_at_pos is None or not _tile_str_eq(tile_at_pos, dora_str):
                                                    skip_match = True
                                                    break
                                            if skip_match:
                                                continue
                                        if riichi_constraint and riichi_constraint != "any":
                                            if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                                                continue
                                            if riichi_constraint == "no_riichi" and _opponent_riichi_happened(discard):
                                                continue
                                        if call_constraint and call_constraint != "any":
                                            if call_constraint == "has_call" and not discard.call_happened:
                                                continue
                                            if call_constraint == "no_call" and discard.call_happened:
                                                continue
                                        _ca = matched_variant.get("call_area_constraints") or call_area_constraints
                                        if _ca:
                                            if not player_satisfies_call_area_constraints(
                                                player_state, round_players, player_state.oya, _ca,
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
            del logs
            maintenance_batch_index += 1
            if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches, use_deal_in_instant):
                gc.collect()
                try:
                    conn.execute("PRAGMA shrink_memory")
                except:
                    pass
                conn.close()
                conn = _connect_db_memory_efficient(self.db_path)
                cur = conn.cursor()
                _ensure_log_json_column(conn)
                _trim_process_memory()
            if sample_limit and processed >= sample_limit:
                break

        conn.close()
        gc.collect()
        _trim_process_memory()

        # 按格计算合并概率与完整分布
        cell_combo = {(tr_idx, pat_idx): is_combo for tr_idx, pat_idx, _, _, _, is_combo in grid_meta}
        table = {}
        table_dist = {}  # 姣忔牸完整分布 {(tr_idx, pat_idx): {0: pct, 1: pct, ...}}
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

        logger.info(f"批量分析瀹屾垚: 处理 {processed:,} 场对局")
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
        target_count_filter: Optional[int] = None,  # None=全部, 0/1/2/3=可收该数
        target_tile_filter: Optional[str] = None,  # 多目标时指定按哪一目标筛选，如"6s"
        dora_constraint: Optional[str] = None,
        dora_position_spec: Optional[List[int]] = None,  # 宝牌=模式第N张时使用
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
        exclude_south4: bool = False,
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[str] = None,
        call_area_constraints: Optional[List[str]] = None,
        gc_interval_batches: Optional[int] = None,
        outcome_filter: Optional[str] = None,  # 前段不可打，与舍牌模式同步等价变换
        deal_in_filter: Optional[str] = None,  # "hit"|"miss"|"furiten" 即时铳率样本筛选
        analysis_target: Optional[str] = None,
    ) -> List[Dict]:
        """
        收集验证样本，用于人工复盘核验。
        返回含 log_id、oya、局显示等完整信息的样本列表。
        若传入 sample_pool（主统计时预收集），则直接从池采样，无需二次分析。
        """
        requested_batch_size = (
            analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        )
        use_deal_in_instant = (analysis_target == "deal_in_instant")
        batch_size = _clamp_analysis_batch_size(requested_batch_size, workers=1, use_parallel=False)

        if sample_pool is not None:
            # 从预收集的样本池中筛选并取前 N 个，无需遍历牌谱（主分析已排除南四局则无需再过滤）
            candidates = sample_pool
            if outcome_filter is not None:
                if outcome_filter == "win":
                    candidates = [s for s in candidates if s.get("outcome_won")]
                elif outcome_filter == "deal_in":
                    candidates = [s for s in candidates if s.get("outcome_deal_in")]
                elif outcome_filter == "neither":
                    candidates = [s for s in candidates if not s.get("outcome_won") and not s.get("outcome_deal_in")]
            if deal_in_filter is not None:
                if target_tile_filter and any("instant_eval_multi" in s for s in candidates):
                    # 如果指定了目标牌且有详细评估结果，按该目标的评估结果过滤
                    if deal_in_filter == "hit":
                        candidates = [s for s in candidates if s.get("instant_eval_multi", {}).get(target_tile_filter, {}).get("deal_in_hit")]
                    elif deal_in_filter == "miss":
                        candidates = [s for s in candidates if not s.get("instant_eval_multi", {}).get(target_tile_filter, {}).get("deal_in_hit")]
                    elif deal_in_filter == "furiten":
                        candidates = [
                            s for s in candidates
                            if (not s.get("instant_eval_multi", {}).get(target_tile_filter, {}).get("deal_in_hit")) 
                            and str(s.get("instant_eval_multi", {}).get(target_tile_filter, {}).get("furiten_state", "none")) != "none"
                        ]
                else:
                    if deal_in_filter == "hit":
                        candidates = [s for s in candidates if s.get("deal_in_hit")]
                    elif deal_in_filter == "miss":
                        candidates = [s for s in candidates if not s.get("deal_in_hit")]
                    elif deal_in_filter == "furiten":
                        candidates = [
                            s for s in candidates
                            if (not s.get("deal_in_hit")) and str(s.get("furiten_state", "none")) != "none"
                        ]
            if target_count_filter is not None:
                if target_tile_filter and any("target_counts" in s for s in candidates):
                    candidates = [s for s in candidates if s.get("target_counts", {}).get(target_tile_filter) == target_count_filter]
                elif any("target_counts" in s for s in candidates):
                    # 多模式但未指定具体 target_tile_filter 时，按主模式统计
                    candidates = [s for s in candidates if s.get("target_count") == target_count_filter]
                else:
                    candidates = [s for s in candidates if s.get("target_count") == target_count_filter]
            
            # 核心修正：既然在 sample_pool 中，说明主分析时已匹配成功。
            # 无需再进行由于字典信息不全可能导致失败的 verify_sample_consistency 校验。
            return candidates[:sample_count]

        variants = generate_equivalent_variants(
            query_pattern, target_tile, visible_constraints, prior_discard_exclusion, call_area_constraints
        )
        consumed_search = get_consumed_search_patterns(query_pattern)
        riichi_search = pattern_has_riichi(query_pattern)
        samples = []

        if should_cancel and should_cancel():
            return []

        conn = _connect_db_memory_efficient(self.db_path)
        cur = conn.cursor()
        _ensure_log_json_column(conn)

        # 避免耗时的COUNT(*)，使用 total_logs_hint 或 sample_limit
        if total_logs_hint is not None and total_logs_hint > 0:
            total_logs = total_logs_hint
        else:
            total_logs = 0
        if sample_limit:
            total_logs = min(total_logs, sample_limit) if total_logs > 0 else sample_limit

        last_id = None  # 游标分页），避免OFFSET 越大越慢
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
                    gc.collect()
                    _trim_process_memory()
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
                    round_payloads = extract_tenhou6_rounds(_raw_to_tenhou6_for_instant(raw)) if use_deal_in_instant else []
                    round_size = 4

                    for round_start in range(0, len(game_states), round_size):
                        round_players = game_states[round_start:round_start + round_size]
                        if len(round_players) < round_size:
                            break
                        round_idx = round_start // round_size
                        round_payload = round_payloads[round_idx] if round_idx < len(round_payloads) else None
                        round_instant_analyzer = None
                        
                        rn = getattr(round_players[0], "round_num", 0)
                        if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
                            continue
                        dora_str = None
                        if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                            dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                            if dora_constraint not in ("dora_unrelated", "dora_matches_position") and not _dora_matches_constraint(dora_str, dora_constraint):
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

                                # 预转换：全量舍牌（用于 matched_variant 判断）
                                all_discards_precomputed = [
                                    (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                                    for d in player_state.discards
                                ]
                                all_riichi_flags = [
                                    getattr(d, 'is_riichi_declaration', False)
                                    for d in player_state.discards
                                ]
                                honor_ctx_base = {
                                    "jikaze": MjlogParser.get_jikaze(player_state.player_id, player_state.oya, player_state.round_num),
                                    "bakaze": ["东", "南", "西", "北"][player_state.round_num // 4],
                                    "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                                    "calls": getattr(player_state, "calls", []),
                                    "visible_tiles": player_state.visible_tiles,
                                    "dora_indicators": getattr(round_players[0], "dora_indicators", None) or getattr(player_state, "dora_indicators", []),
                                }

                                discarded_bases = set()
                                for j, (orig_i, discard) in enumerate(in_range):
                                    discarded_bases.add(discard.tile // 4)
                                    full_discards_up_to_now = all_discards_precomputed[:orig_i + 1]
                                    current_riichi_flags = all_riichi_flags[:orig_i + 1]
                                    hand_discard_strings = _format_actual_pattern(full_discards_up_to_now, current_riichi_flags)

                                    honor_ctx = {
                                        **honor_ctx_base,
                                        "current_discard_turn": discard.turn,
                                        "discard_riichi_flags": current_riichi_flags,
                                    }
                                    matched_variant = match_discard_to_variant(full_discards_up_to_now, variants, honor_ctx)
                                    if not matched_variant:
                                        continue

                                    # 排除：若未打出牌就是目标牌，不计入
                                    mapped_target = matched_variant["target"]
                                    mapped_target_str = mapped_target if isinstance(mapped_target, str) else (mapped_target[0] if mapped_target else None)
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

                                    # 结局过滤
                                    rw = getattr(player_state, "round_winners", [])
                                    rdi = getattr(player_state, "round_deal_in", None)
                                    if outcome_filter:
                                        if outcome_filter == "win" and player_state.player_id not in rw:
                                            continue
                                        if outcome_filter == "deal_in" and rdi != player_state.player_id:
                                            continue
                                        if outcome_filter == "neither" and (player_state.player_id in rw or rdi == player_state.player_id):
                                            continue

                                    # 即时铳率分析
                                    instant_eval = {}
                                    instant_eval_multi = {}
                                    if use_deal_in_instant:
                                        if round_instant_analyzer is None and round_payload:
                                            try:
                                                rp_data, rp_events = round_payload
                                                norm_oya = False  # 离线样本扫描未传该选项，使用默认
                                                round_instant_analyzer = RoundInstantDealInAnalyzer(
                                                    rp_data,
                                                    rp_events,
                                                    player_state.round_num,
                                                    player_state.oya,
                                                    normalize_oya_ron_to_ko=norm_oya,
                                                )
                                            except Exception:
                                                logger.exception("即时铳率引擎初始化失败（样本扫描本局跳过）")

                                        if round_instant_analyzer:
                                            if isinstance(mapped_target, list):
                                                # 多目标
                                                orig_targets, _ = parse_target_tiles(target_tile)
                                                for i, m_t in enumerate(mapped_target):
                                                    ev = round_instant_analyzer.evaluate(player_state.player_id, discard.turn, m_t)
                                                    tk = orig_targets[i]
                                                    instant_eval_multi[tk] = ev

                                                # 如果有 filter，取 filter 对应的结果；否则取第一个
                                                if target_tile_filter and target_tile_filter in instant_eval_multi:
                                                    instant_eval = instant_eval_multi[target_tile_filter]
                                                else:
                                                    instant_eval = list(instant_eval_multi.values())[0]
                                            elif mapped_target_str:
                                                instant_eval = round_instant_analyzer.evaluate(player_state.player_id, discard.turn, mapped_target_str)

                                    if deal_in_filter:
                                        if deal_in_filter == "hit" and not instant_eval.get("deal_in_hit"):
                                            continue
                                        if deal_in_filter == "miss" and instant_eval.get("deal_in_hit"):
                                            continue
                                        if deal_in_filter == "furiten" and (instant_eval.get("deal_in_hit") or str(instant_eval.get("furiten_state", "none")) == "none"):
                                            continue

                                    if prior_discard_exclusion:
                                        excl_str = matched_variant.get("prior_discard_exclusion")
                                        if excl_str:
                                            forbidden = get_forbidden_bases_from_exclusion_str(excl_str)
                                            start_idx = matched_variant.get("matched_start_index")
                                            if start_idx is not None:
                                                prior_tiles = full_discards_up_to_now[:start_idx]
                                                if any(MjlogParser.string_to_tile(t[0]) // 4 in forbidden for t in prior_tiles):
                                                    continue
                                            elif turn_range:
                                                prior_discards = [d for d in player_state.discards if d.turn < in_range[0][1].turn]
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
                                    if dora_constraint == "dora_matches_position" and dora_position_spec and dora_str:
                                        pos_to_tile = matched_variant.get("position_to_tile") or {}
                                        skip_match = False
                                        for pos in dora_position_spec:
                                            tile_at_pos = pos_to_tile.get(pos)
                                            if tile_at_pos is None or not _tile_str_eq(tile_at_pos, dora_str):
                                                skip_match = True
                                                break
                                        if skip_match:
                                            continue
                                    if riichi_constraint and riichi_constraint != "any":
                                        if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                                            continue
                                        if riichi_constraint == "no_riichi" and _opponent_riichi_happened(discard):
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

                                    # 排除：若未打出牌就是目标牌，不计入（与主统计逻辑一致）
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
                                    hand_at_turn = list(hh)  # 必须为 list 以保留同种牌枚数
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
                                    call_area = _format_call_area_display(
                                        getattr(player_state, "calls", []) or [], discard.turn
                                    )
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
                                        "dora_indicators": list(player_state.dora_indicators),
                                        "dora_readable": dora_readable,
                                        "visible_target": visible_target,
                                        "call_area": call_area,
                                        "target_count": target_count,
                                        "is_combo": sample_is_combo,
                                        "outcome_won": bool(rw and player_state.player_id in rw),
                                        "outcome_deal_in": bool(rdi is not None and rdi == player_state.player_id),
                                    }
                                    if use_deal_in_instant:
                                        sp_entry.update({
                                            "deal_in_hit": bool(instant_eval.get("deal_in_hit")),
                                            "deal_in_point": int(instant_eval.get("deal_in_point", 0)),
                                            "furiten_state": instant_eval.get("furiten_state", "none"),
                                            "furiten_reason": instant_eval.get("furiten_reason", ""),
                                            "waits_snapshot": instant_eval.get("waits_snapshot", []),
                                        })
                                        if instant_eval_multi:
                                            sp_entry["instant_eval_multi"] = instant_eval_multi
                                    # 实时扫描匹配到的样本，无需再调用 verify_sample_consistency 校验，直接添加
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
            last_id = logs[-1][0]
            del logs
            maintenance_batch_index += 1
            if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches, use_deal_in_instant):
                gc.collect()
                try:
                    conn.execute("PRAGMA shrink_memory")
                except:
                    pass
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
        gc.collect()
        _trim_process_memory()
        return samples


def _visible_count(visible_tiles: dict, tile_str: str) -> int:
    """统计某牌在可见牌中的枚数）m/0p/0s 串5m/5p/5s 视为不同牌）"""
    base = MjlogParser.string_to_tile(tile_str)
    equiv = MjlogParser.get_count_equivalent_bases(base)
    return sum(c for t, c in visible_tiles.items() if t // 4 in equiv)


def _format_call_area_display(calls: list, current_turn: int) -> str:
    """将 CallInfo 列表格式化为副露区展示字符串（仅包含 current_turn 前已发生的副露）。"""
    if not calls:
        return "(无)"
    parts = []
    for c in calls:
        from_turn = getattr(c, "from_discard_turn", 1)
        if from_turn > current_turn:
            continue
        ct = getattr(c, "call_type", "")
        pai = getattr(c, "pai", "")
        consumed = getattr(c, "consumed", []) or []
        if ct == "chii" and pai and len(consumed) >= 2:
            parts.append(f"{pai}c{consumed[0]}{consumed[1]}")
        elif ct == "pon" and len(consumed) >= 2:
            parts.append(f"p{consumed[0]}{consumed[1]}")
        elif ct in ("kan", "daiminkan", "kakan", "ankan") and consumed:
            tiles = [pai] + list(consumed) if pai else list(consumed)
            parts.append("k" + "".join(tiles[:4]))
    return " ".join(parts) if parts else "(无)"


def _fmt_target(mt) -> str:
    """格式化目标牌（单张或搭子）"""
    return "-".join(mt) if isinstance(mt, list) else str(mt)


def _target_desc(s: dict, use_tenpai: bool = False) -> str:
    """目标牌描述：听牌模式=听牌/未听牌；搭子=未时无搭子；单张=应有X张在手牌锛涘目标=各目标枚数"""
    if use_tenpai:
        return "tenpai" if s["target_count"] else "noten"
    tc = s.get("target_counts")
    if tc is not None:
        return " ".join(f"{k}:{v}" for k, v in sorted(tc.items()))
    if s.get("is_combo"):
        return "has_combo" if s["target_count"] else "no_combo"
    return "应有{}张在手牌".format(s["target_count"])


def _target_display_set(mt) -> set:
    """目标牌的显示集合）m/0p/0s 与 5m/5p/5s 视为不同牌）"""
    return set(mt) if isinstance(mt, list) else {mt}


def _actual_pattern_to_full_discards(actual_pattern: List[str]) -> Tuple[List[Tuple[str, bool]], List[bool]]:
    """
    将actual_pattern（舍牌序列的展示格式）转为full_discards 格式以供匹配使用。
    "1m" -> (1m, False), riichi=False
    "1mt" -> (1m, True), riichi=False
    "1mr" -> (1m, False), riichi=True (手切立直)
    "1mtr" -> (1m, True), riichi=True (摸切立直)
    """
    result = []
    riichi_flags = []
    for s in actual_pattern:
        if not s or len(s) < 2:
            continue
        if s.endswith("tr") or s.endswith("rt"):
            result.append((s[:-2], True))
            riichi_flags.append(True)
        elif s.endswith("r"):
            result.append((s[:-1], False)) # r 代表手切立直
            riichi_flags.append(True)
        elif s.endswith("t"):
            result.append((s[:-1], True))
            riichi_flags.append(False)
        else:
            result.append((s, False))
            riichi_flags.append(False)
    return result, riichi_flags


def verify_sample_consistency(
    sample: Dict,
    query_pattern: List[str],
    target_tile: str,
    visible_constraints: Optional[Dict] = None,
    prior_discard_exclusion: Optional[str] = None,
    call_area_constraints: Optional[List[str]] = None,
) -> Tuple[bool, Optional[str]]:
    """
    验证样本的 actual_pattern 是否与查询模式在匹配逻辑下一致。
    用于检验样本生成与分析匹配是否同源。

    Returns:
        (一致性状态, 错误信息)
    """
    actual = sample.get("actual_pattern")
    if not actual:
        return (False, "样本不包含 actual_pattern")
    try:
        full_discards, riichi_flags = _actual_pattern_to_full_discards(actual)
        if not full_discards:
            return (False, "actual_pattern parsed to empty sequence")
        first_t = parse_multi_targets(target_tile)[0]
        variant_target = "".join(first_t[0]) if first_t[1] else first_t[0][0]
        variants = generate_equivalent_variants(
            query_pattern, variant_target, visible_constraints,
            prior_discard_exclusion, call_area_constraints
        )
        
        # 补充上下文，支持 r (立直) 及 zf/kf (自风/客风) 占位符校验
        player_id = sample.get("player_id", 0)
        oya = sample.get("oya", 0)
        round_num = sample.get("round_num", 0)
        
        ctx = {
            "jikaze": MjlogParser.get_jikaze(player_id, oya, round_num),
            "bakaze": ["东", "南", "西", "北"][round_num // 4],
            "kyokuze_list": MjlogParser.get_kyokuze_list(player_id, oya, round_num),
            "discard_riichi_flags": riichi_flags,
            "current_discard_turn": sample.get("turn"),
            "visible_tiles": sample.get("visible_tiles"),
            "dora_indicators": sample.get("dora_indicators"),
        }
        
        matched = match_discard_to_variant(full_discards, variants, ctx)
        if matched:
            return (True, None)
        return (False, "重新匹配失败：actual_pattern 无法匹配任一等价变体")
    except Exception as e:
        logger.exception("样本一致性校验异常")
        return (False, str(e))


def _target_counts_display_set(tc: dict) -> set:
    """多目标时，各目标牌及其等价牌的显示集合"""
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
    use_instant = (analysis_target == "deal_in_instant")
    is_combo = False if use_tenpai else (samples[0].get("is_combo", False) if samples else False)
    has_multi = bool(samples and samples[0].get("target_counts"))
    target_label = "即时铳率" if use_instant else ("和铳率" if use_outcome else (target_tile if use_tenpai else (
        f"{target_tile} (combo)" if is_combo else (f"{target_tile} (multi-target)" if has_multi else target_tile)
    )))
    lines = [
        "=" * 80,
        f"Verification Samples: {query_pattern_str} -> {target_label}",
        "=" * 80,
        "",
    ]
    for i, s in enumerate(samples, 1):
        mt = s.get("mapped_target")
        if use_outcome or use_instant:
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
        elif use_instant:
            hit = bool(s.get("deal_in_hit"))
            point = int(s.get("deal_in_point", 0))
            target_line = f"  即时可铳:    {'是' if hit else '否'}"
            if hit:
                target_line += f" (理论点 {point})"
        elif use_tenpai:
            target_line = f"  Tenpai:      {_target_desc(s, use_tenpai)}"
        elif has_multi and s.get("target_counts"):
            target_line = f"  Target cnts: {_target_desc(s, use_tenpai)}"
        else:
            target_line = f"  Target:      {_fmt_target(mt)} ({_target_desc(s, use_tenpai)})"
        call_area_str = s.get("call_area", "(无)")
        block = [
            f"[Sample {i}]",
            f"  Log ID:      {s['log_id']}",
            f"  Tenhou URL:  https://tenhou.net/4/?log={s['log_id']}&tw={s['player_id']}",
            f"  Round:       {round_display}",
            f"  Seat:        {wind}",
            f"  Turn:        {s['turn']}",
            f"  Dora:        {s['dora_readable']}",
            f"  Discards:    {' '.join(s['actual_pattern'])}",
            f"  副露区:      {call_area_str}",
            target_line,
            f"  Hand({len(s['hand_tiles'])}): {hand_str}",
        ]
        if use_instant:
            block.append(f"  振听状态:    {s.get('furiten_state', 'none')}")
            block.append(f"  振听原因:    {s.get('furiten_reason', '') or '(无)'}")
            waits = s.get("waits_snapshot") or []
            block.append(f"  当时待牌:    {' '.join(waits) if waits else '(无)'}")
            mt = s.get("mapped_target")
            if mt is not None:
                mt_str = ", ".join(mt) if isinstance(mt, list) else mt
                block.append(f"  假想目标牌:  {mt_str}")
        if not use_tenpai and not use_outcome:
            if has_multi and s.get("target_counts"):
                block.append(f"  Visible targets: {s['visible_target']}")
            elif not use_instant:
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
    获取数据库统计信息。
    使用内存受限连接，用后立即关闭并触发 gc/trim，避免 tenhou.db 长期占用数 GB 缓存。
    """
    conn = _connect_db_memory_efficient(db_path)
    cur = conn.cursor()
    stats = {}
    try:
        cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
        stats['total_logs'] = cur.fetchone()[0]
        if os.path.exists(db_path):
            stats['db_size_mb'] = os.path.getsize(db_path) / (1024 * 1024)
    finally:
        conn.close()
    gc.collect()
    _trim_process_memory()
    return stats


