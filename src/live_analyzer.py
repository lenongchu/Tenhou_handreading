"""
实时查询引擎 - 按需分析模式
直接从 logs 表读取完整牌谱数据并实时解析分析

解析采用 tenhou6 格式（tenhou-paifu-to-json），支持副露等完整信息。
"""
import sqlite3
import gzip
import json
from typing import List, Dict, Optional, Tuple, Callable, Union
from collections import Counter
import logging

from .mjlog_parser import MjlogParser, GameState
from .tenpai_utils import is_tenpai
from .simple_normalizer import (
    generate_equivalent_variants,
    match_discard_to_variant,
    parse_target_tiles,
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

        item_variants = []
        for p, t in items:
            vars_p = generate_equivalent_variants(p, t, visible_constraints)
            _, combo = parse_target_tiles(t)
            item_variants.append((vars_p, t, combo))

        variants = item_variants[0][0]
        _, is_combo = parse_target_tiles(first_target)

        matched_states = []
        sample_pool: List[Dict] = []
        SAMPLE_POOL_CAP = 10000
        total_matches = 0
        pattern_matches = [0] * len(items)
        use_tenpai = (analysis_target == "tenpai")
        pattern_distributions = [
            ({0: 0, 1: 0} if (use_tenpai or iv[2]) else {0: 0, 1: 0, 2: 0, 3: 0})
            for iv in item_variants
        ]
        target_count_distribution = pattern_distributions[0]

        if should_cancel and should_cancel():
            return _empty_analysis_result(first_pattern, first_target)

        conn = sqlite3.connect(self.db_path, timeout=60)
        cur = conn.cursor()
        _ensure_log_json_column(conn)

        # 避免 COUNT(*) 在大库上阻塞数分钟；使用 total_logs_hint 或 sample_limit
        if total_logs_hint is not None and total_logs_hint > 0:
            total_logs = total_logs_hint
        else:
            total_logs = 0
        if sample_limit:
            total_logs = min(total_logs, sample_limit) if total_logs > 0 else sample_limit
        
        logger.info(f"开始分析 {total_logs:,} 场对局...")
        
        # 批量读取并分析（用 id 游标分页，避免 OFFSET 越大越慢）
        last_id = None  # None 表示第一页；之后用 WHERE id < last_id
        processed = 0
        
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
            
            # 解析并匹配
            for log_id, log_content in logs:
                if should_cancel and should_cancel():
                    conn.close()
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
                                if not use_tenpai:
                                    if item_combo:
                                        target_equiv = set()
                                        for t in mapped_target:
                                            target_equiv |= MjlogParser.get_count_equivalent_bases(MjlogParser.string_to_tile(t))
                                        if discard.tile // 4 in target_equiv:
                                            continue
                                    else:
                                        if MjlogParser.bases_equivalent_for_count(discard.tile // 4, MjlogParser.string_to_tile(mapped_target)):
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
                                
                                # mapped_target 已在上方排除逻辑中取得
                                if use_tenpai:
                                    target_count = 1 if is_tenpai(list(hand_at_turn)) else 0
                                elif item_combo:
                                    # 搭子：手牌是否包含所有目标牌（每种至少1张，0/5 视为不同）
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
                                pattern_distributions[matched_idx][target_count] += 1
                                
                                # 记录匹配状态（仅保留前 cap 条，避免内存持续增长）
                                if len(matched_states) < cap:
                                    matched_states.append({
                                        'round_num': player_state.round_num,
                                        'turn': discard.turn,
                                        'target_count': target_count,
                                        'actual_pattern': hand_discard_strings,
                                        'mapped_target': mapped_target,
                                        'hand_tiles': list(hand_at_turn),
                                        'visible_tiles': dict(player_state.visible_tiles)
                                    })
                                
                                # 主统计时顺带收集完整样本，供采样直接使用
                                if len(sample_pool) < SAMPLE_POOL_CAP:
                                    visible_tiles_dict = dict(player_state.visible_tiles)
                                    dora_readable = "".join(
                                        MjlogParser.tile_to_string(d)
                                        for d in round_players[0].dora_indicators[:5]
                                    ) if round_players[0].dora_indicators else "（无）"
                                    if item_combo and isinstance(mapped_target, list):
                                        visible_target = ", ".join(
                                            f"{t}:{_visible_count(visible_tiles_dict, t)}"
                                            for t in mapped_target
                                        )
                                    else:
                                        visible_target = str(_visible_count(visible_tiles_dict, mapped_target))
                                    sample_pool.append({
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
                                        "is_combo": item_combo,
                                        "matched_pattern_idx": matched_idx,
                                    })
                    
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
        
        conn.close()

        target_count_distribution = pattern_distributions[0]
        probability_distribution = {}
        keys = [0, 1] if (use_tenpai or is_combo) else [0, 1, 2, 3]
        for count in keys:
            prob = (target_count_distribution.get(count, 0) / max(1, pattern_matches[0]) * 100) if pattern_matches[0] > 0 else 0
            probability_distribution[count] = prob

        pattern_results = []
        for idx, (p, t) in enumerate(items):
            dist = pattern_distributions[idx]
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
            'pattern_results': pattern_results,
        }

        logger.info(f"分析完成: 匹配 {total_matches} 个状态")
        if len(items) > 1:
            for pr in pattern_results:
                logger.info(f"  {pr['pattern_str']}→{pr['target']}: {pr['matches']:,} 次")
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

    def collect_verification_samples(
        self,
        query_pattern: List[str],
        target_tile: str,
        sample_count: int = 10,
        target_count_filter: Optional[int] = None,  # None=全部, 0/1/2/3=只收该数量
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
    ) -> List[Dict]:
        """
        收集验证样本，用于人工复盘核对。
        返回含 log_id、oya、正确小局显示等完整信息的样本列表。
        若传入 sample_pool（主统计时预收集），则直接从中采样，无需二次分析。
        """
        batch_size = analysis_batch_size if analysis_batch_size is not None else ANALYSIS_BATCH_SIZE
        if sample_pool and len(sample_pool) > 0:
            # 从预收集的样本池中筛选并取前 N 个，无需遍历牌谱
            candidates = sample_pool
            if target_count_filter is not None:
                candidates = [s for s in sample_pool if s["target_count"] == target_count_filter]
            return candidates[:sample_count]

        variants = generate_equivalent_variants(
            query_pattern, target_tile, visible_constraints
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
                                samples.append({
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
                                })
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
    """目标牌描述：听牌模式=听牌/未听牌；搭子=有/无搭子；单张=应有X张在手牌"""
    if use_tenpai:
        return "听牌" if s["target_count"] else "未听牌"
    if s.get("is_combo"):
        return "有搭子" if s["target_count"] else "无搭子"
    return "应有{}张在手牌".format(s["target_count"])


def _target_display_set(mt) -> set:
    """目标牌的显示集合（0m/0p/0s 与 5m/5p/5s 视为不同牌）"""
    return set(mt) if isinstance(mt, list) else {mt}


def format_samples_for_display(samples: List[Dict], query_pattern_str: str, target_tile: str,
                               analysis_target: str = "target_count") -> str:
    """将样本格式化为可读文本，支持单张、搭子、听牌模式"""
    use_tenpai = (analysis_target == "tenpai")
    is_combo = False if use_tenpai else (samples[0].get("is_combo", False) if samples else False)
    target_label = target_tile if use_tenpai else (f"{target_tile} (搭子)" if is_combo else target_tile)
    lines = [
        "=" * 80,
        f"验证样本：{query_pattern_str} → {target_label}",
        "=" * 80,
        ""
    ]
    for i, s in enumerate(samples, 1):
        mt = s["mapped_target"]
        mt_set = _target_display_set(mt) if not use_tenpai else set()
        hand_parts = []
        for t in sorted(s["hand_tiles"], key=lambda x: (x // 4, x)):
            ts = MjlogParser.tile_to_string(t)
            hand_parts.append(f"[{ts}]" if (ts in mt_set) else ts)
        hand_str = " ".join(hand_parts)
        round_display = MjlogParser.format_round_display(s["round_num"], s["honba"])
        wind = MjlogParser.get_player_wind(s["player_id"], s["oya"])
        target_line = f"  听牌状态:   {_target_desc(s, use_tenpai)}" if use_tenpai else f"  目标牌:     {_fmt_target(mt)} ({_target_desc(s, use_tenpai)})"
        block = [
            f"【样本 {i}】",
            f"  对局ID:     {s['log_id']}",
            f"  天凤牌谱:   https://tenhou.net/0/?log={s['log_id']}",
            f"  小局/本场:  {round_display}",
            f"  目标玩家:   {wind}家",
            f"  巡目:       第{s['turn']}巡",
            f"  宝牌:       {s['dora_readable']}",
            f"  舍牌序列:   {' '.join(s['actual_pattern'])} ",
            target_line,
            f"  手牌({len(s['hand_tiles'])}张): {hand_str}",
        ]
        if not use_tenpai:
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
