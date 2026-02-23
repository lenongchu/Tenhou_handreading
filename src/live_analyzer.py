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
from .simple_normalizer import (
    generate_equivalent_variants,
    match_discard_to_variant,
    parse_target_tiles,
    get_consumed_search_patterns,
    log_contains_consumed,
    round_has_matching_consumed,
)

logger = logging.getLogger(__name__)


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


def _empty_analysis_result(query_pattern: List[str], target_tile: str) -> Dict:
    """用户取消时返回的空结果"""
    _, is_combo = parse_target_tiles(target_tile)
    dist = {0: 0, 1: 0} if is_combo else {0: 0, 1: 0, 2: 0, 3: 0}
    return {
        'total_logs_analyzed': 0,
        'total_matches': 0,
        'target_count_distribution': dist,
        'probability_distribution': {k: 0.0 for k in dist},
        'query_pattern': query_pattern,
        'query_pattern_str': '-'.join(query_pattern),
        'target_tile': target_tile,
        'turn_range': None,
        'variants_count': 0,
        'matched_states': [],
        'is_combo': is_combo,
    }


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
        query_pattern: List[str],
        target_tile: str,
        dora_constraint: Optional[str] = None,
        visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
        riichi_constraint: Optional[str] = None,
        call_constraint: Optional[str] = None,  # "any" | "has_call" | "no_call"
        turn_range: Optional[Tuple[int, int]] = None,  # (min_turn, max_turn) 如 (2, 8)
        sample_limit: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        total_logs_hint: Optional[int] = None,
    ) -> Dict:
        """
        分析舍牌模式，计算目标牌在手牌中的概率
        
        Args:
            query_pattern: 查询的舍牌序列，如 ["7s", "9s"]
            target_tile: 目标牌，如 "8s"
            dora_constraint: 宝牌约束，"any" 或具体牌如 "6s"
            visible_constraints: 可见枚数约束，如 {"8s": (0, 2)}
            riichi_constraint: 立直约束，"any"=无限制, "has_riichi"=有人立直, "no_riichi"=无人立直
            call_constraint: 副露约束，"any"=无限制, "has_call"=有人副露, "no_call"=无人副露
            turn_range: 巡目范围 (min, max)，如 (2, 8) 表示仅分析 2-8 巡内的舍牌
            sample_limit: 最大分析对局数（None = 全部）
            progress_callback: 进度回调函数 (current, total)
            should_cancel: 取消检查函数，返回 True 则停止分析
            total_logs_hint: 对局总数预估值（避免耗时的 COUNT(*)；0 表示未知）
            
        Returns:
            分析结果字典
        """
        # 生成所有等价变体
        variants = generate_equivalent_variants(
            query_pattern, target_tile, visible_constraints
        )
        _, is_combo = parse_target_tiles(target_tile)
        consumed_search = get_consumed_search_patterns(query_pattern)

        # 统计结果；搭子模式只统计 有/没有
        matched_states = []
        total_matches = 0
        target_count_distribution = (
            {0: 0, 1: 0} if is_combo else {0: 0, 1: 0, 2: 0, 3: 0}
        )
        
        if should_cancel and should_cancel():
            return _empty_analysis_result(query_pattern, target_tile)

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
        
        # 批量读取并分析
        batch_size = 1000
        offset = 0
        processed = 0
        
        while True:
            # 检查是否取消
            if should_cancel and should_cancel():
                logger.info("分析已取消")
                break
            
            # 读取批次：优先 log_json（tenhou6 JSON），若无则用 log（XML）
            query = """
                SELECT id, COALESCE(log_json, log) as content
                FROM logs 
                WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '')
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """
            cur.execute(query, (batch_size, offset))
            logs = cur.fetchall()
            
            if not logs:
                break
            
            # 解析并匹配
            for log_id, log_content in logs:
                if should_cancel and should_cancel():
                    conn.close()
                    return _empty_analysis_result(query_pattern, target_tile)
                try:
                    raw = _get_raw_content(log_content)
                    if consumed_search and _is_tenhou6_json(raw):
                        if not log_contains_consumed(raw, consumed_search):
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
                        
                        
                        dora_str = None
                        if dora_constraint and dora_constraint != "any" and round_players[0].dora_indicators:
                            dora_str = MjlogParser.tile_to_string(round_players[0].dora_indicators[0])
                            # 指定宝牌：局级判断，不满足则跳过整局
                            # 目前测试，之后可能也作等价变体处理
                            if dora_constraint != "dora_unrelated" and dora_str != dora_constraint:
                                continue

                        # 含吃碰时：仅处理本局确有该副露的小局，避免半庄级预过滤误通过
                        if consumed_search and not round_has_matching_consumed(round_players, consumed_search):
                            continue
                        
                        # 分析该局每个玩家
                        for player_state in round_players:
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

                            # 遍历范围内舍牌
                            for j, (orig_i, discard) in enumerate(in_range):
                                discarded_bases.add(discard.tile // 4)
                                full_discards_up_to_now = discards_precomputed[:j+1]
                                hand_discard_strings = [
                                    f"{t}t" if ts else t for t, ts in full_discards_up_to_now
                                ]

                                honor_ctx = {**honor_ctx_base, "current_discard_turn": discard.turn}
                                matched_variant = match_discard_to_variant(full_discards_up_to_now, variants, honor_ctx)
                                if not matched_variant:
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
                                
                                # 检查可见枚数约束（含宝牌指示物，mjlog_parser 已将其计入 visible_tiles）
                                vc = matched_variant["visible_constraints"]
                                if vc:
                                    match_visible = True
                                    for tile_str, (min_count, max_count) in vc.items():
                                        base_code = MjlogParser.string_to_tile(tile_str)
                                        count = sum(
                                            c for t, c in player_state.visible_tiles.items()
                                            if t // 4 == base_code
                                        )
                                        if not (min_count <= count <= max_count):
                                            match_visible = False
                                            break
                                    if not match_visible:
                                        continue
                                
                                # 匹配成功！
                                total_matches += 1
                                
                                # 获取该巡打牌后的手牌快照（orig_i 为完整舍牌序列中的下标）
                                if orig_i < len(player_state.hand_tiles_history):
                                    hand_at_turn = player_state.hand_tiles_history[orig_i]
                                else:
                                    logger.warning(f"手牌历史记录不足：巡目{orig_i+1}，历史长度{len(player_state.hand_tiles_history)}")
                                    hand_at_turn = player_state.hand_tiles
                                
                                # 使用匹配变体的目标牌（无需映射）
                                mapped_target = matched_variant["target"]
                                if is_combo:
                                    # 搭子：手牌是否包含所有目标牌（每种至少1张）
                                    target_codes = [
                                        MjlogParser.string_to_tile(t) for t in mapped_target
                                    ]
                                    hand_bases = [t // 4 for t in hand_at_turn]
                                    target_count = 1 if all(
                                        hand_bases.count(c) >= 1 for c in target_codes
                                    ) else 0
                                else:
                                    mapped_target_code = MjlogParser.string_to_tile(mapped_target)
                                    target_count = sum(
                                        1 for tile in hand_at_turn
                                        if tile // 4 == mapped_target_code
                                    )
                                    target_count = min(target_count, 3)
                                target_count_distribution[target_count] += 1
                                
                                # 记录匹配状态
                                matched_states.append({
                                    'round_num': player_state.round_num,
                                    'turn': discard.turn,
                                    'target_count': target_count,
                                    'actual_pattern': hand_discard_strings,
                                    'mapped_target': mapped_target,
                                    'hand_tiles': list(hand_at_turn),
                                    'visible_tiles': dict(player_state.visible_tiles)
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
            
            offset += batch_size
            
            # 达到样本限制
            if sample_limit and processed >= sample_limit:
                break
        
        conn.close()

        # 计算概率分布
        probability_distribution = {}
        keys = [0, 1] if is_combo else [0, 1, 2, 3]
        for count in keys:
            prob = (target_count_distribution.get(count, 0) / total_matches * 100) if total_matches > 0 else 0
            probability_distribution[count] = prob

        result = {
            'total_logs_analyzed': processed,
            'total_matches': total_matches,
            'target_count_distribution': target_count_distribution,
            'probability_distribution': probability_distribution,
            'query_pattern': query_pattern,
            'query_pattern_str': '-'.join(query_pattern),
            'target_tile': target_tile,
            'turn_range': turn_range,
            'variants_count': len(variants),
            'matched_states': matched_states[:100],
            'is_combo': is_combo
        }

        logger.info(f"分析完成: 匹配 {total_matches} 个状态")
        if is_combo:
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
    ) -> List[Dict]:
        """
        收集验证样本，用于人工复盘核对。
        返回含 log_id、oya、正确小局显示等完整信息的样本列表。
        """
        variants = generate_equivalent_variants(
            query_pattern, target_tile, visible_constraints
        )
        consumed_search = get_consumed_search_patterns(query_pattern)
        samples = []

        def _visible_count(visible_tiles: dict, tile_str: str) -> int:
            base = MjlogParser.string_to_tile(tile_str)
            return sum(c for t, c in visible_tiles.items() if t // 4 == base)

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

        batch_size = 1000
        offset = 0
        processed = 0

        while True:
            if should_cancel and should_cancel():
                break
            cur.execute(
                "SELECT id, COALESCE(NULLIF(log_json, ''), log) FROM logs WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '') ORDER BY id DESC LIMIT ? OFFSET ?",
                (batch_size, offset),
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

                        for player_state in round_players:
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

                            for j, (orig_i, discard) in enumerate(in_range):
                                discarded_bases.add(discard.tile // 4)
                                full_discards_up_to_now = discards_precomputed[:j + 1]
                                hand_discard_strings = [
                                    f"{t}t" if ts else t for t, ts in full_discards_up_to_now
                                ]

                                honor_ctx = {**honor_ctx_base, "current_discard_turn": discard.turn}
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
                                        count = sum(
                                            c for t, c in player_state.visible_tiles.items()
                                            if t // 4 == base_code
                                        )
                                        if not (min_count <= count <= max_count):
                                            match_visible = False
                                            break
                                    if not match_visible:
                                        continue

                                hand_at_turn = (
                                    player_state.hand_tiles_history[orig_i]
                                    if orig_i < len(player_state.hand_tiles_history)
                                    else player_state.hand_tiles
                                )
                                mapped_target = matched_variant["target"]
                                sample_is_combo = matched_variant.get("is_combo", False)
                                if sample_is_combo:
                                    target_codes = [
                                        MjlogParser.string_to_tile(t) for t in mapped_target
                                    ]
                                    hand_bases = [t // 4 for t in hand_at_turn]
                                    target_count = 1 if all(
                                        hand_bases.count(c) >= 1 for c in target_codes
                                    ) else 0
                                else:
                                    mapped_target_code = MjlogParser.string_to_tile(mapped_target)
                                    target_count = sum(
                                        1 for tile in hand_at_turn
                                        if tile // 4 == mapped_target_code
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
            offset += batch_size
            if sample_limit and processed >= sample_limit:
                break
            if len(samples) >= sample_count:
                break

        conn.close()
        return samples


def _fmt_target(mt) -> str:
    """格式化目标牌（单张或搭子）"""
    return "-".join(mt) if isinstance(mt, list) else str(mt)


def _target_desc(s: dict) -> str:
    """目标牌描述：有搭子/无搭子 或 应有X张在手牌"""
    if s.get("is_combo"):
        return "有搭子" if s["target_count"] else "无搭子"
    return "应有{}张在手牌".format(s["target_count"])


def format_samples_for_display(samples: List[Dict], query_pattern_str: str, target_tile: str) -> str:
    """将样本格式化为可读文本，支持单张和搭子"""
    is_combo = samples[0].get("is_combo", False) if samples else False
    target_label = f"{target_tile} (搭子)" if is_combo else target_tile
    lines = [
        "=" * 80,
        f"验证样本：{query_pattern_str} → {target_label}",
        "=" * 80,
        ""
    ]
    for i, s in enumerate(samples, 1):
        mt = s["mapped_target"]
        mt_set = set(mt) if isinstance(mt, list) else {mt}
        hand_parts = []
        for t in sorted(s["hand_tiles"], key=lambda x: (x // 4, x)):
            ts = MjlogParser.tile_to_string(t)
            hand_parts.append(f"[{ts}]" if ts in mt_set else ts)
        hand_str = " ".join(hand_parts)
        round_display = MjlogParser.format_round_display(s["round_num"], s["honba"])
        wind = MjlogParser.get_player_wind(s["player_id"], s["oya"])

        lines.extend([
            f"【样本 {i}】",
            f"  对局ID:     {s['log_id']}",
            f"  天凤牌谱:   https://tenhou.net/0/?log={s['log_id']}",
            f"  小局/本场:  {round_display}",
            f"  目标玩家:   {wind}家)",
            f"  巡目:       第{s['turn']}巡",
            f"  宝牌:       {s['dora_readable']}",
            f"  舍牌序列:   {' '.join(s['actual_pattern'])} ",
            f"  目标牌:     {_fmt_target(mt)} ({_target_desc(s)})",
            f"  手牌({len(s['hand_tiles'])}张): {hand_str}",
            f"  可见{_fmt_target(mt)}: {s['visible_target']}{'' if s.get('is_combo') else '张'} (他家舍牌+宝牌指示物)",
            ""
        ])
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
    import 