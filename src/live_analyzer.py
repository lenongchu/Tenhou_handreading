"""
实时查询引擎 - 按需分析模式
直接从 logs 表读取完整牌谱数据并实时解析分析

解析采用 tenhou6 格式（tenhou-paifu-to-json），支持副露等完整信息。
"""
import gc
import os
import sys
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
from .related_tile_utils import (
    is_related_discard,
    hand_to_suit_counts,
    base_to_discard_num_and_suit,
)
from .equivalent_variants import (
    generate_equivalent_variants,
    get_prior_discard_exclusion_forbidden_bases,
    normalize_prior_discard_exclusion_list,
    match_discard_to_variant,
    match_discard_pattern_contained,
    prior_required_pattern_to_variants,
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
from .yaku_hai_hand_stats import (
    compute_yaku_hai_hand_stats,
    yaku_hai_per_tile_counts,
    yaku_pair_units_bucket,
)

logger = logging.getLogger(__name__)

# 生成验证样本时：役牌模式下单选「三对及以上」传给 collect_verification_samples 的哨兵（与 0/1/2 的精确 pair_kinds 区分）
YAKU_HAI_PAIR_FILTER_GE3 = -1


def _apply_combo_independence_filter(
    hand_at_turn: List[int],
    mapped_target: Union[str, List[str], None],
    base_count: int,
    enabled: bool,
) -> int:
    """
    搭子 combo：在已判定「目标张数齐套」(base_count==1) 后套独立性筛选（independence filter）。
    非 combo、未齐套、或非两枚数牌目标时直接返回 base_count。
    """
    if not enabled or base_count != 1 or mapped_target is None:
        return base_count
    tiles = mapped_target if isinstance(mapped_target, list) else [mapped_target]
    if len(tiles) != 2:
        return base_count
    from .taatsu_independence import combo_passes_independence_filter

    return 1 if combo_passes_independence_filter(hand_at_turn, tiles) else 0


# 每批从数据库读取的对局数（统一默认）：主分析、矩阵、即时铳率(deal_in_instant) 等均用 analyze 入参 analysis_batch_size，未传时即用本值；GUI 单控件覆盖。
# 过小则 SQL 批次数暴增、批边界与主线程合并更频繁，CPU 易呈「锯齿状」低谷；默认取较大批以换流水线重叠（内存紧张时再改小或用入参覆盖）
ANALYSIS_BATCH_SIZE = 5000
MIN_ANALYSIS_BATCH_SIZE = 500
# 主统计时最多保留的匹配状态条数（仅用于返回给界面，超出部分不保留，避免内存持续增长）
MATCHED_STATES_CAP = 200
SAMPLE_POOL_CAP = 3000
PARALLEL_MIN_MATCHED_STATES_PER_LOG = 4
PARALLEL_MAX_SAMPLE_POOL_PER_LOG = 512  # 提高以保留更多可铳样本，避免单局多匹配时样本池截断
PARALLEL_BATCH_PER_WORKER = 500
# Python 3.11+ 可选 max_tasks_per_child：过小会导致子进程在单批内就轮换，引发明显批间/批内停顿；None 表示不限制（默认）
PARALLEL_MAX_TASKS_PER_CHILD: Optional[int] = None
PARALLEL_IN_FLIGHT_FACTOR = 4
GC_INTERVAL_BATCHES = 10  # 降低频率，因为现在有管理员强制清理
# 内存负载阈值 [0,1]，与 GlobalMemoryStatusEx 的 dwMemoryLoad 一致；误写成 10 会导致「高压早维护」分支永不到
HIGH_MEMORY_LOAD_RATIO = 0.94
# ---------------------------------------------------------------------------
# 长分析中的内存维护（默认关闭最耗时的几项；系统在 ~94% 饱和时收益常不明显）
# 需要恢复旧行为时把对应项改为 True 即可（不必翻注释块）。
# ---------------------------------------------------------------------------
# 每 N 批：gc（若开）+ PRAGMA shrink_memory + 关闭并重连主线程上的 DB 连接。
# True 时极易造成明显批间低谷，且与 _trim 叠加时常表现为「停顿越来越久」（WAL/页缓存 + 系统文件缓存被反复刷冷）。
ENABLE_PERIODIC_MEMORY_MAINTENANCE = False
# Windows：EmptyWorkingSet + SetSystemFileCacheSize（可能拖慢后续 I/O）
ENABLE_TRIM_PROCESS_MEMORY = True
# 是否与「周期 DB 维护」一同调用 Trim：极易拉长低谷；分析结束时的 finally 仍会按需 Trim（见 ENABLE_TRIM_PROCESS_MEMORY）
ENABLE_TRIM_WITH_PERIODIC_MAINTENANCE = False
# 取消/结束等路径上的 gc.collect()（全量 GC 在百万对象下很慢）
ENABLE_GC_COLLECT_DURING_ANALYSIS = False


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


def _make_analysis_process_pool(max_workers: int) -> ProcessPoolExecutor:
    """创建分析用进程池。仅当 PARALLEL_MAX_TASKS_PER_CHILD 为正数且 Python>=3.11 时才传入 max_tasks_per_child。"""
    mtpc = PARALLEL_MAX_TASKS_PER_CHILD
    if sys.version_info >= (3, 11) and mtpc is not None and mtpc > 0:
        return ProcessPoolExecutor(
            max_workers=max_workers,
            max_tasks_per_child=int(mtpc),
        )
    return ProcessPoolExecutor(max_workers=max_workers)


def _ipc_copy_instant_eval(ev: Optional[Dict]) -> Dict:
    """Worker→主进程 IPC：即时铳率 evaluate 仅保留统计/展示键，降低 pickle 与合并开销。"""
    if not ev:
        return {
            "deal_in_hit": False,
            "deal_in_point": 0,
            "furiten_state": "none",
            "furiten_reason": "",
            "waits_snapshot": [],
        }
    return {
        "deal_in_hit": bool(ev.get("deal_in_hit")),
        "deal_in_point": int(ev.get("deal_in_point", 0)),
        "furiten_state": ev.get("furiten_state", "none"),
        "furiten_reason": ev.get("furiten_reason", ""),
        "waits_snapshot": list(ev.get("waits_snapshot") or []),
    }


def _ipc_copy_instant_eval_multi(multi: Optional[Dict]) -> Dict:
    """多目标即时铳率：逐目标裁剪后再进入跨进程序列化。"""
    if not multi:
        return {}
    return {k: _ipc_copy_instant_eval(v) for k, v in multi.items()}


class BackgroundLogFetcher:
    """后台对局预取器，用于在计算时并行读取数据库 I/O"""
    def __init__(self, db_path, batch_size, last_id=None):
        self.db_path = db_path
        self.batch_size = batch_size
        self.last_id = last_id
        # 有界队列存流式元素：('chunk', rows) 与 ('batch_done',)；容量按「每批 chunk 数 × 缓冲批数」估算，避免 fetchmany 时阻塞死锁
        _chunks_est = max(
            1,
            (int(batch_size) + BACKGROUND_FETCHER_FETCHMANY_ROWS - 1) // BACKGROUND_FETCHER_FETCHMANY_ROWS,
        ) + 2
        _q_cap = max(64, _chunks_est * BACKGROUND_FETCHER_QUEUE_MAX_BATCHES)
        self.queue = queue.Queue(maxsize=_q_cap)
        self.stop_event = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            # 后台线程开启独立连接
            conn = _connect_db_memory_efficient(self.db_path, log_sequential_reader=True)
            cur = conn.cursor()
            last_id = self.last_id
            fm = max(1, int(BACKGROUND_FETCHER_FETCHMANY_ROWS))

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

                # fetchmany 分块入队：不必等整批 fetchall 完成即可被 RowTaskPrefetcher 消费，缩短 worker 批末空转
                batch_last_id = None
                any_chunk = False
                while not self.stop_event.is_set():
                    chunk = cur.fetchmany(fm)
                    if not chunk:
                        break
                    any_chunk = True
                    batch_last_id = chunk[-1][0]
                    self.queue.put(("chunk", chunk))
                if not any_chunk:
                    self.queue.put(None)  # 无更多对局
                    break
                last_id = batch_last_id
                self.queue.put(("batch_done",))
            conn.close()
        except Exception as e:
            logger.error(f"后台预取线程出错: {e}")
            self.error = e
            self.queue.put(None)

    def pull_stream_item(self):
        """流式取队列：('chunk', rows) | ('batch_done',) | None（None=扫描结束）。供 RowTaskPrefetcher 使用。"""
        if self.error:
            raise self.error
        return self.queue.get()

    def next_batch(self):
        """串行路径：聚合 chunk 直至 batch_done，对外仍为「一整批 rows」。"""
        if self.error:
            raise self.error
        acc = []
        while True:
            item = self.pull_stream_item()
            if item is None:
                return None if not acc else acc
            if item[0] == "chunk":
                acc.extend(item[1])
            elif item[0] == "batch_done":
                return acc
            else:
                logger.warning("BackgroundLogFetcher: 未知队列项 %r", item)
                continue

    def stop(self):
        self.stop_event.set()
        try:
            while not self.queue.empty():
                self.queue.get_nowait()
        except:
            pass


# 行任务预取队列内哨兵：标记「一个 SQL 批已拆完」，主线程取出时调用 on_batch_done（可能关连主库 conn，必须在主线程）
_PREFETCH_BATCH_GAP = object()
_PREFETCH_STREAM_END = object()


class _RowTaskPrefetcher:
    """
    后台线程从 BackgroundLogFetcher 取流式 chunk，拆成 (log_id, content, params) 写入有界队列。
    与 fetchmany 分块配合，避免等整批 fetchall 结束后才向 worker 队列投喂，减轻批末 CPU 断崖。
    """

    def __init__(
        self,
        fetcher: BackgroundLogFetcher,
        per_log_params: Dict,
        on_batch_done: Optional[Callable[[], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        queue_maxsize: int = 256,
    ):
        self._fetcher = fetcher
        self._per_log_params = per_log_params
        self._on_batch_done = on_batch_done
        self._should_cancel = should_cancel
        self._q: "queue.Queue" = queue.Queue(maxsize=max(32, int(queue_maxsize)))
        self._stop = threading.Event()
        self._error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="RowTaskPrefetch")
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self._should_cancel and self._should_cancel():
                    break
                item = self._fetcher.pull_stream_item()
                if item is None:
                    break
                if item[0] == "chunk":
                    _, rows = item
                    for row in rows:
                        if self._stop.is_set():
                            return
                        if self._should_cancel and self._should_cancel():
                            return
                        log_id, log_content = row[0], row[1]
                        task = (log_id, log_content, self._per_log_params)
                        while not self._stop.is_set():
                            try:
                                self._q.put(task, timeout=0.25)
                                break
                            except queue.Full:
                                continue
                elif item[0] == "batch_done":
                    if self._stop.is_set():
                        return
                    while not self._stop.is_set():
                        try:
                            self._q.put(_PREFETCH_BATCH_GAP, timeout=0.25)
                            break
                        except queue.Full:
                            continue
                else:
                    logger.warning("_RowTaskPrefetcher: 未知 fetch 项 %r", item)
        except Exception as e:
            logger.error(f"行级任务预取线程出错: {e}")
            self._error = e
        finally:
            while True:
                try:
                    self._q.put(_PREFETCH_STREAM_END, timeout=2.0)
                    break
                except queue.Full:
                    if self._stop.is_set():
                        try:
                            self._q.put_nowait(_PREFETCH_STREAM_END)
                        except Exception:
                            pass
                        break
                    continue

    def close(self) -> None:
        """停止预取并尽量排空队列，减轻 cancel 时生产者阻塞在 put 的概率。"""
        self._stop.set()
        try:
            while True:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
        except Exception:
            pass
        self._thread.join(timeout=3.0)

    def __iter__(self) -> "_RowTaskPrefetcher":
        return self

    def __next__(self) -> Tuple:
        if self._error is not None:
            raise self._error
        if self._should_cancel and self._should_cancel():
            raise StopIteration
        while True:
            item = self._q.get()
            if item is _PREFETCH_STREAM_END:
                raise StopIteration
            if item is _PREFETCH_BATCH_GAP:
                if self._on_batch_done:
                    self._on_batch_done()
                continue
            return item


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


def _hands_counters_at_discard_moment(discard, player_state, round_players: List) -> Optional[List[Counter]]:
    """
    四名玩家在当前打出瞬间的手牌按 base 计数；若 discard 来自玩家 P，则 P 的手牌含本张打出牌（与延伸手牌判定一致）。
    all_players_turns 不可用则返回 None。
    """
    all_turns = getattr(discard, "all_players_turns", None)
    if all_turns is None or len(all_turns) != 4:
        return None
    hands_counts: List[Counter] = []
    for pid in range(4):
        t = all_turns[pid]
        p_state = round_players[pid]
        if t == 0:
            hand = list(p_state.initial_hand)
        elif t <= len(p_state.hand_tiles_history):
            hand = list(p_state.hand_tiles_history[t - 1])
        else:
            hand = list(p_state.hand_tiles)
        if pid == player_state.player_id:
            hand.append(discard.tile)
        hands_counts.append(Counter(tile // 4 for tile in hand))
    return hands_counts


class MatchValidator:
    """
    舍牌匹配后的统一约束验证器。
    将原先散落在 for 循环内的「if not ...: continue」校验逻辑集中管理，
    供第三步 _core_match_engine 统一调用。不改变任何原始判定条件和麻将规则。
    """

    def __init__(self, params: Dict):
        self.params = params
        # 多条前段禁打（list of lines）时与单条一致：非空即启用校验
        self.prior_discard_exclusion = normalize_prior_discard_exclusion_list(
            params.get("prior_discard_exclusion")
        )
        self.dora_constraint = params.get("dora_constraint")
        self.dora_position_spec = params.get("dora_position_spec") or []
        self.riichi_constraint = params.get("riichi_constraint")
        self.call_constraint = params.get("call_constraint")
        self.target_no_call = params.get("target_no_call", False)
        self.call_area_constraints = params.get("call_area_constraints")
        self.hand_visible_constraints = params.get("hand_visible_constraints")
        self.use_tenpai = params.get("use_tenpai", False)
        self.use_related_tile = params.get("use_related_tile", False)
        self.use_deal_in_instant = params.get("use_deal_in_instant", False)

    def validate_all(
        self,
        discard,
        matched_variant: Dict,
        match_ctx: Dict,
    ) -> bool:
        """
        主控方法：依次执行所有舍牌匹配后的校验，任一不通过则返回 False。

        Args:
            discard: 当前舍牌对象
            matched_variant: match_discard_to_variant 返回的匹配变体
            match_ctx: 上下文，需含 full_discards_up_to_now, honor_ctx, player_state, round_players；
                可选 dora_str, turn_range, first_turn_in_range, prior_discards_for_exclusion,
                multi_t, item_combo（用于目标牌排除）

        Returns:
            True 表示全部校验通过，False 表示应跳过该匹配
        """
        full_discards = match_ctx.get("full_discards_up_to_now") or []
        honor_ctx = match_ctx.get("honor_ctx") or {}
        player_state = match_ctx.get("player_state")
        round_players = match_ctx.get("round_players") or []

        if not self._check_prior_discard_exclusion(matched_variant, full_discards, match_ctx, player_state):
            return False
        if not self._check_prior_discard_required(matched_variant, full_discards, honor_ctx, match_ctx, player_state):
            return False
        if not self._check_dora_constraint(matched_variant, match_ctx):
            return False
        if not self._check_riichi_constraint(discard):
            return False
        if not self._check_call_constraint(discard, player_state):
            return False
        if not self._check_call_area_constraints(discard, matched_variant, player_state, round_players):
            return False
        if not self._check_visible_constraints(matched_variant, player_state):
            return False
        if not self._check_hand_visible_constraints(discard, matched_variant, player_state, round_players):
            return False
        if not self._check_player_visible_constraints(
            discard, matched_variant, player_state, round_players
        ):
            return False
        if not self._check_target_discard_exclusion(discard, matched_variant, match_ctx):
            return False
        # 即时铳率：模式达成之前，目标玩家不得曾打出过任一假想目标牌（多目标取并集）
        if not self._check_instant_prior_no_target_discard(matched_variant, match_ctx):
            return False
        return True

    def _check_prior_discard_exclusion(
        self, matched_variant: Dict, full_discards: List, match_ctx: Dict, player_state
    ) -> bool:
        """禁打校验：前段禁打（prior_discard_exclusion）相关：匹配点之前不得出现禁止牌。"""
        if not self.prior_discard_exclusion:
            return True
        forbidden = get_prior_discard_exclusion_forbidden_bases(matched_variant)
        if not forbidden:
            return True
        start_idx = matched_variant.get("matched_start_index")
        if start_idx is not None:
            prior_tiles = full_discards[:start_idx]
            if any(MjlogParser.string_to_tile(t[0]) in forbidden for t in prior_tiles):
                return False
        elif match_ctx.get("turn_range"):
            first_turn = match_ctx.get("first_turn_in_range")
            prior_discards = match_ctx.get("prior_discards_for_exclusion")
            if first_turn is not None and prior_discards is not None:
                if any((d.tile // 4) in forbidden for d in prior_discards):
                    return False
        return True

    def _check_prior_discard_required(
        self, matched_variant: Dict, full_discards: List, honor_ctx: Dict, match_ctx: Dict, player_state
    ) -> bool:
        """前段有打校验：匹配点之前须出现 required 模式（prior_discard_required）。"""
        prior_req = matched_variant.get("prior_discard_required")
        if not prior_req:
            return True
        prior_req_variants = prior_required_pattern_to_variants(prior_req)
        if not prior_req_variants:
            return True
        start_idx = matched_variant.get("matched_start_index")
        turn_range = match_ctx.get("turn_range")
        if start_idx is not None and start_idx > 0:
            prior_tiles = full_discards[:start_idx]
        elif turn_range:
            first_turn = match_ctx.get("first_turn_in_range")
            if first_turn is not None and player_state is not None:
                prior_tiles = [
                    (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                    for d in player_state.discards
                    if d.turn < first_turn
                ]
            else:
                prior_tiles = []
        else:
            prior_tiles = []
        if not prior_tiles:
            return False
        if not match_discard_pattern_contained(prior_tiles, prior_req_variants, honor_ctx):
            return False
        return True

    def _check_dora_constraint(self, matched_variant: Dict, match_ctx: Dict) -> bool:
        """宝牌校验：dora_unrelated（舍牌花色与宝牌不同）、dora_matches_position（宝牌出现在指定位置）。"""
        dora_str = match_ctx.get("dora_str")
        if not dora_str:
            return True
        if self.dora_constraint == "dora_unrelated":
            pattern_suit = None
            for elem in matched_variant.get("discard") or []:
                t = elem[0] if isinstance(elem, tuple) else elem
                if isinstance(t, str) and len(t) >= 2 and t[-1] in "mps":
                    pattern_suit = t[-1]
                    break
            if pattern_suit and dora_str[-1] == pattern_suit:
                return False
        if self.dora_constraint == "dora_matches_position" and self.dora_position_spec:
            pos_to_tile = matched_variant.get("position_to_tile") or {}
            for pos in self.dora_position_spec:
                tile_at_pos = pos_to_tile.get(pos)
                if tile_at_pos is None or not _tile_str_eq(tile_at_pos, dora_str):
                    return False
        return True

    def _check_riichi_constraint(self, discard) -> bool:
        """立直约束：has_riichi / no_riichi。"""
        if not self.riichi_constraint or self.riichi_constraint == "any":
            return True
        if self.riichi_constraint == "has_riichi" and not discard.riichi_happened:
            return False
        if self.riichi_constraint == "no_riichi" and _opponent_riichi_happened(discard):
            return False
        return True

    def _check_call_constraint(self, discard, player_state=None) -> bool:
        """副露约束（舍牌级）：该舍牌是否在鸣牌后打出，或目标玩家当前瞬间是否无副露。"""
        if self.call_constraint and self.call_constraint != "any":
            if self.call_constraint == "has_call" and not discard.call_happened:
                return False
            if self.call_constraint == "no_call" and discard.call_happened:
                return False
        
        # 目标无副露约束：仅针对分析目标的玩家在满足舍牌模式的瞬间没有副露
        if self.target_no_call and player_state:
            # 检查该玩家在该巡之前（含该巡）是否有已完成的副露
            # from_discard_turn 是副露后第一张舍牌的巡目；若 <= 当前巡目，则已完成副露
            if any(getattr(c, "from_discard_turn", 1) <= discard.turn for c in getattr(player_state, "calls", [])):
                return False
        return True

    def _check_call_area_constraints(
        self, discard, matched_variant: Dict, player_state, round_players: List
    ) -> bool:
        """副露区域约束：玩家在该舍牌时点须满足副露区域条件。"""
        _ca = matched_variant.get("call_area_constraints") or self.call_area_constraints
        if not _ca:
            return True
        return player_satisfies_call_area_constraints(
            player_state, round_players, player_state.oya, _ca,
            current_discard_turn=discard.turn,
        )

    def _check_visible_constraints(self, matched_variant: Dict, player_state) -> bool:
        """可见牌约束：从变体带入的 visible_constraints（等价变换后），场上可见枚数须在范围内。"""
        vc = matched_variant.get("visible_constraints")
        if not vc:
            return True
        for tile_str, (min_count, max_count) in vc.items():
            base_code = MjlogParser.string_to_tile(tile_str)
            equiv_bases = MjlogParser.get_count_equivalent_bases(base_code)
            count = sum(c for t, c in player_state.visible_tiles.items() if t // 4 in equiv_bases)
            if not (min_count <= count <= max_count):
                return False
        return True

    def _check_hand_visible_constraints(self, discard, matched_variant: Dict, player_state, round_players: List) -> bool:
        """手牌可见枚数约束（延伸手牌）：除目标玩家外的三名玩家，其手牌+牌山中目标牌枚数须在范围内。"""
        hvc = matched_variant.get("hand_visible_constraints")
        if not hvc:
            return True

        all_turns = getattr(discard, "all_players_turns", None)
        hands_counts = _hands_counters_at_discard_moment(discard, player_state, round_players)
        if hands_counts is None or all_turns is None:
            return True

        # 1. 统计当前瞬间全场已暴露（非隐藏）的牌（舍牌+副露+表宝指示）。
        global_exposed = Counter()
        for p in round_players:
            p_turn = all_turns[p.player_id]
            for d in p.discards:
                if d.turn <= p_turn:
                    global_exposed[d.tile // 4] += 1
            for c in p.calls:
                if getattr(c, "from_discard_turn", 1) <= p_turn:
                    global_exposed[MjlogParser.string_to_tile(c.pai)] += 1
                    for cp in c.consumed:
                        global_exposed[MjlogParser.string_to_tile(cp)] += 1
        if round_players:
            for ind in round_players[0].dora_indicators:
                global_exposed[ind // 4] += 1

        # 3. 计算牌山中剩余各牌的枚数
        # Wall(T) = 4 - Exposed(T) - Sum(Hand(all, T))
        wall_counts = Counter()
        all_bases = set(global_exposed.keys())
        for hc in hands_counts:
            all_bases.update(hc.keys())
        
        for b in all_bases:
            revealed_in_hands = sum(hc[b] for hc in hands_counts)
            wall_counts[b] = max(0, 4 - global_exposed[b] - revealed_in_hands)

        # 4. 校验约束
        target_pid = player_state.player_id
        for tile_str, (min_c, max_c) in hvc.items():
            base_code = MjlogParser.string_to_tile(tile_str)
            equiv_bases = MjlogParser.get_count_equivalent_bases(base_code)
            
            # 延伸手牌定义：该玩家的手牌 + 牌山里的任意牌。
            # 对于每一名非目标玩家：
            for pid in range(4):
                if pid == target_pid:
                    continue
                
                # 计算该玩家手中等价牌的总数
                hand_c = sum(hands_counts[pid][b] for b in equiv_bases)
                # 计算牌山中等价牌的总数
                wall_c = sum(wall_counts[b] for b in equiv_bases)
                
                # 延伸手牌的可能枚数范围为 [hand_c, hand_c + wall_c]
                # 若该范围与 [min_c, max_c] 无交集，则不满足
                if hand_c > max_c or (hand_c + wall_c) < min_c:
                    return False
        
        return True

    def _check_player_visible_constraints(
        self, discard, matched_variant: Dict, player_state, round_players: List
    ) -> bool:
        """
        玩家可见（player visible）：目标玩家手牌中该牌枚数 + 场上可见（visible_tiles 口径）须在范围内。
        手牌为打出瞬间计数，与延伸手牌逻辑一致，含本张打出牌。
        """
        pvc = matched_variant.get("player_visible_constraints")
        if not pvc:
            return True
        hands_counts = _hands_counters_at_discard_moment(discard, player_state, round_players)
        if hands_counts is None:
            return True
        tp = player_state.player_id
        for tile_str, (min_c, max_c) in pvc.items():
            base_code = MjlogParser.string_to_tile(tile_str)
            equiv_bases = MjlogParser.get_count_equivalent_bases(base_code)
            hand_c = sum(hands_counts[tp][b] for b in equiv_bases)
            field_c = sum(
                c for t, c in player_state.visible_tiles.items() if t // 4 in equiv_bases
            )
            total = hand_c + field_c
            if not (min_c <= total <= max_c):
                return False
        return True

    def _check_target_discard_exclusion(
        self, discard, matched_variant: Dict, match_ctx: Dict
    ) -> bool:
        """
        目标牌排除：分析「目标牌存量」时，若当前舍牌即为目标牌（或等价牌），则排除该样本。
        等价于原逻辑：not use_tenpai and not use_deal_in_instant and not use_related_tile 时，
        if discard.tile // 4 in target_equiv: continue
        """
        if self.use_tenpai or self.use_deal_in_instant or self.use_related_tile:
            return True
        multi_t = match_ctx.get("multi_t")
        item_combo = match_ctx.get("item_combo")
        mapped_target = matched_variant.get("target")
        if mapped_target is None:
            return True
        target_equiv = set()
        if multi_t is None:
            # 单目标、非 combo：analyze 场景可能无 multi_t，用 mapped_target 直接
            target_equiv = MjlogParser.get_bases_for_target_tile_str(
                mapped_target if isinstance(mapped_target, str) else mapped_target[0]
            )
        else:
            if len(multi_t) == 1:
                if item_combo:
                    for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target]):
                        target_equiv |= MjlogParser.get_bases_for_target_tile_str(t)
                else:
                    target_equiv = MjlogParser.get_bases_for_target_tile_str(mapped_target)
            else:
                target_equiv = MjlogParser.get_bases_for_target_tile_str(
                    mapped_target if isinstance(mapped_target, str) else mapped_target[0]
                )
                for tiles, _ in multi_t[1:]:
                    for t in tiles:
                        target_equiv |= MjlogParser.get_bases_for_target_tile_str(t)
        if discard.tile // 4 in target_equiv:
            return False
        return True

    def _check_instant_prior_no_target_discard(self, matched_variant: Dict, match_ctx: Dict) -> bool:
        """
        即时铳率（instant deal-in）专用：本局内、达成舍牌模式的那一打「之前」，
        目标玩家不得曾打出过任一统计用目标牌（多目标为 base 并集，含赤五等价）。
        使用 discard_orig_i 遍历 player_state.discards，避免 grid 模式仅截断巡目窗口导致漏检。
        """
        if not self.use_deal_in_instant:
            return True
        player_state = match_ctx.get("player_state")
        orig_i = match_ctx.get("discard_orig_i")
        if player_state is None or orig_i is None:
            return True
        mapped_target = matched_variant.get("target")
        if mapped_target is None:
            return True
        target_equiv: set = set()
        if isinstance(mapped_target, list):
            for t in mapped_target:
                if t:
                    target_equiv |= MjlogParser.get_bases_for_target_tile_str(t)
        else:
            target_equiv = MjlogParser.get_bases_for_target_tile_str(mapped_target)
        if not target_equiv:
            return True
        discards = getattr(player_state, "discards", None) or []
        for i in range(int(orig_i)):
            if i >= len(discards):
                break
            prev = discards[i]
            t_base = getattr(prev, "tile", None)
            if t_base is None:
                continue
            if (t_base // 4) in target_equiv:
                return False
        return True


def _clamp_analysis_batch_size(batch_size: int, workers: int, use_parallel: bool) -> int:
    """Clamp analysis batch size to keep parallel memory usage bounded."""
    requested = max(MIN_ANALYSIS_BATCH_SIZE, int(batch_size))
    # 现在有管理员权限清理 Standby，放宽限制，以 GUI 设定的数值为准
    return requested


def _trim_process_memory() -> None:
    """Best-effort memory trim for long runs (mainly effective on Windows)."""
    if not ENABLE_TRIM_PROCESS_MEMORY:
        return
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


def _maybe_analysis_gc() -> None:
    """全量 GC：长分析中途调用成本高；由 ENABLE_GC_COLLECT_DURING_ANALYSIS 控制。"""
    if ENABLE_GC_COLLECT_DURING_ANALYSIS:
        gc.collect()


def _should_run_memory_maintenance(batch_index: int, gc_interval_batches: Optional[int] = None, is_instant_mode: bool = False) -> bool:
    """Run maintenance periodically, or early under high system memory pressure."""
    if not ENABLE_PERIODIC_MEMORY_MAINTENANCE:
        return False
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
            # 对每个已完成任务：result → 立刻 yield → 立刻 submit 补位，避免先把本轮 done 全部 unpickle 再统一投喂（主线程 CPU 突刺、worker 短暂挨饿）。
            # 若 next(task_iter) 阻塞，仅影响同轮后续 fut 的交付顺序；FIRST_COMPLETED 下 done 通常仅 1 个。
            for fut in done:
                r = fut.result()
                yield r
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


# SQLite：PRAGMA cache_size 负值 = 缓存上限（KiB）。过小则大 batch 读 BLOB 时页抖动、磁盘占用呈锯齿。
SQLITE_DEFAULT_CACHE_KIB = 20000  # ~20 MiB，主线程偶发查询 / 维护重连
SQLITE_LOG_SCANNER_CACHE_KIB = 262144  # ~256 MiB，仅顺序扫 logs 的预取连接（内存换 I/O 平滑）
# 预取队列容量按「chunk 槽位」计：每 SQL 批会拆成多个 fetchmany 块先入队，worker 可更早开工（避免整批 fetchall 结束才投喂）
BACKGROUND_FETCHER_QUEUE_MAX_BATCHES = 8
BACKGROUND_FETCHER_FETCHMANY_ROWS = 400


def _connect_db_memory_efficient(db_path: str, *, log_sequential_reader: bool = False):
    """
    创建限制内存占用的 SQLite 连接。
    避免 tenhou.db 在长时分析中占用十余 GB 内存（SQLite 缓存 + OS 文件缓存）。
    log_sequential_reader=True：给 BackgroundLogFetcher 等大段顺序读 BLOB 的连接用更大页缓存，
    减轻 batchsize 上万时页频繁换出导致的磁盘锯齿（disk active time 脉冲）。
    """
    conn = sqlite3.connect(db_path, timeout=60)
    # PRAGMA cache_size 为负时表示「KiB」；默认 ~20MiB，扫表连接 ~256MiB（可调 SQLITE_*_CACHE_KIB）
    cache_kib = SQLITE_LOG_SCANNER_CACHE_KIB if log_sequential_reader else SQLITE_DEFAULT_CACHE_KIB
    conn.execute(f"PRAGMA cache_size = {-int(cache_kib)}")
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


def iter_valid_discards(
    game_states: List[GameState],
    params: Dict,
) -> Iterator[Tuple[GameState, List[GameState], int, int, object]]:
    """
    遍历所有通过前置校验的舍牌，扁平化原 for-round -> for-player -> for-discard 的深层嵌套。
    将局级、玩家级、舍牌级的基础过滤集中在此，产出符合前置条件的 (player_state, round_players, round_idx, orig_i, discard)。

    前置过滤包括：
    - 局级：exclude_south4/exclude_south3、dora_constraint（基础宝牌匹配）、
      round_has_matching_consumed、round_could_satisfy_call_constraints、riichi_any
    - 玩家级：call_constraint=="no_call"（无副露）、call_area_constraints
    - 舍牌级：turn_range（可选）、riichi_constraint、call_constraint（has_call/no_call 针对该舍牌）

    Yield:
        (player_state, round_players, round_idx, orig_i, discard) 元组
        round_idx 用于即时铳率分析时索引 round_payloads
    """
    round_size = 4
    exclude_south4 = params.get("exclude_south4", False)
    exclude_south3 = params.get("exclude_south3", False)
    dora_constraint = params.get("dora_constraint")
    call_constraint = params.get("call_constraint")
    target_no_call = params.get("target_no_call", False)
    call_area_constraints = params.get("call_area_constraints")
    call_area_constraint_sets = params.get("call_area_constraint_sets")
    consumed_search_list = params.get("consumed_search_list", [])
    riichi_any = params.get("riichi_any", False)
    riichi_constraint = params.get("riichi_constraint")
    turn_range = params.get("turn_range")

    for round_start in range(0, len(game_states), round_size):
        round_players = game_states[round_start : round_start + round_size]
        if len(round_players) < round_size:
            break

        # 局级：南四/南三排除
        rn = getattr(round_players[0], "round_num", 0)
        if (exclude_south4 and rn == 7) or (exclude_south3 and rn == 6):
            continue

        oya = getattr(round_players[0], "oya", 0)

        # 局级：宝牌约束（非 dora_unrelated/dora_matches_position 的精确匹配）
        if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
            dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
            if dora_constraint not in ("dora_unrelated", "dora_matches_position") and not _dora_matches_constraint(
                dora_str, dora_constraint
            ):
                continue

        # 局级：消耗牌（consumed search）约束
        if consumed_search_list:
            if not any(round_has_matching_consumed(round_players, cs) for cs in consumed_search_list):
                continue

        # 局级：副露约束（round 内至少有一名玩家可能满足）
        if call_constraint or target_no_call or call_area_constraints or call_area_constraint_sets:
            if not round_could_satisfy_call_constraints(
                round_players,
                call_constraint,
                None if call_area_constraint_sets else call_area_constraints,
                oya,
                call_area_constraint_sets=call_area_constraint_sets,
                target_no_call=target_no_call,
            ):
                continue

        # 局级：任意立直（riichi_any）——局内需有至少一次立直宣言
        if riichi_any:
            if not any(
                any(getattr(d, "is_riichi_declaration", False) for d in p.discards) for p in round_players
            ):
                continue

        for player_state in round_players:
            # 玩家级：call_constraint=="no_call" 时，有副露则跳过
            # 如果开启了 target_no_call，则不在此处做全局跳过，而是在舍牌级精确判断
            if not target_no_call and call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
                continue

            # 玩家级：副露区域约束
            if call_area_constraint_sets:
                if not player_could_satisfy_any_call_area_constraints(player_state, oya, call_area_constraint_sets):
                    continue
            elif call_area_constraints and not player_could_satisfy_call_area_constraints(
                player_state, oya, call_area_constraints
            ):
                continue

            # 构建舍牌列表（可选 turn_range 过滤）
            if turn_range:
                min_turn, max_turn = turn_range
                discard_list = [(i, d) for i, d in enumerate(player_state.discards) if min_turn <= d.turn <= max_turn]
            else:
                discard_list = [(i, d) for i, d in enumerate(player_state.discards) if hasattr(d, "turn") and hasattr(d, "tile")]

            if not discard_list:
                continue

            for orig_i, discard in discard_list:
                # 舍牌级：立直约束（has_riichi / no_riichi）
                if riichi_constraint and riichi_constraint != "any":
                    if riichi_constraint == "has_riichi" and not discard.riichi_happened:
                        continue
                    if riichi_constraint == "no_riichi" and _opponent_riichi_happened(discard):
                        continue

                # 舍牌级：副露约束（该舍牌是否在鸣牌后打出，或目标玩家无副露）
                if (call_constraint and call_constraint != "any") or target_no_call:
                    if call_constraint == "has_call" and not discard.call_happened:
                        continue
                    if call_constraint == "no_call" and discard.call_happened:
                        continue
                    # 目标无副露：仅针对本玩家在当前瞬间
                    if target_no_call:
                        if any(getattr(c, "from_discard_turn", 1) <= discard.turn for c in getattr(player_state, "calls", [])):
                            continue

                round_idx = round_start // round_size
                yield (player_state, round_players, round_idx, orig_i, discard)


def _core_match_engine(
    raw_content: str,
    params: Dict,
    is_grid: bool = False,
    pre_parsed_game_states: Optional[List[GameState]] = None,
) -> Iterator[Dict]:
    """
    核心匹配引擎：接管 JSON 解析、遍历与匹配逻辑，作为 analyze 与 grid 共用的底层。

    流程：预检 → 解析 → iter_valid_discards → 变体匹配 → MatchValidator → 产出样本。

    Args:
        raw_content: 已解压的 log 字符串（调用方负责 _get_raw_content）
        params: 分析参数，含 items/item_variants（analyze）或 patterns/grid_meta（grid）
        is_grid: True 为矩阵分析模式，False 为主界面分析
        pre_parsed_game_states: 若已 `parse_tenhou6_json` 过则传入，避免与 extract_tenhou6_rounds 等重复 json.loads

    Yields:
        符合所有约束的样本字典，含 matched_idx, item, player_state, round_players,
        round_idx, discard, orig_i, variant, honor_ctx
    """
    riichi_any = params.get("riichi_any", False)
    consumed_search_list = params.get("consumed_search_list", [])
    items = params.get("items") or []

    # 【1. 预检】快速过滤：riichi_any 与 consumed_search 的 raw 子串检查
    if riichi_any and _is_tenhou6_json(raw_content):
        if "riichi" not in raw_content and "reach" not in raw_content:
            return
    if consumed_search_list and _is_tenhou6_json(raw_content):
        if is_grid:
            if not any(log_contains_consumed(raw_content, cs) for cs in consumed_search_list):
                return
        else:
            if len(items) == 1:
                if not log_contains_consumed(raw_content, consumed_search_list[0]):
                    return
            else:
                if not any(log_contains_consumed(raw_content, cs) for cs in consumed_search_list):
                    return

    # 【2. 解析】worker 侧可与即时铳率共用同一次 tenhou6 解析，避免双重 json.loads
    if pre_parsed_game_states is not None:
        game_states = pre_parsed_game_states
    else:
        game_states = parse_log_to_game_states(raw_content)
    if not game_states:
        return

    # 【3. 初始化】
    validator = MatchValidator(params)
    iter_params = {
        "exclude_south4": params.get("exclude_south4", False),
        "exclude_south3": params.get("exclude_south3", False),
        "dora_constraint": params.get("dora_constraint"),
        "call_constraint": params.get("call_constraint"),
        "target_no_call": params.get("target_no_call", False),
        "call_area_constraints": params.get("call_area_constraints"),
        "call_area_constraint_sets": params.get("call_area_constraint_sets"),
        "consumed_search_list": consumed_search_list,
        "riichi_any": riichi_any,
        "riichi_constraint": params.get("riichi_constraint"),
        "turn_range": None if is_grid else params.get("turn_range"),
    }
    patterns = params.get("patterns") or []
    grid_meta = params.get("grid_meta") or []
    item_variants = params.get("item_variants") or []
    item_multi_targets = params.get("item_multi_targets") or []
    dora_position_spec = params.get("dora_position_spec") or []

    player_cache = {}

    # 【4. 扁平化遍历】
    for player_state, round_players, round_idx, orig_i, discard in iter_valid_discards(
        game_states, iter_params
    ):
        cache_key = (id(round_players), player_state.player_id)
        if cache_key not in player_cache:
            all_discards = [
                (i, d) for i, d in enumerate(player_state.discards)
                if hasattr(d, "turn") and hasattr(d, "tile")
            ]
            discards_precomputed = [
                (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri) for _, d in all_discards
            ]
            discard_riichi_flags = [
                getattr(all_discards[i][1], "is_riichi_declaration", False)
                for i in range(len(all_discards))
            ]
            honor_ctx_base = {
                "jikaze": MjlogParser.get_jikaze(
                    player_state.player_id, player_state.oya, player_state.round_num
                ),
                "bakaze": MjlogParser.resolve_bakaze(
                    player_state.round_num, getattr(player_state, "bakaze", None)
                ),
                "kyokuze_list": MjlogParser.get_kyokuze_list(
                    player_state.player_id, player_state.oya, player_state.round_num
                ),
                "calls": getattr(player_state, "calls", []),
                "dora_indicators": player_state.dora_indicators,
            }
            player_cache[cache_key] = (
                all_discards,
                discards_precomputed,
                discard_riichi_flags,
                honor_ctx_base,
            )
        all_discards, discards_precomputed, discard_riichi_flags, honor_ctx_base = player_cache[
            cache_key
        ]

        dora_str = None
        if round_players and round_players[0].dora_indicators:
            dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])

        if is_grid:
            # 【Grid 模式】遍历每个 cell，仅在当前舍牌为该 cell 的「最后一张在范围内」时匹配
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
                visible_with_own = _visible_tiles_with_own_discards(
                    player_state.visible_tiles, full_discards_up_to_now
                )
                hand_after_by_index = [
                    list(player_state.hand_tiles_history[idx])
                    if idx < len(player_state.hand_tiles_history)
                    else list(player_state.hand_tiles)
                    for idx, _ in in_range_for_cell
                ]
                honor_ctx = {
                    **honor_ctx_base,
                    "visible_tiles": visible_with_own,
                    "current_discard_turn": discard_turn,
                    "discard_riichi_flags": [discard_riichi_flags[idx] for idx, _ in in_range_for_cell],
                    "hand_after_by_index": hand_after_by_index,
                }

                pattern, target = patterns[pat_idx] if pat_idx < len(patterns) else (None, None)
                if pattern is None:
                    continue
                cs = get_consumed_search_patterns(pattern)
                if cs:
                    if not round_has_matching_consumed(round_players, cs):
                        continue
                    if not player_has_matching_consumed(player_state, cs):
                        continue

                matched_variant = match_discard_to_variant(
                    full_discards_up_to_now, vars_p, honor_ctx
                )
                if not matched_variant:
                    continue

                first_turn_in_range = in_range_for_cell[0][1].turn
                prior_discards_for_exclusion = [
                    d for d in player_state.discards if d.turn < first_turn_in_range
                ]
                match_ctx = {
                    "full_discards_up_to_now": full_discards_up_to_now,
                    "honor_ctx": honor_ctx,
                    "player_state": player_state,
                    "round_players": round_players,
                    "dora_str": dora_str,
                    "dora_position_spec": dora_position_spec,
                    "turn_range": turn_range,
                    "first_turn_in_range": first_turn_in_range,
                    "prior_discards_for_exclusion": prior_discards_for_exclusion,
                    "multi_t": multi_t,
                    "item_combo": is_combo,
                    "discard_orig_i": orig_i,
                }

                if not validator.validate_all(discard, matched_variant, match_ctx):
                    continue

                # Grid 下游需 full_discards_up_to_now、in_range_indices 计算 target_count（含 use_related_tile）
                in_range_indices = [idx for idx, _ in in_range_for_cell]
                item = (pattern, target)
                yield {
                    "matched_idx": (tr_idx, pat_idx),
                    "item": item,
                    "player_state": player_state,
                    "round_players": round_players,
                    "round_idx": round_idx,
                    "discard": discard,
                    "orig_i": orig_i,
                    "variant": matched_variant,
                    "honor_ctx": honor_ctx,
                    "full_discards_up_to_now": full_discards_up_to_now,
                    "in_range_indices": in_range_indices,
                    "multi_t": multi_t,
                    "is_combo": is_combo,
                }
        else:
            # 【Analyze 模式】遍历 items，首匹配即产出并 break
            full_discards_up_to_now = discards_precomputed[: orig_i + 1]
            visible_with_own = _visible_tiles_with_own_discards(
                player_state.visible_tiles, full_discards_up_to_now
            )
            hand_after_by_index = [
                list(player_state.hand_tiles_history[i])
                if i < len(player_state.hand_tiles_history)
                else list(player_state.hand_tiles)
                for i in range(len(full_discards_up_to_now))
            ]
            honor_ctx = {
                **honor_ctx_base,
                "visible_tiles": visible_with_own,
                "current_discard_turn": discard.turn,
                "discard_riichi_flags": discard_riichi_flags[: orig_i + 1],
                "hand_after_by_index": hand_after_by_index,
            }

            turn_range = params.get("turn_range")
            first_turn_in_range = None
            prior_discards_for_exclusion = None
            if turn_range and items:
                in_range = [
                    (i, d) for i, d in enumerate(player_state.discards)
                    if turn_range[0] <= d.turn <= turn_range[1]
                ]
                if in_range:
                    first_turn_in_range = in_range[0][1].turn
                    prior_discards_for_exclusion = [
                        d for d in player_state.discards if d.turn < first_turn_in_range
                    ]

            match_ctx_base = {
                "full_discards_up_to_now": full_discards_up_to_now,
                "honor_ctx": honor_ctx,
                "player_state": player_state,
                "round_players": round_players,
                "dora_str": dora_str,
                "dora_position_spec": dora_position_spec,
                "turn_range": turn_range,
                "first_turn_in_range": first_turn_in_range,
                "prior_discards_for_exclusion": prior_discards_for_exclusion,
                "discard_orig_i": orig_i,
            }

            # 多模式：默认可对同一巡分别记录每条舍牌模式的命中（matched_idx 对应真实命中的行），
            # 以便役牌手持等场景的 pattern 分布与 sample_pool 按模式筛选一致。
            # 即时铳率 + 多模式：各模式目标牌可能不同，全局铳点/铳率仍保持「首条命中即停」以免重复计数与歧义。
            leave_after_first_match = bool(
                params.get("use_deal_in_instant", False) and len(items) > 1
            )
            for idx, (vars_p, _, item_combo) in enumerate(item_variants):
                if idx >= len(items):
                    break
                pattern = items[idx][0] if items[idx] else None
                if pattern is None:
                    continue
                cs = get_consumed_search_patterns(pattern)
                if cs:
                    if not round_has_matching_consumed(round_players, cs):
                        continue
                    if not player_has_matching_consumed(player_state, cs):
                        continue

                matched_variant = match_discard_to_variant(
                    full_discards_up_to_now, vars_p, honor_ctx
                )
                if not matched_variant:
                    continue

                mt_item = item_multi_targets[idx] if idx < len(item_multi_targets) else []
                match_ctx = {
                    **match_ctx_base,
                    "multi_t": mt_item,
                    "item_combo": item_combo,
                }

                if not validator.validate_all(discard, matched_variant, match_ctx):
                    continue

                item = items[idx] if idx < len(items) else None
                yield {
                    "matched_idx": idx,
                    "item": item,
                    "player_state": player_state,
                    "round_players": round_players,
                    "round_idx": round_idx,
                    "discard": discard,
                    "orig_i": orig_i,
                    "variant": matched_variant,
                    "honor_ctx": honor_ctx,
                }
                if leave_after_first_match:
                    break


def _merge_instant_grid_detail(dst: Dict, src: Dict) -> None:
    """将单条 log 的即时铳率矩阵增量合并到累加器（total、各目标 hits/points）。"""
    dst["total"] = dst.get("total", 0) + src.get("total", 0)
    dst_targets = dst.setdefault("targets", {})
    for tk, st in src.get("targets", {}).items():
        t = dst_targets.setdefault(tk, {"hits": 0, "points": 0})
        t["hits"] += int(st.get("hits", 0))
        t["points"] += int(st.get("points", 0))


def _instant_grid_target_keys(multi_t: Optional[List]) -> List[str]:
    """
    与矩阵 worker 中按 multi_t 展开目标键一致（非 combo 项），供表头与 UI 勾选序列排序。
    """
    out: List[str] = []
    for tiles, is_cb in multi_t or []:
        if is_cb:
            continue
        out.append("".join(sorted(tiles)) if is_cb else tiles[0])
    return out


def _process_one_log_grid(task: Tuple) -> Dict:
    """
    Per-log 并行 worker：处理单条牌谱，返回该log 对各格的增量统计。
    使用 _core_match_engine 作为底层，本函数仅负责数据聚合。
    """
    log_id, log_content, params = task
    use_tenpai = params.get("use_tenpai", False)
    use_related_tile = params.get("use_related_tile", False)
    use_deal_in_instant = params.get("use_deal_in_instant", False)
    independence_filter = bool(params.get("independence_filter", False))
    grid_meta = params.get("grid_meta") or []
    hypothetical_furiten_list = params.get("hypothetical_furiten_list") or []
    norm_oya = bool(params.get("instant_normalize_oya_ron_to_ko", False))

    def _target_key(tiles: List[str], is_combo: bool) -> str:
        return "".join(sorted(tiles)) if is_combo else tiles[0]

    result = {}  # (tr_idx, pat_idx) -> {0: n, 1: n, ...}

    try:
        raw = _get_raw_content(log_content)
        parsed_tenhou6: Optional[dict] = None
        if _is_tenhou6_json(raw):
            try:
                parsed_tenhou6 = json.loads(raw)
            except Exception:
                parsed_tenhou6 = None
        # 即时铳率（instant deal-in）矩阵：与主分析相同，需 tenhou6 事件流重放
        round_payloads: List = []
        if use_deal_in_instant:
            if parsed_tenhou6 is not None:
                round_payloads = extract_tenhou6_rounds(parsed_tenhou6)
            else:
                round_payloads = extract_tenhou6_rounds(_raw_to_tenhou6_for_instant(raw))
        pre_gs: Optional[List[GameState]] = None
        if parsed_tenhou6 is not None:
            try:
                from .tenhou6_adapter import parse_tenhou6_json

                pre_gs = parse_tenhou6_json(parsed_tenhou6)
            except Exception:
                pre_gs = None
        if pre_gs is not None:
            matches = _core_match_engine(
                raw, params, is_grid=True, pre_parsed_game_states=pre_gs
            )
        else:
            matches = _core_match_engine(raw, params, is_grid=True)

        instant_analyzers: Dict = {}

        for m in matches:
            player_state = m["player_state"]
            round_players = m["round_players"]
            round_idx = m["round_idx"]
            discard = m["discard"]
            orig_i = m["orig_i"]
            matched_variant = m["variant"]
            honor_ctx = m["honor_ctx"]
            full_discards_up_to_now = m.get("full_discards_up_to_now") or []
            in_range_indices = m.get("in_range_indices") or []
            multi_t = m.get("multi_t") or []
            is_combo = m.get("is_combo", False)
            tr_idx, pat_idx = m["matched_idx"]

            k = (tr_idx, pat_idx)

            # 矩阵 × 即时铳率：每格统计 {0: 非可铳, 1: 可铳}，与听牌矩阵同样用 merge_keys=[1] 展示占比
            if use_deal_in_instant:
                round_payload = round_payloads[round_idx] if round_idx < len(round_payloads) else None
                if round_idx not in instant_analyzers and round_payload:
                    try:
                        rp_data, rp_events = round_payload
                        instant_analyzers[round_idx] = RoundInstantDealInAnalyzer(
                            rp_data, rp_events,
                            player_state.round_num, player_state.oya,
                            normalize_oya_ron_to_ko=norm_oya,
                        )
                    except Exception:
                        logger.exception("即时铳率引擎初始化失败（矩阵 worker 本局跳过）")
                        instant_analyzers[round_idx] = None
                round_instant_analyzer = instant_analyzers.get(round_idx)
                mapped_target = matched_variant.get("target")
                mt_item = list(multi_t)
                multi_target = len(mt_item) > 1
                instant_eval: Dict = {}
                instant_eval_multi: Dict = {}
                excluded_this_match_by_hypothetical_furiten = False

                if round_instant_analyzer:
                    if multi_target:
                        m_targets = matched_variant["target"]
                        if isinstance(m_targets, str):
                            m_targets = [m_targets]
                        if len(m_targets) == len(mt_item):
                            for j, (tiles, is_cb) in enumerate(mt_item):
                                if is_cb:
                                    continue
                                tk = _target_key(tiles, is_cb)
                                mapped_t = m_targets[j]
                                instant_eval_multi[tk] = round_instant_analyzer.evaluate(
                                    player_state.player_id, discard.turn, mapped_t
                                )
                            any_hit = any(ev.get("deal_in_hit") for ev in instant_eval_multi.values())
                            if any_hit and hypothetical_furiten_list:
                                tiles_deal_in = _get_hypothetical_furiten_deal_in_tiles(
                                    round_instant_analyzer, player_state.player_id, discard.turn,
                                    hypothetical_furiten_list, matched_variant.get("mapping"),
                                    mapped_targets_to_skip=m_targets,
                                    matched_variant=matched_variant,
                                )
                                if tiles_deal_in:
                                    excluded_this_match_by_hypothetical_furiten = True
                            first_tk = _target_key(mt_item[0][0], mt_item[0][1])
                            instant_eval = instant_eval_multi.get(first_tk, {})
                        else:
                            raise ValueError(
                                "多目标即时铳率：变体 target 与当前条目标数量不一致 "
                                "(len(m_targets)=%s, len(mt_item)=%s)"
                                % (len(m_targets), len(mt_item))
                            )
                    else:
                        mapped_target_str = mapped_target if isinstance(mapped_target, str) else (
                            mapped_target[0] if mapped_target else None
                        )
                        if mapped_target_str:
                            instant_eval = round_instant_analyzer.evaluate(
                                player_state.player_id, discard.turn, mapped_target_str
                            )
                        if instant_eval.get("deal_in_hit") and hypothetical_furiten_list:
                            tiles_deal_in = _get_hypothetical_furiten_deal_in_tiles(
                                round_instant_analyzer, player_state.player_id, discard.turn,
                                hypothetical_furiten_list, matched_variant.get("mapping"),
                                mapped_targets_to_skip=mapped_target_str,
                                matched_variant=matched_variant,
                            )
                            if tiles_deal_in:
                                excluded_this_match_by_hypothetical_furiten = True

                cell_hit = False
                if not excluded_this_match_by_hypothetical_furiten:
                    if multi_target:
                        cell_hit = any(ev.get("deal_in_hit") for ev in instant_eval_multi.values())
                    else:
                        cell_hit = bool(instant_eval.get("deal_in_hit"))
                target_count = 1 if cell_hit else 0

                if k not in result:
                    result[k] = {0: 0, 1: 0, "_instant_detail": {"total": 0, "targets": {}}}
                elif "_instant_detail" not in result[k]:
                    result[k]["_instant_detail"] = {"total": 0, "targets": {}}
                result[k][target_count] = result[k].get(target_count, 0) + 1
                idetail = result[k]["_instant_detail"]
                idetail["total"] += 1
                # 按目标牌累计可铳次数与点数（多目标分开；假想振听排除则本样本对各目标均不计入可铳）
                _tks_order = []
                for _tiles, _is_cb in mt_item:
                    if _is_cb:
                        continue
                    _tks_order.append(_target_key(_tiles, _is_cb))
                for _tk in _tks_order:
                    if _tk not in idetail["targets"]:
                        idetail["targets"][_tk] = {"hits": 0, "points": 0}
                    if excluded_this_match_by_hypothetical_furiten:
                        continue
                    if multi_target:
                        _ev = instant_eval_multi.get(_tk, {})
                        if _ev.get("deal_in_hit"):
                            idetail["targets"][_tk]["hits"] += 1
                            idetail["targets"][_tk]["points"] += int(_ev.get("deal_in_point", 0))
                    else:
                        if instant_eval.get("deal_in_hit"):
                            idetail["targets"][_tk]["hits"] += 1
                            idetail["targets"][_tk]["points"] += int(instant_eval.get("deal_in_point", 0))
                continue

            # 计算 target_count（含 use_tenpai、use_related_tile、multi_t、is_combo）
            hand_after_by_index = honor_ctx.get("hand_after_by_index") or []
            if orig_i < len(player_state.hand_tiles_history):
                hand_at_turn = list(player_state.hand_tiles_history[orig_i])
            else:
                hand_at_turn = list(player_state.hand_tiles)

            mapped_target = matched_variant.get("target")
            target_equiv = None
            if use_tenpai:
                target_count = 1 if is_tenpai(hand_at_turn) else 0
            elif use_related_tile and mapped_target is not None:
                target_equiv = MjlogParser.get_bases_for_target_tile_str(
                    mapped_target if isinstance(mapped_target, str) else mapped_target[0]
                )
                target_discard_idx = None
                target_discard_tile_base = None
                for k in range(len(full_discards_up_to_now) - 1, -1, -1):
                    t_str = full_discards_up_to_now[k][0]
                    base = MjlogParser.string_to_tile(t_str)
                    if base in target_equiv:
                        target_discard_idx = in_range_indices[k] if k < len(in_range_indices) else orig_i
                        target_discard_tile_base = base
                        break
                if target_discard_idx is not None and target_discard_tile_base is not None:
                    hand_at_target = (
                        hand_after_by_index[k]
                        if k < len(hand_after_by_index)
                        else list(player_state.hand_tiles)
                    )
                    num, suit = base_to_discard_num_and_suit(target_discard_tile_base)
                    if num is not None and suit is not None:
                        counts = hand_to_suit_counts(hand_at_target, suit)
                        target_count = 1 if is_related_discard(num, counts) else 0
                    else:
                        target_count = 0
                else:
                    target_count = 0
            elif len(multi_t) > 1:
                if multi_t[0][1]:  # is_combo
                    hand_bases = [t // 4 for t in hand_at_turn]
                    target_count = 1 if all(
                        any(hand_bases.count(b) >= 1 for b in MjlogParser.get_bases_for_target_tile_str(ts))
                        for ts in multi_t[0][0]
                    ) else 0
                    target_count = _apply_combo_independence_filter(
                        hand_at_turn, multi_t[0][0], target_count, independence_filter
                    )
                else:
                    equiv = MjlogParser.get_bases_for_target_tile_str(multi_t[0][0][0])
                    target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
            elif is_combo and mapped_target is not None:
                hand_bases = [t // 4 for t in hand_at_turn]
                mts = mapped_target if isinstance(mapped_target, list) else [mapped_target]
                target_count = 1 if all(
                    any(hand_bases.count(b) >= 1 for b in MjlogParser.get_bases_for_target_tile_str(ts))
                    for ts in mts
                ) else 0
                target_count = _apply_combo_independence_filter(
                    hand_at_turn, mapped_target, target_count, independence_filter
                )
            elif mapped_target is not None:
                equiv = MjlogParser.get_bases_for_target_tile_str(
                    mapped_target if isinstance(mapped_target, str) else mapped_target[0]
                )
                target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
            else:
                target_count = 0

            k = (tr_idx, pat_idx)
            if k not in result:
                result[k] = {0: 0, 1: 0, 2: 0, 3: 0} if not (use_tenpai or use_related_tile or is_combo) else {0: 0, 1: 0}
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
    use_related_tile = params.get("use_related_tile", False)
    use_deal_in_instant = params.get("use_deal_in_instant", False)
    multi_target = params.get("multi_target", False)
    independence_filter = bool(params.get("independence_filter", False))
    use_yaku_hai_hand = bool(params.get("use_yaku_hai_hand", False))
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
    if use_yaku_hai_hand:
        def _yh() -> Dict[int, int]:
            return {i: 0 for i in range(6)}

        # 聚合：役牌种类 kinds（0～5）与役牌「对」副数 pair_units（四桶：零对/一对/两对/三对及以上）
        def _pu() -> Dict[int, int]:
            return {i: 0 for i in range(4)}

        pattern_distributions = [
            {"kinds": _yh(), "pair_units": _pu()}
            for _ in item_variants
        ]
    elif params.get("multi_target"):
        pattern_distributions = [
            {_target_key(t[0], t[1]): ({0: 0, 1: 0} if (use_tenpai or use_related_tile or t[1]) else {0: 0, 1: 0, 2: 0, 3: 0})
             for t in item_multi_targets[idx]}
            for idx in range(len(items))
        ]
    else:
        pattern_distributions = [
            ({0: 0, 1: 0} if (use_tenpai or use_related_tile or iv[2]) else {0: 0, 1: 0, 2: 0, 3: 0})
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
        # 把数据库里的 log 内容转成可解析的原始文本/JSON 字符串（raw content）。
        raw = _get_raw_content(log_content)

        # 【快速过滤（fast prefilter）】如果用户勾选“任意立直（riichi_any）”，
        # 且该 log 是 tenhou6 JSON，那么可以先做一次纯字符串判断：
        # tenhou6 JSON 完全不包含 "riichi"/"reach" 时，后续也不可能命中“存在立直宣言”的约束，
        # 直接 early return 避免 parse 成 GameState（性能优化）。
        if riichi_any and _is_tenhou6_json(raw):
            if "riichi" not in raw and "reach" not in raw:
                return {"total_matches": 0, "outcome_wins": 0, "outcome_deal_ins": 0,
                        "deal_in_hits": 0, "deal_in_point_sum": 0, "excluded_due_to_hypothetical_furiten": 0,
                        "excluded_due_to_hypothetical_furiten_by_tile": {},
                        "pattern_matches": pattern_matches, "pattern_distributions": pattern_distributions,
                        "matched_states": [], "sample_pool": [], "instant_deal_in_dist": instant_deal_in_dist,
                        "instant_deal_in_dist_per_pattern": instant_deal_in_dist_per_pattern}

        # 【快速过滤（fast prefilter）】consumed_search_list 是从“消耗牌约束”衍生出的关键字搜索串。
        # 对 tenhou6 JSON 可以先在 raw 里做轻量 contains 检查，不通过则整局必不可能满足约束，直接 early return。
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

        # tenhou6 JSON：单次 json.loads，供 extract_tenhou6_rounds 与 parse_tenhou6_json 共用，
        # 避免「即时铳率 + 核心引擎」对同一巨串重复解析（重构后主要性能回退点之一）。
        parsed_tenhou6: Optional[dict] = None
        if _is_tenhou6_json(raw):
            try:
                parsed_tenhou6 = json.loads(raw)
            except Exception:
                parsed_tenhou6 = None

        # 即时铳率需要 tenhou6 事件流；字典路径零二次 loads
        if use_deal_in_instant:
            if parsed_tenhou6 is not None:
                round_payloads = extract_tenhou6_rounds(parsed_tenhou6)
            else:
                round_payloads = extract_tenhou6_rounds(_raw_to_tenhou6_for_instant(raw))
        else:
            round_payloads = []

        pre_gs: Optional[List[GameState]] = None
        if parsed_tenhou6 is not None:
            try:
                from .tenhou6_adapter import parse_tenhou6_json

                pre_gs = parse_tenhou6_json(parsed_tenhou6)
            except Exception:
                pre_gs = None

        # 使用核心匹配引擎扁平化遍历，本函数仅负责数据聚合。
        instant_analyzers = {}  # 按 round_idx 缓存 RoundInstantDealInAnalyzer，避免同一局多次初始化
        if pre_gs is not None:
            match_engine_iter = _core_match_engine(
                raw, params, is_grid=False, pre_parsed_game_states=pre_gs
            )
        else:
            match_engine_iter = _core_match_engine(raw, params, is_grid=False)

        # 同一巡可被多行舍牌模式同时命中；和牌/放铳结局只计一次，避免 total_matches 膨胀时结局重复累计
        seen_outcome_phys: set = set()
        for m in match_engine_iter:
            matched_idx = m["matched_idx"]
            player_state = m["player_state"]
            round_players = m["round_players"]
            round_idx = m["round_idx"]
            discard = m["discard"]
            orig_i = m["orig_i"]
            matched_variant = m["variant"]
            honor_ctx = m["honor_ctx"]

            full_discards_up_to_now = [
                (MjlogParser.tile_to_string(d.tile), d.is_tsumogiri)
                for d in player_state.discards[: orig_i + 1]
            ]
            all_riichi_flags = [
                getattr(d, "is_riichi_declaration", False) for d in player_state.discards
            ]

            mapped_target = matched_variant["target"]
            mapped_target_str = mapped_target if isinstance(mapped_target, str) else (
                mapped_target[0] if mapped_target else None
            )
            mt_item = item_multi_targets[matched_idx] if matched_idx < len(item_multi_targets) else []
            _, _, item_combo = item_variants[matched_idx] if matched_idx < len(item_variants) else (None, None, False)

            total_matches += 1
            pattern_matches[matched_idx] += 1
            rw = getattr(player_state, "round_winners", [])
            rdi = getattr(player_state, "round_deal_in", None)
            _phys_key_out = (
                player_state.player_id,
                player_state.round_num,
                player_state.honba,
                orig_i,
            )
            if _phys_key_out not in seen_outcome_phys:
                seen_outcome_phys.add(_phys_key_out)
                if rw and player_state.player_id in rw:
                    outcome_wins += 1
                if rdi is not None and rdi == player_state.player_id:
                    outcome_deal_ins += 1

            if orig_i < len(player_state.hand_tiles_history):
                hand_at_turn = list(player_state.hand_tiles_history[orig_i])
            else:
                hand_at_turn = list(player_state.hand_tiles)

            target_equiv = None
            if use_related_tile:
                target_equiv = MjlogParser.get_bases_for_target_tile_str(
                    mapped_target if isinstance(mapped_target, str) else mapped_target[0]
                )

            # 役牌统计：按 player_id/oya/round_num 定役牌集合，聚合 kinds + pair_units（对副四桶）
            yaku_hai_bundle = None
            if use_yaku_hai_hand:
                yaku_bases = MjlogParser.yaku_honor_bases_for_seat(
                    player_state.player_id,
                    player_state.oya,
                    player_state.round_num,
                    getattr(player_state, "bakaze", None),
                )
                st = compute_yaku_hai_hand_stats(hand_at_turn, yaku_bases)
                yaku_per_tile = yaku_hai_per_tile_counts(hand_at_turn, yaku_bases)
                pattern_distributions[matched_idx]["kinds"][st["kinds"]] += 1
                pattern_distributions[matched_idx]["pair_units"][
                    yaku_pair_units_bucket(st["pair_kinds"])
                ] += 1
                target_count = st["pair_kinds"]
                target_counts = None
                yaku_hai_bundle = {
                    **st,
                    "pair_units": yaku_pair_units_bucket(st["pair_kinds"]),
                    "per_tile": yaku_per_tile,
                    "seat_yaku_labels": sorted(
                        MjlogParser.tile_to_string(b * 4) for b in yaku_bases
                    ),
                }
            elif use_tenpai:
                target_count = 1 if is_tenpai(hand_at_turn) else 0
                target_counts = None
            elif use_related_tile:
                # 目标牌指定要判断的舍牌，在匹配序列中找最后一次出现目标牌的舍牌
                target_discard_orig_i = None
                target_discard = None
                in_range = [
                    (i, d) for i, d in enumerate(player_state.discards)
                    if (not turn_range or turn_range[0] <= d.turn <= turn_range[1])
                ]
                j = next((idx for idx, (oi, _) in enumerate(in_range) if oi == orig_i), None)
                if j is not None:
                    for k in range(j, -1, -1):
                        oi2, d2 = in_range[k]
                        if d2.tile // 4 in target_equiv:
                            target_discard_orig_i = oi2
                            target_discard = d2
                            break
                if target_discard_orig_i is not None and target_discard is not None:
                    if target_discard_orig_i < len(player_state.hand_tiles_history):
                        hand_at_target = list(player_state.hand_tiles_history[target_discard_orig_i])
                    else:
                        hand_at_target = list(player_state.hand_tiles)
                    num, suit = base_to_discard_num_and_suit(target_discard.tile // 4)
                    if num is not None and suit is not None:
                        counts = hand_to_suit_counts(hand_at_target, suit)
                        target_count = 1 if is_related_discard(num, counts) else 0
                    else:
                        target_count = 0
                else:
                    target_count = 0
                target_counts = None
            elif len(mt_item) > 1:
                target_counts = {}
                for tiles, is_cb in mt_item:
                    k = _target_key(tiles, is_cb)
                    if is_cb:
                        hand_bases = [t // 4 for t in hand_at_turn]
                        tc = 1 if all(
                            any(hand_bases.count(b) >= 1 for b in MjlogParser.get_bases_for_target_tile_str(ts))
                            for ts in tiles
                        ) else 0
                        target_counts[k] = _apply_combo_independence_filter(
                            hand_at_turn, tiles, tc, independence_filter
                        )
                    else:
                        equiv = MjlogParser.get_bases_for_target_tile_str(tiles[0])
                        target_counts[k] = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                target_count = target_counts.get(_target_key(mt_item[0][0], mt_item[0][1]), 0)
            elif item_combo:
                hand_bases = [t // 4 for t in hand_at_turn]
                mts = mapped_target if isinstance(mapped_target, list) else [mapped_target]
                tc = 1 if all(
                    any(hand_bases.count(b) >= 1 for b in MjlogParser.get_bases_for_target_tile_str(ts))
                    for ts in mts
                ) else 0
                target_count = _apply_combo_independence_filter(
                    hand_at_turn, mapped_target, tc, independence_filter
                )
                target_counts = None
            else:
                equiv = MjlogParser.get_bases_for_target_tile_str(
                    mapped_target if isinstance(mapped_target, str) else mapped_target[0]
                )
                target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                target_counts = None

            instant_eval = {}
            instant_eval_multi = {}
            excluded_this_match_by_hypothetical_furiten = False
            if use_deal_in_instant:
                round_payload = round_payloads[round_idx] if round_idx < len(round_payloads) else None
                if round_idx not in instant_analyzers and round_payload:
                    try:
                        rp_data, rp_events = round_payload
                        norm_oya = params.get("instant_normalize_oya_ron_to_ko", False)
                        instant_analyzers[round_idx] = RoundInstantDealInAnalyzer(
                            rp_data, rp_events,
                            player_state.round_num, player_state.oya,
                            normalize_oya_ron_to_ko=norm_oya,
                        )
                    except Exception:
                        logger.exception("即时铳率引擎初始化失败（本局跳过）")
                        instant_analyzers[round_idx] = None
                round_instant_analyzer = instant_analyzers.get(round_idx)
                if round_instant_analyzer:
                    if multi_target:
                        m_targets = matched_variant["target"]
                        if isinstance(m_targets, str):
                            m_targets = [m_targets]
                        if len(m_targets) == len(mt_item):
                            for tiles, is_cb in mt_item:
                                if is_cb:
                                    continue
                                tk = _target_key(tiles, is_cb)
                                mapped_t = m_targets[mt_item.index((tiles, is_cb))]
                                ev = round_instant_analyzer.evaluate(
                                    player_state.player_id, discard.turn, mapped_t
                                )
                                instant_eval_multi[tk] = ev
                            any_hit = any(ev.get("deal_in_hit") for ev in instant_eval_multi.values())
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
                                        excluded_due_to_hypothetical_furiten_by_tile[t] = (
                                            excluded_due_to_hypothetical_furiten_by_tile.get(t, 0) + 1
                                        )
                            if not excluded_this_match_by_hypothetical_furiten:
                                for tk, ev in instant_eval_multi.items():
                                    if ev.get("deal_in_hit"):
                                        instant_deal_in_dist[tk]["hits"] += 1
                                        instant_deal_in_dist[tk]["points"] += int(ev.get("deal_in_point", 0))
                                        if instant_deal_in_dist_per_pattern and matched_idx < len(instant_deal_in_dist_per_pattern) and tk in instant_deal_in_dist_per_pattern[matched_idx]:
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
                    if not multi_target and instant_eval.get("deal_in_hit") and hypothetical_furiten_list:
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
                                excluded_due_to_hypothetical_furiten_by_tile[t] = (
                                    excluded_due_to_hypothetical_furiten_by_tile.get(t, 0) + 1
                                )
                    if not excluded_this_match_by_hypothetical_furiten:
                        if instant_eval.get("deal_in_hit"):
                            deal_in_hits += 1
                            deal_in_point_sum += int(instant_eval.get("deal_in_point", 0))

            if use_yaku_hai_hand:
                pass
            elif len(mt_item) > 1 and target_counts:
                for k, cnt in target_counts.items():
                    pattern_distributions[matched_idx][k][cnt] = pattern_distributions[matched_idx][k].get(cnt, 0) + 1
            else:
                pattern_distributions[matched_idx][target_count] += 1

            hand_discard_strings = None
            if len(matched_states) < worker_matched_states_cap or len(sample_pool) < worker_sample_pool_cap:
                hand_discard_strings = _format_actual_pattern(
                    full_discards_up_to_now, all_riichi_flags[: orig_i + 1]
                )

            if len(matched_states) < worker_matched_states_cap:
                # matched_states 仅作轻量摘要跨进程序列化：不携带整表 visible_tiles / 手牌列表，降低 IPC 与主进程合并成本
                _vt_ms = dict(player_state.visible_tiles)
                if len(mt_item) > 1 and target_counts:
                    if isinstance(mapped_target, list):
                        _visible_target_ms = ", ".join(
                            f"{t}:{_visible_count(_vt_ms, t)}" for t in mapped_target
                        )
                    else:
                        _visible_target_ms = ", ".join(
                            f"{k}:{_visible_count(_vt_ms, k)}" for k in target_counts
                        )
                elif item_combo and isinstance(mapped_target, list):
                    _visible_target_ms = ", ".join(
                        f"{t}:{_visible_count(_vt_ms, t)}" for t in mapped_target
                    )
                else:
                    _visible_target_ms = str(
                        _visible_count(
                            _vt_ms,
                            mapped_target if isinstance(mapped_target, str) else mapped_target[0],
                        )
                    )
                ms_entry = {
                    "round_num": player_state.round_num,
                    "bakaze": getattr(player_state, "bakaze", None),
                    "turn": discard.turn,
                    "actual_pattern": hand_discard_strings,
                    "mapped_target": mapped_target,
                    "visible_target": _visible_target_ms,
                }
                if target_counts is not None:
                    ms_entry["target_counts"] = target_counts
                else:
                    ms_entry["target_count"] = target_count
                if yaku_hai_bundle is not None:
                    ms_entry["yaku_hai"] = yaku_hai_bundle
                if use_deal_in_instant:
                    _slim_ie = _ipc_copy_instant_eval(instant_eval)
                    if excluded_this_match_by_hypothetical_furiten:
                        _slim_ie["deal_in_hit"] = False
                        _slim_ie["deal_in_point"] = 0
                    ms_entry.update(_slim_ie)
                    if multi_target:
                        if excluded_this_match_by_hypothetical_furiten:
                            ms_entry["instant_eval_multi"] = {
                                k: {
                                    **_ipc_copy_instant_eval(v),
                                    "deal_in_hit": False,
                                    "deal_in_point": 0,
                                }
                                for k, v in instant_eval_multi.items()
                            }
                        else:
                            ms_entry["instant_eval_multi"] = _ipc_copy_instant_eval_multi(
                                instant_eval_multi
                            )
                matched_states.append(ms_entry)

            is_deal_in = use_deal_in_instant and not excluded_this_match_by_hypothetical_furiten and (
                instant_eval.get("deal_in_hit")
                or any(ev.get("deal_in_hit") for ev in (instant_eval_multi or {}).values())
            )
            if len(sample_pool) < worker_sample_pool_cap or is_deal_in:
                if hand_discard_strings is None:
                    hand_discard_strings = _format_actual_pattern(
                        full_discards_up_to_now, all_riichi_flags[: orig_i + 1]
                    )
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
                    "log_id": log_id,
                    "round_num": player_state.round_num,
                    "bakaze": getattr(player_state, "bakaze", None),
                    "honba": player_state.honba,
                    "oya": player_state.oya, "player_id": player_state.player_id, "turn": discard.turn,
                    "actual_pattern": hand_discard_strings.copy() if hand_discard_strings else [],
                    "mapped_target": mapped_target,
                    "hand_tiles": list(hand_at_turn), "visible_tiles": visible_tiles_dict,
                    "dora_readable": dora_readable, "visible_target": visible_target,
                    "call_area": call_area, "is_combo": item_combo, "matched_pattern_idx": matched_idx,
                    "outcome_won": bool(rw and player_state.player_id in rw),
                    "outcome_deal_in": bool(rdi is not None and rdi == player_state.player_id),
                }
                if target_counts is not None:
                    sp_entry["target_counts"] = target_counts
                else:
                    sp_entry["target_count"] = target_count
                if yaku_hai_bundle is not None:
                    sp_entry["yaku_hai"] = yaku_hai_bundle
                if use_deal_in_instant:
                    _slim_sp = _ipc_copy_instant_eval(instant_eval)
                    if excluded_this_match_by_hypothetical_furiten:
                        _slim_sp["deal_in_hit"] = False
                        _slim_sp["deal_in_point"] = 0
                    sp_entry.update(_slim_sp)
                    if multi_target:
                        if excluded_this_match_by_hypothetical_furiten:
                            sp_entry["instant_eval_multi"] = {
                                k: {
                                    **_ipc_copy_instant_eval(v),
                                    "deal_in_hit": False,
                                    "deal_in_point": 0,
                                }
                                for k, v in instant_eval_multi.items()
                            }
                        else:
                            sp_entry["instant_eval_multi"] = _ipc_copy_instant_eval_multi(
                                instant_eval_multi
                            )
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
        target_no_call: bool = False,  # 目标无副露：仅针对分析目标的玩家在满足舍牌模式的瞬间没有副露
        call_area_constraints: Optional[List[str]] = None,  # 副露区域约束，最多4个AND
        hand_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,  # 手牌可见枚数约束（延伸手牌）
        player_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,  # 玩家可见：目标玩家手牌+场上可见合并计数
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
        prior_discard_exclusion: Optional[Union[str, List[str]]] = None,  # 前段不可打；多行/列表时每条并集禁打 base
        prior_discard_required: Optional[str] = None,  # 前段有打：匹配前须出现过该舍牌模式，语法同舍牌模式
        hypothetical_furiten_tiles: Optional[str] = None,  # 假想振听牌，如 6p 或 6p,7p；若也会放铳则不计入主铳率
        max_workers: Optional[int] = None,
        gc_interval_batches: Optional[int] = None,
        instant_use_theory_point_only: Optional[bool] = True,  # 即时铳率：True=平均铳点仅按理论点（表宝牌），False=可考虑里宝模拟（若已实现）
        instant_normalize_oya_ron_to_ko: bool = False,  # 即时铳率：True=亲家和牌时铳点按子家算（折半），统一统计口径
        independence_filter: bool = False,  # 仅目标牌存量+搭子 combo：单花色 (M,T) 价值，V_origin==V_after+(0,1) 判真搭子
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
        use_related_tile = (analysis_target == "related_tile")
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
                if "." in t:
                    raise ValueError(
                        "即时铳率分析不支持目标通配符（如 5.p），请使用 5p 或 0p 明确指定"
                    )
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
            vars_p = generate_equivalent_variants(
                p,
                variant_target,
                visible_constraints,
                prior_discard_exclusion,
                call_area_constraints,
                prior_discard_required,
                hand_visible_constraints,
                player_visible_constraints,
            )
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
        use_yaku_hai_hand = (analysis_target == "yaku_hai_hand")
        multi_target = len(multi_targets) > 1
        # 役牌统计不使用多目标存量语义，避免分布结构冲突（占位目标仍可生成变体）
        if use_yaku_hai_hand:
            multi_target = False
            is_combo = False
        # 独立性筛选仅对「目标牌存量」有意义，其它分析目标忽略该开关
        eff_independence_filter = bool(independence_filter) and analysis_target == "target_count"

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
        if use_yaku_hai_hand:
            # 役牌：kinds（0～5）+ pair_units（四桶）
            def _yaku_hist_kinds() -> Dict[int, int]:
                return {i: 0 for i in range(6)}

            def _yaku_hist_pair_units() -> Dict[int, int]:
                return {i: 0 for i in range(4)}

            pattern_distributions = [
                {
                    "kinds": _yaku_hist_kinds(),
                    "pair_units": _yaku_hist_pair_units(),
                }
                for _ in item_variants
            ]
        elif multi_target:
            pattern_distributions = [
                {_target_key(t[0], t[1]): ({0: 0, 1: 0} if (use_tenpai or use_related_tile or t[1]) else {0: 0, 1: 0, 2: 0, 3: 0})
                 for t in item_multi_targets[idx]}
                for idx in range(len(items))
            ]
        else:
            pattern_distributions = [
                ({0: 0, 1: 0} if (use_tenpai or use_related_tile or iv[2]) else {0: 0, 1: 0, 2: 0, 3: 0})
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
            "target_no_call": target_no_call,
            "call_area_constraints": call_area_constraints,
            "hand_visible_constraints": hand_visible_constraints,
            "player_visible_constraints": player_visible_constraints,
            "call_area_constraint_sets": call_area_constraint_sets,
            "consumed_search_list": consumed_search_list,
            "exclude_south4": exclude_south4,
            "exclude_south3": exclude_south3,
            "riichi_any": riichi_any,
            "prior_discard_exclusion": prior_discard_exclusion,
            "prior_discard_required": prior_discard_required,
            "use_tenpai": use_tenpai,
            "use_related_tile": use_related_tile,
            "use_deal_in_instant": use_deal_in_instant,
            "multi_target": multi_target,
            "hypothetical_furiten_list": hypothetical_furiten_list,
            "instant_use_theory_point_only": instant_use_theory_point_only if use_deal_in_instant else True,
            "instant_normalize_oya_ron_to_ko": instant_normalize_oya_ron_to_ko if use_deal_in_instant else False,
            "cap": cap,
            "worker_matched_states_cap": worker_matched_states_cap,
            "worker_sample_pool_cap": worker_sample_pool_cap,
            "independence_filter": eff_independence_filter,
            "use_yaku_hai_hand": use_yaku_hai_hand,
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

        # 批量读取并分析（用 id 游标分页），避免 OFFSET 越大越慢；预取线程见 BackgroundLogFetcher
        processed = 0
        pool = _make_analysis_process_pool(workers) if use_parallel else None
        maintenance_batch_index = 0

        # 启动后台预取线程，掩盖数据库 I/O 延迟
        fetcher = BackgroundLogFetcher(self.db_path, batch_size, last_id=None)

        # 并行/串行共用的单条合并逻辑（避免三处复制）
        def _merge_one_analyze_per_log(per_log_result: Dict) -> None:
            nonlocal total_matches, outcome_wins_total, outcome_deal_ins_total
            nonlocal deal_in_hits_total, deal_in_point_sum_total, excluded_due_to_hypothetical_furiten
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
            # 合并后立即截断，避免 matched_states / sample_pool 无限增长
            if len(matched_states) > cap:
                del matched_states[cap:]
            if len(sample_pool) > sample_pool_cap:
                del sample_pool[sample_pool_cap:]

        if use_parallel and pool is not None:
            # 跨 SQL 批单段 bounded；预取线程与 _RowTaskPrefetcher 重叠 I/O 与计算，避免主线程包办 next_batch
            def _on_parallel_sql_batch_done() -> None:
                nonlocal maintenance_batch_index, conn, cur
                maintenance_batch_index += 1
                if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches, use_deal_in_instant):
                    _maybe_analysis_gc()
                    try:
                        conn.execute("PRAGMA shrink_memory")
                    except Exception:
                        pass
                    conn.close()
                    conn = _connect_db_memory_efficient(self.db_path)
                    cur = conn.cursor()
                    _ensure_log_json_column(conn)
                    if ENABLE_TRIM_WITH_PERIODIC_MAINTENANCE:
                        _trim_process_memory()

            _max_if = max(2, workers * PARALLEL_IN_FLIGHT_FACTOR)
            _row_pref = _RowTaskPrefetcher(
                fetcher,
                analysis_params,
                on_batch_done=_on_parallel_sql_batch_done,
                should_cancel=lambda: bool(should_cancel and should_cancel()),
                queue_maxsize=max(128, _max_if * 8),
            )
            _parallel_bounded = _iter_pool_results_bounded(
                pool,
                _process_one_log_analyze,
                _row_pref,
                max_in_flight=_max_if,
            )
            try:
                for per_log_result in _parallel_bounded:
                    if should_cancel and should_cancel():
                        logger.info("analysis cancelled")
                        fetcher.stop()
                        conn.close()
                        _maybe_analysis_gc()
                        _trim_process_memory()
                        pool.shutdown(wait=False)
                        return _empty_analysis_result(first_pattern, first_target)
                    _merge_one_analyze_per_log(per_log_result)
                    processed += 1
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                    if sample_limit and processed >= sample_limit:
                        break
            finally:
                _parallel_bounded.close()
                _row_pref.close()
                fetcher.stop()
        else:
            # 串行：单进程逐条，仍按 SQL 批取 log
            while True:
                if should_cancel and should_cancel():
                    logger.info("analysis cancelled")
                    break
                logs = fetcher.next_batch()
                if not logs:
                    break
                for log_id, log_content in logs:
                    if should_cancel and should_cancel():
                        fetcher.stop()
                        conn.close()
                        _maybe_analysis_gc()
                        _trim_process_memory()
                        return _empty_analysis_result(first_pattern, first_target)
                    try:
                        per_log_result = _process_one_log_analyze((log_id, log_content, analysis_params))
                    except Exception as e:
                        logger.error(f"解析对局 {log_id} 失败: {e}")
                        if isinstance(e, ValueError):
                            raise
                        continue
                    _merge_one_analyze_per_log(per_log_result)
                    processed += 1
                    if progress_callback and (processed <= 10 or processed % 10 == 0):
                        progress_callback(processed, total_logs)
                    if sample_limit and processed >= sample_limit:
                        break
                # 与旧逻辑一致：每取完一批 log（无论是否因 sample_limit 提前结束内层循环）都做维护计数
                maintenance_batch_index += 1
                if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches, use_deal_in_instant):
                    _maybe_analysis_gc()
                    try:
                        conn.execute("PRAGMA shrink_memory")
                    except Exception:
                        pass
                    conn.close()
                    conn = _connect_db_memory_efficient(self.db_path)
                    cur = conn.cursor()
                    _ensure_log_json_column(conn)
                    if ENABLE_TRIM_WITH_PERIODIC_MAINTENANCE:
                        _trim_process_memory()
                if sample_limit and processed >= sample_limit:
                    break

        if pool is not None:
            pool.shutdown(wait=True)
        fetcher.stop()
        conn.close()
        _maybe_analysis_gc()
        _trim_process_memory()

        # 并行时可能超过 cap，截断
        matched_states = matched_states[:cap]
        sample_pool = sample_pool[:sample_pool_cap]

        target_count_distribution = pattern_distributions[0]
        probability_distribution = {}
        if use_yaku_hai_hand:
            n0 = max(1, pattern_matches[0])
            probability_distribution = {
                "kinds": {
                    c: (target_count_distribution["kinds"].get(c, 0) / n0 * 100)
                    for c in range(6)
                },
                "pair_units": {
                    c: (target_count_distribution["pair_units"].get(c, 0) / n0 * 100)
                    for c in range(4)
                },
            }
        elif multi_target:
            target_tiles = [_target_key(t[0], t[1]) for t in multi_targets]
            for tk in target_tiles:
                dist_t = target_count_distribution.get(tk, {0: 0, 1: 0, 2: 0, 3: 0})
                prob_t = {}
                for c in [0, 1, 2, 3]:
                    prob_t[c] = (dist_t.get(c, 0) / max(1, pattern_matches[0]) * 100) if pattern_matches[0] > 0 else 0
                probability_distribution[tk] = prob_t
        else:
            keys = [0, 1] if (use_tenpai or use_related_tile or is_combo) else [0, 1, 2, 3]
            for count in keys:
                prob = (target_count_distribution.get(count, 0) / max(1, pattern_matches[0]) * 100) if pattern_matches[0] > 0 else 0
                probability_distribution[count] = prob

        pattern_results = []
        for idx, (p, t) in enumerate(items):
            dist = pattern_distributions[idx]
            mt = item_multi_targets[idx]
            if use_yaku_hai_hand:
                ni = max(1, pattern_matches[idx])
                prob = {
                    "kinds": {c: (dist["kinds"].get(c, 0) / ni * 100) for c in range(6)},
                    "pair_units": {c: (dist["pair_units"].get(c, 0) / ni * 100) for c in range(4)},
                }
            elif len(mt) > 1:
                prob = {}
                for tk in dist:
                    prob_t = {}
                    for c in [0, 1, 2, 3]:
                        prob_t[c] = (dist[tk].get(c, 0) / max(1, pattern_matches[idx]) * 100) if pattern_matches[idx] > 0 else 0
                    prob[tk] = prob_t
            else:
                prob = {}
                k = [0, 1] if (use_tenpai or use_related_tile or item_variants[idx][2]) else [0, 1, 2, 3]
                for c in k:
                    prob[c] = (dist.get(c, 0) / max(1, pattern_matches[idx]) * 100) if pattern_matches[idx] > 0 else 0
            pr_entry = {
                'pattern': p,
                'pattern_str': '-'.join(p),
                'target': t,
                'matches': pattern_matches[idx],
                'target_count_distribution': dist,
                'probability_distribution': prob,
                'is_combo': False if use_yaku_hai_hand else item_variants[idx][2],
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
                        "points": st["points"],
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
            'independence_filter': eff_independence_filter,
        }

        logger.info(f"analysis complete: matched {total_matches} states")
        # 样本一致性校验：抽查样本池中前若干条，确认 actual_pattern 能重新匹配
        if sample_pool and total_matches > 0 and analysis_target != "yaku_hai_hand":
            check_n = min(20, len(sample_pool))
            fail_count = 0
            for s in sample_pool[:check_n]:
                idx = s.get("matched_pattern_idx", 0)
                qp, tt = items[idx]
                ok, err = verify_sample_consistency(
                    s, qp, tt, visible_constraints,
                    hand_visible_constraints=hand_visible_constraints,
                    player_visible_constraints=player_visible_constraints,
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
        elif use_yaku_hai_hand:
            pd_k = probability_distribution.get("kinds", {})
            td_k = target_count_distribution.get("kinds", {})
            parts_k = [f"{i}:{pd_k.get(i, 0):.1f}%({td_k.get(i, 0):,}例)" for i in range(6)]
            logger.info("  役牌种类(>=1枚): %s", " ".join(parts_k))
            pd_u = probability_distribution.get("pair_units", {})
            td_u = target_count_distribution.get("pair_units", {})
            _pu_names = ("零对", "一对", "两对", "三对及以上")
            parts_u = [
                f"{_pu_names[i]}:{pd_u.get(i, 0):.1f}%({td_u.get(i, 0):,}例)" for i in range(4)
            ]
            logger.info("  役牌对副数(每种役牌>=2枚计1副,刻子仍计1副): %s", " ".join(parts_u))
        elif use_tenpai:
            logger.info(f"  未听牌: {probability_distribution[0]:.2f}% ({target_count_distribution[0]:,} 例")
            logger.info(f"  听牌: {probability_distribution[1]:.2f}% ({target_count_distribution[1]:,} 例")
        elif use_related_tile:
            logger.info(f"  非关联: {probability_distribution.get(0, 0):.2f}% ({target_count_distribution.get(0, 0):,} 例)")
            logger.info(f"  关联: {probability_distribution.get(1, 0):.2f}% ({target_count_distribution.get(1, 0):,} 例)")
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
        hand_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        player_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        target_no_call: bool = False,
        call_area_constraints: Optional[List[str]] = None,
        turn_range: Optional[Tuple[int, int]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        exclude_south4: bool = False,
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[Union[str, List[str]]] = None,
        prior_discard_required: Optional[str] = None,
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
            hand_visible_constraints=hand_visible_constraints,
            player_visible_constraints=player_visible_constraints,
            riichi_constraint=riichi_constraint,
            call_constraint=call_constraint,
            target_no_call=target_no_call,
            call_area_constraints=call_area_constraints,
            turn_range=turn_range,
            sample_limit=sample_limit,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            exclude_south4=exclude_south4,
            exclude_south3=exclude_south3,
            prior_discard_exclusion=prior_discard_exclusion,
            prior_discard_required=prior_discard_required,
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
        hand_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        player_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        target_no_call: bool = False,
        call_area_constraints: Optional[List[str]] = None,
        turn_range: Optional[Tuple[int, int]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        exclude_south4: bool = False,
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[Union[str, List[str]]] = None,
        prior_discard_required: Optional[str] = None,
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
            hand_visible_constraints=hand_visible_constraints,
            player_visible_constraints=player_visible_constraints,
            riichi_constraint=riichi_constraint,
            call_constraint=call_constraint,
            target_no_call=target_no_call,
            call_area_constraints=call_area_constraints,
            analysis_target="deal_in_instant",
            turn_range=turn_range,
            sample_limit=sample_limit,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            exclude_south4=exclude_south4,
            exclude_south3=exclude_south3,
            prior_discard_exclusion=prior_discard_exclusion,
            prior_discard_required=prior_discard_required,
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
        hand_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        player_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        target_no_call: bool = False,
        call_area_constraints: Optional[List[str]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        analysis_batch_size: Optional[int] = None,
        exclude_south4: bool = False,
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[Union[str, List[str]]] = None,
        prior_discard_required: Optional[str] = None,
        max_workers: Optional[int] = None,
        gc_interval_batches: Optional[int] = None,
        independence_filter: bool = False,
        hypothetical_furiten_tiles: Optional[str] = None,
        instant_normalize_oya_ron_to_ko: bool = False,
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

        def _target_str_for_variant_grid(tiles: List[str], is_combo: bool) -> str:
            return "".join(tiles) if is_combo else tiles[0]

        use_tenpai = (analysis_target == "tenpai")
        use_related_tile = (analysis_target == "related_tile")
        # 是否为「即时铳率」分析模式；用于 _should_run_memory_maintenance 区分内存维护策略（即时铳率下不必每批都做 GC）
        use_deal_in_instant = (analysis_target == "deal_in_instant")
        eff_grid_independence = bool(independence_filter) and analysis_target == "target_count"
        keys = [0, 1] if (use_tenpai or use_related_tile or use_deal_in_instant) else [0, 1, 2, 3]
        merge_keys_f = [k for k in merge_keys if k in keys] or keys[:2]
        hypothetical_furiten_list: List[str] = []
        if use_deal_in_instant and hypothetical_furiten_tiles and str(hypothetical_furiten_tiles).strip():
            hypothetical_furiten_list = [
                x.strip()
                for x in hypothetical_furiten_tiles.replace(",", " ").split()
                if x.strip()
            ]

        # 与 MatchValidator 一致：归一化后非空才跑前段禁打分支（避免 list/str 判真不一致）
        _prior_discard_exclusion_effective = normalize_prior_discard_exclusion_list(
            prior_discard_exclusion
        )

        # 棰勬瀯寤烘瘡鏍肩殑变体与元数据
        grid_meta = []  # [(tr_idx, pat_idx, turn_range, item_variants, item_multi_targets, is_combo), ...]
        consumed_search_list = []
        for pat_idx, (pattern, target) in enumerate(patterns):
            multi_t = parse_multi_targets(target)
            # 与主分析即时铳率相同：禁止 combo 目标与「.」通配（与 GUI 校验一致，避免 worker 内才失败）
            if use_deal_in_instant:
                if multi_t[0][1]:
                    raise ValueError(
                        "即时铳率分析不支持 combo 目标牌（如 4s-5s），请使用逗号分隔的多目标（如 4s,5s）"
                    )
                if "." in target:
                    raise ValueError(
                        "即时铳率分析不支持目标通配符（如 5.p），请使用 5p 或 0p 明确指定"
                    )
            first_t = multi_t[0]
            # 与 analyze_discard_pattern 一致：多目标须把全部目标拼入 generate_equivalent_variants，
            # 否则变体 target 只有第一项，即时铳率会报 len(m_targets)!=len(mt_item)
            variant_target = (
                ",".join(t[0][0] for t in multi_t)
                if len(multi_t) > 1
                else _target_str_for_variant_grid(first_t[0], first_t[1])
            )
            vars_p = generate_equivalent_variants(
                pattern,
                variant_target,
                visible_constraints,
                prior_discard_exclusion,
                call_area_constraints,
                prior_discard_required,
                hand_visible_constraints,
                player_visible_constraints,
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
                if use_deal_in_instant or use_tenpai or use_related_tile or is_combo:
                    grid_dist[k] = {0: 0, 1: 0}
                else:
                    grid_dist[k] = {0: 0, 1: 0, 2: 0, 3: 0}
        # 即时铳率矩阵专用累加器：与 worker 返回的 _instant_detail 合并（按格、按目标牌 hits/points）
        grid_instant_accum: Optional[Dict] = {} if use_deal_in_instant else None

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
            "target_no_call": target_no_call,
            "call_area_constraints": call_area_constraints,
            "hand_visible_constraints": hand_visible_constraints,
            "player_visible_constraints": player_visible_constraints,
            "call_area_constraint_sets": grid_call_area_sets,
            "consumed_search_list": consumed_search_list,
            "exclude_south4": exclude_south4,
            "exclude_south3": exclude_south3,
            "riichi_any": riichi_any,
            "use_tenpai": use_tenpai,
            "use_related_tile": use_related_tile,
            "use_deal_in_instant": use_deal_in_instant,
            "hypothetical_furiten_list": hypothetical_furiten_list,
            "instant_normalize_oya_ron_to_ko": (
                bool(instant_normalize_oya_ron_to_ko) if use_deal_in_instant else False
            ),
            "prior_discard_exclusion": prior_discard_exclusion,
            "prior_discard_required": prior_discard_required,
            "independence_filter": eff_grid_independence,
        }

        conn = _connect_db_memory_efficient(self.db_path)
        _ensure_log_json_column(conn)
        processed = 0
        maintenance_batch_index = 0

        # 后台预取，掩盖数据库 I/O 延迟（与主分析一致）
        fetcher = BackgroundLogFetcher(self.db_path, batch_size, last_id=None)
        # 单进程池贯穿全程：避免旧实现「每 SQL 批 with 新建池」造成的批间进程启停、CPU 空窗
        grid_pool = _make_analysis_process_pool(workers) if use_parallel else None

        try:
            if use_parallel and grid_pool is not None:
                def _on_grid_sql_batch_done() -> None:
                    nonlocal maintenance_batch_index, conn
                    maintenance_batch_index += 1
                    if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches, use_deal_in_instant):
                        _maybe_analysis_gc()
                        try:
                            conn.execute("PRAGMA shrink_memory")
                        except Exception:
                            pass
                        conn.close()
                        conn = _connect_db_memory_efficient(self.db_path)
                        _ensure_log_json_column(conn)
                        if ENABLE_TRIM_WITH_PERIODIC_MAINTENANCE:
                            _trim_process_memory()

                _g_max_if = max(2, workers * PARALLEL_IN_FLIGHT_FACTOR)
                _grid_row_pref = _RowTaskPrefetcher(
                    fetcher,
                    grid_params,
                    on_batch_done=_on_grid_sql_batch_done,
                    should_cancel=lambda: bool(should_cancel and should_cancel()),
                    queue_maxsize=max(128, _g_max_if * 8),
                )
                _grid_bounded = _iter_pool_results_bounded(
                    grid_pool,
                    _process_one_log_grid,
                    _grid_row_pref,
                    max_in_flight=_g_max_if,
                )
                try:
                    for per_log_result in _grid_bounded:
                        if should_cancel and should_cancel():
                            logger.info("grid analysis cancelled")
                            fetcher.stop()
                            conn.close()
                            _maybe_analysis_gc()
                            _trim_process_memory()
                            return {"table": {}, "total_logs_analyzed": processed, "elapsed_seconds": round(time.perf_counter() - t0, 1)}
                        for (tr_idx, pat_idx), delta in per_log_result.items():
                            k = (tr_idx, pat_idx)
                            for count, n in delta.items():
                                if count == "_instant_detail":
                                    continue
                                grid_dist[k][count] = grid_dist[k].get(count, 0) + n
                            if grid_instant_accum is not None:
                                idet = delta.get("_instant_detail")
                                if isinstance(idet, dict):
                                    acc = grid_instant_accum.setdefault(
                                        k, {"total": 0, "targets": {}}
                                    )
                                    _merge_instant_grid_detail(acc, idet)
                        processed += 1
                        if progress_callback and (processed <= 10 or processed % 10 == 0):
                            progress_callback(processed, total_logs)
                        if sample_limit and processed >= sample_limit:
                            break
                finally:
                    _grid_bounded.close()
                    _grid_row_pref.close()
                    fetcher.stop()
            else:
                while True:
                    if should_cancel and should_cancel():
                        logger.info("grid analysis cancelled")
                        break
                    logs = fetcher.next_batch()
                    if not logs:
                        break

                    call_area_constraint_sets = grid_call_area_sets
                    for log_id, log_content in logs:
                        if should_cancel and should_cancel():
                            fetcher.stop()
                            conn.close()
                            _maybe_analysis_gc()
                            _trim_process_memory()
                            return {"table": {}, "total_logs_analyzed": processed, "elapsed_seconds": round(time.perf_counter() - t0, 1)}
                        try:
                            if use_deal_in_instant:
                                per_log_result = _process_one_log_grid((log_id, log_content, grid_params))
                                for (tr_idx, pat_idx), delta in per_log_result.items():
                                    k = (tr_idx, pat_idx)
                                    for count, n in delta.items():
                                        if count == "_instant_detail":
                                            continue
                                        grid_dist[k][count] = grid_dist[k].get(count, 0) + n
                                    if grid_instant_accum is not None:
                                        idet = delta.get("_instant_detail")
                                        if isinstance(idet, dict):
                                            acc = grid_instant_accum.setdefault(
                                                k, {"total": 0, "targets": {}}
                                            )
                                            _merge_instant_grid_detail(acc, idet)
                            else:
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
                                    if call_constraint or target_no_call or call_area_constraints or call_area_constraint_sets:
                                        if not round_could_satisfy_call_constraints(
                                            round_players, call_constraint,
                                            None if call_area_constraint_sets else call_area_constraints,
                                            oya, call_area_constraint_sets=call_area_constraint_sets,
                                            target_no_call=target_no_call,
                                        ):
                                            continue
                                    if riichi_any:
                                        if not any(
                                            any(getattr(d, "is_riichi_declaration", False) for d in p.discards)
                                            for p in round_players
                                        ):
                                            continue
    
                                    for player_state in round_players:
                                        if not target_no_call and call_constraint == "no_call" and len(getattr(player_state, "calls", []) or []) > 0:
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
                                            "bakaze": MjlogParser.resolve_bakaze(
                                                player_state.round_num, getattr(player_state, "bakaze", None)
                                            ),
                                            "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                                            "calls": getattr(player_state, "calls", []),
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
                                                # 与正常模式一致：使用从开局到当前舍牌的完整序列，否则会漏掉「模式前半在巡目外」的匹配
                                                orig_i = in_range_for_cell[-1][0]
                                                full_discards_up_to_now = discards_precomputed[: orig_i + 1]
                                                riichi_flags_for_cell = discard_riichi_flags[: orig_i + 1]
                                                visible_with_own = _visible_tiles_with_own_discards(player_state.visible_tiles, full_discards_up_to_now)
                                                hand_after_by_index = [
                                                    list(player_state.hand_tiles_history[i]) if i < len(player_state.hand_tiles_history) else list(player_state.hand_tiles)
                                                    for i in range(len(full_discards_up_to_now))
                                                ]
                                                honor_ctx = {
                                                    **honor_ctx_base,
                                                    "visible_tiles": visible_with_own,
                                                    "current_discard_turn": discard_turn,
                                                    "discard_riichi_flags": riichi_flags_for_cell,
                                                    "hand_after_by_index": hand_after_by_index,
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
                                                if _prior_discard_exclusion_effective:
                                                    forbidden = get_prior_discard_exclusion_forbidden_bases(matched_variant)
                                                    if forbidden:
                                                        start_idx = matched_variant.get("matched_start_index")
                                                        if start_idx is not None:
                                                            prior_tiles = full_discards_up_to_now[:start_idx]  # [(tile_str, is_tsumogiri), ...]
                                                            # string_to_tile 返回 base(0-36)，forbidden 亦为 base 集合，直接用 in，勿用 // 4
                                                            if any(MjlogParser.string_to_tile(t[0]) in forbidden for t in prior_tiles):
                                                                continue
                                                        elif turn_range:
                                                            first_turn_in_range = in_range_for_cell[0][1].turn
                                                            prior_discards = [d for d in player_state.discards if d.turn < first_turn_in_range]
                                                            # d.tile 为 tile 码(0-147)，需 // 4 得 base 再与 forbidden 比较
                                                            if any((d.tile // 4) in forbidden for d in prior_discards):
                                                                continue
                                                if matched_variant.get("prior_discard_required"):
                                                    prior_req_variants = prior_required_pattern_to_variants(matched_variant["prior_discard_required"])
                                                    if prior_req_variants:
                                                        start_idx = matched_variant.get("matched_start_index")
                                                        if start_idx is not None and start_idx > 0:
                                                            prior_tiles = full_discards_up_to_now[:start_idx]
                                                        elif turn_range:
                                                            first_turn_in_range = in_range_for_cell[0][1].turn
                                                            prior_tiles = [(MjlogParser.tile_to_string(d.tile), d.is_tsumogiri) for d in player_state.discards if d.turn < first_turn_in_range]
                                                        else:
                                                            prior_tiles = []
                                                        if not prior_tiles:
                                                            continue
                                                        if not match_discard_pattern_contained(prior_tiles, prior_req_variants, honor_ctx):
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
                                                if (call_constraint and call_constraint != "any") or target_no_call:
                                                    if call_constraint == "has_call" and not discard.call_happened:
                                                        continue
                                                    if call_constraint == "no_call" and discard.call_happened:
                                                        continue
                                                    if target_no_call:
                                                        if any(getattr(c, "from_discard_turn", 1) <= discard.turn for c in getattr(player_state, "calls", [])):
                                                            continue
                                                _ca = matched_variant.get("call_area_constraints") or call_area_constraints
                                                if _ca:
                                                    if not player_satisfies_call_area_constraints(
                                                        player_state, round_players, player_state.oya, _ca,
                                                        current_discard_turn=discard.turn,
                                                    ):
                                                        continue
                                                mapped_target = matched_variant["target"]
                                                target_equiv = None
                                                if not use_tenpai and not use_related_tile:
                                                    target_equiv = set()
                                                    if len(multi_t) == 1:
                                                        if is_combo:
                                                            for t in (mapped_target if isinstance(mapped_target, list) else [mapped_target]):
                                                                target_equiv |= MjlogParser.get_bases_for_target_tile_str(t)
                                                        else:
                                                            target_equiv = MjlogParser.get_bases_for_target_tile_str(mapped_target)
                                                    else:
                                                        target_equiv = MjlogParser.get_bases_for_target_tile_str(
                                                            mapped_target if isinstance(mapped_target, str) else mapped_target[0]
                                                        )
                                                        for tiles, ic in multi_t[1:]:
                                                            for t in tiles:
                                                                target_equiv |= MjlogParser.get_bases_for_target_tile_str(t)
                                                    if discard.tile // 4 in target_equiv:
                                                        continue
                                                elif use_related_tile:
                                                    target_equiv = MjlogParser.get_bases_for_target_tile_str(
                                                        mapped_target if isinstance(mapped_target, str) else mapped_target[0]
                                                    )
    
                                                if orig_i < len(player_state.hand_tiles_history):
                                                    hand_at_turn = list(player_state.hand_tiles_history[orig_i])
                                                else:
                                                    hand_at_turn = list(player_state.hand_tiles)
                                                if use_tenpai:
                                                    target_count = 1 if is_tenpai(hand_at_turn) else 0
                                                elif use_related_tile:
                                                    target_discard_idx = None
                                                    target_discard = None
                                                    for k in range(len(in_range_for_cell) - 1, -1, -1):
                                                        idx, dd = in_range_for_cell[k]
                                                        if dd.tile // 4 in target_equiv:
                                                            target_discard_idx = idx
                                                            target_discard = dd
                                                            break
                                                    if target_discard_idx is not None and target_discard is not None:
                                                        if target_discard_idx < len(player_state.hand_tiles_history):
                                                            hand_at_target = list(player_state.hand_tiles_history[target_discard_idx])
                                                        else:
                                                            hand_at_target = list(player_state.hand_tiles)
                                                        num, suit = base_to_discard_num_and_suit(target_discard.tile // 4)
                                                        if num is not None and suit is not None:
                                                            counts = hand_to_suit_counts(hand_at_target, suit)
                                                            target_count = 1 if is_related_discard(num, counts) else 0
                                                        else:
                                                            target_count = 0
                                                    else:
                                                        target_count = 0
                                                elif len(multi_t) > 1:
                                                    tk = _target_key(multi_t[0][0], multi_t[0][1])
                                                    mt_item = multi_t
                                                    if multi_t[0][1]:
                                                        hand_bases = [t // 4 for t in hand_at_turn]
                                                        tc = 1 if all(
                                                            any(hand_bases.count(b) >= 1 for b in MjlogParser.get_bases_for_target_tile_str(ts))
                                                            for ts in multi_t[0][0]
                                                        ) else 0
                                                        target_count = _apply_combo_independence_filter(
                                                            hand_at_turn, multi_t[0][0], tc, eff_grid_independence
                                                        )
                                                    else:
                                                        equiv = MjlogParser.get_bases_for_target_tile_str(multi_t[0][0][0])
                                                        target_count = min(sum(1 for tile in hand_at_turn if tile // 4 in equiv), 3)
                                                elif is_combo:
                                                    hand_bases = [t // 4 for t in hand_at_turn]
                                                    mts = mapped_target if isinstance(mapped_target, list) else [mapped_target]
                                                    tc = 1 if all(
                                                        any(hand_bases.count(b) >= 1 for b in MjlogParser.get_bases_for_target_tile_str(ts))
                                                        for ts in mts
                                                    ) else 0
                                                    target_count = _apply_combo_independence_filter(
                                                        hand_at_turn, mapped_target, tc, eff_grid_independence
                                                    )
                                                else:
                                                    equiv = MjlogParser.get_bases_for_target_tile_str(mapped_target)
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

                    del logs
                    maintenance_batch_index += 1
                    if _should_run_memory_maintenance(maintenance_batch_index, gc_interval_batches, use_deal_in_instant):
                        _maybe_analysis_gc()
                        try:
                            conn.execute("PRAGMA shrink_memory")
                        except Exception:
                            pass
                        conn.close()
                        conn = _connect_db_memory_efficient(self.db_path)
                        _ensure_log_json_column(conn)
                        if ENABLE_TRIM_WITH_PERIODIC_MAINTENANCE:
                            _trim_process_memory()
                    if sample_limit and processed >= sample_limit:
                        break

        finally:
            if grid_pool is not None:
                grid_pool.shutdown(wait=True)
            fetcher.stop()
            conn.close()
            _maybe_analysis_gc()
            _trim_process_memory()

        # 按格计算合并概率、完整分布与样本数
        cell_combo = {(tr_idx, pat_idx): is_combo for tr_idx, pat_idx, _, _, _, is_combo in grid_meta}
        table = {}
        table_dist = {}  # 每格完整分布 {(tr_idx, pat_idx): {0: pct, 1: pct, ...}}
        table_counts = {}  # 每格样本数 {(tr_idx, pat_idx): n}
        for (tr_idx, pat_idx), dist in grid_dist.items():
            total_m = sum(dist.values())
            table_counts[(tr_idx, pat_idx)] = total_m
            is_combo_cell = cell_combo.get((tr_idx, pat_idx), False)
            k_list = (
                [0, 1]
                if (use_tenpai or use_related_tile or is_combo_cell or use_deal_in_instant)
                else [0, 1, 2, 3]
            )
            if total_m == 0:
                prob = 0.0
                table_dist[(tr_idx, pat_idx)] = {k: 0.0 for k in k_list}
            else:
                prob = sum(dist.get(k, 0) / total_m * 100 for k in merge_keys_f if k in k_list)
                table_dist[(tr_idx, pat_idx)] = {
                    k: round(dist.get(k, 0) / total_m * 100, 2) for k in k_list
                }
            table[(tr_idx, pat_idx)] = round(prob, 2)

        logger.info(f"批量分析完成: 处理 {processed:,} 场对局")
        out_grid: Dict = {
            "table": table,
            "table_dist": table_dist,
            "table_counts": table_counts,
            "total_logs_analyzed": processed,
            "elapsed_seconds": round(time.perf_counter() - t0, 1),
        }
        # 即时铳率矩阵：附加按目标牌的率/点/铳度，供 GUI 分目标展示与勾选合并（arithmetic mean）
        if use_deal_in_instant and grid_instant_accum is not None:
            instant_matrix_meta: Dict[int, List[str]] = {}
            for tr_idx, pat_idx, _, _, multi_t, _ in grid_meta:
                if pat_idx not in instant_matrix_meta:
                    instant_matrix_meta[pat_idx] = _instant_grid_target_keys(multi_t)
            instant_matrix_cells: Dict = {}
            for k in grid_dist.keys():
                acc = grid_instant_accum.get(k, {"total": 0, "targets": {}})
                total_cell = int(acc.get("total", 0))
                by_target: Dict[str, Dict] = {}
                for tk, st in acc.get("targets", {}).items():
                    hits = int(st.get("hits", 0))
                    points = int(st.get("points", 0))
                    rate = (hits / total_cell) if total_cell > 0 else 0.0
                    p_avg = (points / hits) if hits > 0 else 0.0
                    by_target[tk] = {
                        "hits": hits,
                        "rate": rate,
                        "point_avg": p_avg,
                        "intensity": rate * p_avg,
                    }
                instant_matrix_cells[k] = {"total": total_cell, "by_target": by_target}
            out_grid["instant_matrix_cells"] = instant_matrix_cells
            out_grid["instant_matrix_meta"] = instant_matrix_meta
        return out_grid

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
        hand_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        player_visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,
        target_no_call: bool = False,
        turn_range: Optional[Tuple[int, int]] = None,
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
        sample_pool: Optional[List[Dict]] = None,  # 主统计时预收集的样本池，有则无需二次遍历
        analysis_batch_size: Optional[int] = None,
        exclude_south4: bool = False,
        exclude_south3: bool = False,
        prior_discard_exclusion: Optional[Union[str, List[str]]] = None,
        prior_discard_required: Optional[str] = None,
        call_area_constraints: Optional[List[str]] = None,
        gc_interval_batches: Optional[int] = None,
        outcome_filter: Optional[str] = None,
        deal_in_filter: Optional[str] = None,  # "hit"|"miss"|"furiten" 即时铳率样本筛选
        analysis_target: Optional[str] = None,
        pattern_index_filter: Optional[int] = None,  # 多模式时只保留 matched_pattern_idx == 此值的样本
        independence_filter: bool = False,
    ) -> List[Dict]:
        """
        收集验证样本，用于人工复盘核验。
        返回含 log_id、oya、局显示等完整信息的样本列表。
        若传入 sample_pool（主统计时预收集），则直接从池采样，无需二次分析。
        多模式时若传入 pattern_index_filter，仅保留 matched_pattern_idx 等于该值的样本。
        """
        requested_batch_size = (
            analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        )
        use_deal_in_instant = (analysis_target == "deal_in_instant")
        batch_size = _clamp_analysis_batch_size(requested_batch_size, workers=1, use_parallel=False)
        eff_collect_independence = bool(independence_filter) and (analysis_target or "target_count") == "target_count"

        if sample_pool is not None:
            # 从预收集的样本池中筛选并取前 N 个，无需遍历牌谱（主分析已排除南四局则无需再过滤）
            candidates = sample_pool
            if pattern_index_filter is not None:
                candidates = [s for s in candidates if s.get("matched_pattern_idx") == pattern_index_filter]
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
                if analysis_target == "yaku_hai_hand":
                    def _yaku_sample_match(s: Dict) -> bool:
                        yh = s.get("yaku_hai") or {}
                        pk = int(yh.get("pair_kinds", 0))
                        if target_count_filter == YAKU_HAI_PAIR_FILTER_GE3:
                            return pk >= 3
                        return pk == target_count_filter

                    candidates = [s for s in candidates if _yaku_sample_match(s)]
                elif target_tile_filter and any("target_counts" in s for s in candidates):
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
            query_pattern,
            target_tile,
            visible_constraints,
            prior_discard_exclusion,
            call_area_constraints,
            prior_discard_required,
            hand_visible_constraints,
            player_visible_constraints,
        )
        consumed_search = get_consumed_search_patterns(query_pattern)
        riichi_search = pattern_has_riichi(query_pattern)
        samples = []
        # 离线样本扫描与 grid 内联校验共用：归一化前段禁打列表
        _prior_discard_exclusion_effective = normalize_prior_discard_exclusion_list(
            prior_discard_exclusion
        )

        # 初始化验证器，用于手牌可见枚数等约束校验
        validator = MatchValidator({
            "hand_visible_constraints": hand_visible_constraints,
            "target_no_call": target_no_call,
        })

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
                    _maybe_analysis_gc()
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
                                "bakaze": MjlogParser.resolve_bakaze(
                                    player_state.round_num, getattr(player_state, "bakaze", None)
                                ),
                                "kyokuze_list": MjlogParser.get_kyokuze_list(player_state.player_id, player_state.oya, player_state.round_num),
                                "calls": getattr(player_state, "calls", []),
                                "dora_indicators": getattr(round_players[0], "dora_indicators", None) or getattr(player_state, "dora_indicators", []),
                            }

                            discarded_bases = set()
                            for j, (orig_i, discard) in enumerate(in_range):
                                discarded_bases.add(discard.tile // 4)
                                full_discards_up_to_now = all_discards_precomputed[:orig_i + 1]
                                current_riichi_flags = all_riichi_flags[:orig_i + 1]
                                hand_discard_strings = _format_actual_pattern(full_discards_up_to_now, current_riichi_flags)
                                visible_with_own = _visible_tiles_with_own_discards(player_state.visible_tiles, full_discards_up_to_now)
                                hand_after_by_index = [
                                    list(player_state.hand_tiles_history[i]) if i < len(player_state.hand_tiles_history) else list(player_state.hand_tiles)
                                    for i in range(len(full_discards_up_to_now))
                                ]
                                honor_ctx = {
                                    **honor_ctx_base,
                                    "visible_tiles": visible_with_own,
                                    "current_discard_turn": discard.turn,
                                    "discard_riichi_flags": current_riichi_flags,
                                    "hand_after_by_index": hand_after_by_index,
                                }
                                matched_variant = match_discard_to_variant(full_discards_up_to_now, variants, honor_ctx)
                                if not matched_variant:
                                    continue

                                mapped_target = matched_variant["target"]
                                mapped_target_str = mapped_target if isinstance(mapped_target, str) else (mapped_target[0] if mapped_target else None)
                                sample_is_combo = matched_variant.get("is_combo", False)
                                # 即时铳率：允许「达成巡」打出目标牌；但本局此前不得曾打过任一目标准许牌（与 MatchValidator 一致）
                                if use_deal_in_instant:
                                    _te_inst = set()
                                    if isinstance(mapped_target, list):
                                        for _t in mapped_target:
                                            if _t:
                                                _te_inst |= MjlogParser.get_bases_for_target_tile_str(_t)
                                    else:
                                        _te_inst = MjlogParser.get_bases_for_target_tile_str(mapped_target)
                                    if _te_inst:
                                        _bad_prior_inst = False
                                        for _pi in range(orig_i):
                                            if _pi < len(player_state.discards):
                                                if (player_state.discards[_pi].tile // 4) in _te_inst:
                                                    _bad_prior_inst = True
                                                    break
                                        if _bad_prior_inst:
                                            continue
                                else:
                                    # 目标牌存量等：当前这一打不能就是目标牌（或 combo 全量）
                                    if sample_is_combo:
                                        target_equiv = set()
                                        for t in mapped_target:
                                            target_equiv |= MjlogParser.get_bases_for_target_tile_str(t)
                                        if discard.tile // 4 in target_equiv:
                                            continue
                                    else:
                                        if discard.tile // 4 in MjlogParser.get_bases_for_target_tile_str(mapped_target):
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

                                if _prior_discard_exclusion_effective:
                                    forbidden = get_prior_discard_exclusion_forbidden_bases(matched_variant)
                                    if forbidden:
                                        start_idx = matched_variant.get("matched_start_index")
                                        if start_idx is not None:
                                            prior_tiles = full_discards_up_to_now[:start_idx]  # [(tile_str, is_tsumogiri), ...]
                                            # string_to_tile 返回 base(0-36)，forbidden 亦为 base 集合，直接用 in，勿用 // 4
                                            if any(MjlogParser.string_to_tile(t[0]) in forbidden for t in prior_tiles):
                                                continue
                                        elif turn_range:
                                            prior_discards = [d for d in player_state.discards if d.turn < in_range[0][1].turn]
                                            # d.tile 为 tile 码(0-147)，需 // 4 得 base 再与 forbidden 比较
                                            if any((d.tile // 4) in forbidden for d in prior_discards):
                                                continue

                                if matched_variant.get("prior_discard_required"):
                                    prior_req_variants = prior_required_pattern_to_variants(matched_variant["prior_discard_required"])
                                    if prior_req_variants:
                                        start_idx = matched_variant.get("matched_start_index")
                                        if start_idx is not None and start_idx > 0:
                                            prior_tiles = full_discards_up_to_now[:start_idx]
                                        elif turn_range:
                                            prior_tiles = [(MjlogParser.tile_to_string(d.tile), d.is_tsumogiri) for d in player_state.discards if d.turn < in_range[0][1].turn]
                                        else:
                                            prior_tiles = []
                                        if not prior_tiles:
                                            continue
                                        if not match_discard_pattern_contained(prior_tiles, prior_req_variants, honor_ctx):
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
                                if (call_constraint and call_constraint != "any") or target_no_call:
                                    if call_constraint == "has_call" and not discard.call_happened:
                                        continue
                                    if call_constraint == "no_call" and discard.call_happened:
                                        continue
                                    if target_no_call:
                                        if any(getattr(c, "from_discard_turn", 1) <= discard.turn for c in getattr(player_state, "calls", [])):
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

                                hvc = matched_variant.get("hand_visible_constraints")
                                if hvc:
                                    if not validator._check_hand_visible_constraints(discard, matched_variant, player_state, round_players):
                                        continue

                                pvc = matched_variant.get("player_visible_constraints")
                                if pvc:
                                    if not validator._check_player_visible_constraints(
                                        discard, matched_variant, player_state, round_players
                                    ):
                                        continue

                                # 排除：若未打出牌就是目标牌，不计入（与主统计逻辑一致）
                                mapped_target = matched_variant["target"]
                                sample_is_combo = matched_variant.get("is_combo", False)
                                if sample_is_combo:
                                    target_equiv = set()
                                    for t in mapped_target:
                                        target_equiv |= MjlogParser.get_bases_for_target_tile_str(t)
                                    if discard.tile // 4 in target_equiv:
                                        continue
                                else:
                                    if discard.tile // 4 in MjlogParser.get_bases_for_target_tile_str(mapped_target):
                                        continue

                                hh = (
                                    player_state.hand_tiles_history[orig_i]
                                    if orig_i < len(player_state.hand_tiles_history)
                                    else player_state.hand_tiles
                                )
                                hand_at_turn = list(hh)  # 必须为 list 以保留同种牌枚数
                                if sample_is_combo:
                                    hand_bases = [t // 4 for t in hand_at_turn]
                                    tc = 1 if all(
                                        any(hand_bases.count(b) >= 1 for b in MjlogParser.get_bases_for_target_tile_str(ts))
                                        for ts in mapped_target
                                    ) else 0
                                    target_count = _apply_combo_independence_filter(
                                        hand_at_turn, mapped_target, tc, eff_collect_independence
                                    )
                                else:
                                    equiv = MjlogParser.get_bases_for_target_tile_str(mapped_target)
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
                                    "dora_readable": dora_readable,
                                    "visible_target": visible_target,
                                    "call_area": call_area,
                                    "target_count": target_count,
                                    "is_combo": sample_is_combo,
                                    "outcome_won": bool(rw and player_state.player_id in rw),
                                    "outcome_deal_in": bool(rdi is not None and rdi == player_state.player_id),
                                }
                                if use_deal_in_instant:
                                    sp_entry.update(_ipc_copy_instant_eval(instant_eval))
                                    if instant_eval_multi:
                                        sp_entry["instant_eval_multi"] = _ipc_copy_instant_eval_multi(
                                            instant_eval_multi
                                        )
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
                _maybe_analysis_gc()
                try:
                    conn.execute("PRAGMA shrink_memory")
                except Exception:
                    pass
                conn.close()
                conn = _connect_db_memory_efficient(self.db_path)
                cur = conn.cursor()
                _ensure_log_json_column(conn)
                if ENABLE_TRIM_WITH_PERIODIC_MAINTENANCE:
                    _trim_process_memory()
            if sample_limit and processed >= sample_limit:
                break
            if len(samples) >= sample_count:
                break

        conn.close()
        _maybe_analysis_gc()
        _trim_process_memory()
        return samples


def _visible_tiles_with_own_discards(visible_tiles, full_discards_up_to_now):
    """合并分析对象的舍牌到 visible_tiles，供 ap 安牌判定使用。tenhou6 的 visible_tiles 仅含其他玩家可见，不含本家舍牌。"""
    merged = Counter(dict(visible_tiles or {}))
    for tile_str, _ in (full_discards_up_to_now or []):
        try:
            base = MjlogParser.string_to_tile(tile_str)
            tile_code = base * 4
            merged[tile_code] = merged.get(tile_code, 0) + 1
        except (ValueError, TypeError):
            pass
    return merged


def _visible_count(visible_tiles: dict, tile_str: str) -> int:
    """统计某牌在可见牌中的枚数；5.p 含 5p 与 0p；否则 0m/0p/0s 与 5m/5p/5s 仍视为不同牌"""
    equiv = MjlogParser.get_bases_for_target_tile_str(tile_str)
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
    单字符字牌（东/南/西/北/白/发/中）走 else 分支，解析为 (s, False)。
    """
    result = []
    riichi_flags = []
    for s in actual_pattern:
        if not s:
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
    hand_visible_constraints: Optional[Dict] = None,
    player_visible_constraints: Optional[Dict] = None,
    prior_discard_exclusion: Optional[Union[str, List[str]]] = None,
    call_area_constraints: Optional[List[str]] = None,
    prior_discard_required: Optional[str] = None,
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
            query_pattern,
            variant_target,
            visible_constraints,
            prior_discard_exclusion,
            call_area_constraints,
            prior_discard_required,
            hand_visible_constraints,
            player_visible_constraints,
        )
        
        # 补充上下文，支持 r (立直) 及 zf/kf (自风/客风) 占位符校验
        player_id = sample.get("player_id", 0)
        oya = sample.get("oya", 0)
        round_num = sample.get("round_num", 0)
        
        ctx = {
            "jikaze": MjlogParser.get_jikaze(player_id, oya, round_num),
            "bakaze": MjlogParser.resolve_bakaze(round_num, sample.get("bakaze")),
            "kyokuze_list": MjlogParser.get_kyokuze_list(player_id, oya, round_num),
            "discard_riichi_flags": riichi_flags,
            "current_discard_turn": sample.get("turn"),
            "visible_tiles": sample.get("visible_tiles"),
            # 样本池已省略 dora_indicators 列表以瘦身 IPC；离线校验用空列表（占位符匹配不依赖指示牌）
            "dora_indicators": sample.get("dora_indicators") or [],
        }
        
        matched = match_discard_to_variant(full_discards, variants, ctx)
        if matched:
            return (True, None)
        return (False, "重新匹配失败：actual_pattern 无法匹配任一等价变体")
    except Exception as e:
        logger.exception("样本一致性校验异常")
        return (False, str(e))


def _target_counts_display_set(tc: dict) -> set:
    """多目标时，各目标牌及其等价牌的显示集合（键可能为 combo 合并串如 4p5.p，不能直接 string_to_tile）"""
    out = set()
    for k in (tc or {}):
        if not k:
            continue
        try:
            bases = MjlogParser.get_bases_for_target_tile_str(k)
            for b in bases:
                for eq in MjlogParser.get_count_equivalent_bases(b):
                    out.add(MjlogParser.tile_to_string(eq * 4))
            continue
        except ValueError:
            pass
        # 多目标下 _target_key 对搭子为 "".join(sorted(tiles))，如 "4p5.p"
        tokens, _ = parse_target_tiles(k)
        for tok in tokens:
            try:
                tb = MjlogParser.get_bases_for_target_tile_str(tok)
            except ValueError:
                continue
            for b in tb:
                for eq in MjlogParser.get_count_equivalent_bases(b):
                    out.add(MjlogParser.tile_to_string(eq * 4))
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
    use_related_tile = (analysis_target == "related_tile")
    use_yaku_hai = (analysis_target == "yaku_hai_hand")
    is_combo = False if (use_tenpai or use_related_tile) else (samples[0].get("is_combo", False) if samples else False)
    has_multi = bool(samples and samples[0].get("target_counts"))
    target_label = (
        "即时铳率" if use_instant
        else "和铳率" if use_outcome
        else "关联牌" if use_related_tile
        else "役牌手持" if use_yaku_hai
        else (target_tile if use_tenpai else (
            f"{target_tile} (combo)" if is_combo else (f"{target_tile} (multi-target)" if has_multi else target_tile)
        ))
    )
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
        elif use_yaku_hai:
            yh = s.get("yaku_hai") or {}
            mt_set = set(yh.get("per_tile") or {})
        elif has_multi and s.get("target_counts"):
            mt_set = _target_counts_display_set(s["target_counts"])
        else:
            mt_set = _target_display_set(mt) if not (use_tenpai or use_related_tile) and mt else set()
        hand_parts = []
        hand_tiles = s.get("hand_tiles") or []
        try:
            hand_iter = sorted(hand_tiles, key=lambda x: (x // 4, x))
        except TypeError:
            hand_iter = list(hand_tiles) if isinstance(hand_tiles, (list, tuple)) else []
        for t in hand_iter:
            ts = MjlogParser.tile_to_string(t)
            hand_parts.append(f"[{ts}]" if (ts in mt_set) else ts)
        hand_str = " ".join(hand_parts)
        round_display = MjlogParser.format_round_display(s["round_num"], s["honba"])
        wind = MjlogParser.get_player_wind(s["player_id"], s["oya"])
        if use_outcome:
            target_line = f"  结局:        {_outcome_label(s)}"
        elif use_yaku_hai:
            yh = s.get("yaku_hai") or {}
            pt = yh.get("per_tile") or {}
            sy = yh.get("seat_yaku_labels") or []
            _pu_l = ("零对", "一对", "两对", "三对及以上")
            _pu_i = yh.get("pair_units")
            if _pu_i is None:
                _pu_i = yaku_pair_units_bucket(int(yh.get("pair_kinds", 0)))
            _pu_i = int(max(0, min(3, int(_pu_i))))
            target_line = (
                f"  本座役牌:    {','.join(sy)}\n"
                f"  役牌统计:    种类{yh.get('kinds')} 役牌对副:{_pu_l[_pu_i]} | 枚数 {pt}"
            )
        elif use_related_tile:
            target_line = f"  关联牌:      {'是' if s.get('target_count') else '否'}"
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
            f"  Discards:    {' '.join(s.get('actual_pattern') or [])}",
            f"  副露区:      {call_area_str}",
            target_line,
            f"  Hand({len(hand_tiles)}): {hand_str}",
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
            elif not use_instant and not use_yaku_hai:
                # 役牌手持使用占位目标牌，Visible 易误解为「北等舍牌算役牌」；役牌只计此时点手牌
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
    _maybe_analysis_gc()
    _trim_process_memory()
    return stats


