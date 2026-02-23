"""
模式匹配引擎

支持通配符模式查询，匹配舍牌序列
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
from collections import Counter
import logging

from .mjlog_parser import Discard

logger = logging.getLogger(__name__)


@dataclass
class DoraConstraint:
    """宝牌约束"""
    type: str  # "irrelevant" 或 "specific"
    dora_tile: Optional[str] = None  # 宝牌（如 "6s"）


class PatternMatcher:
    """模式匹配引擎"""
    
    def __init__(self):
        pass
    
    def match_pattern(
        self,
        pattern: str,
        discards: List[Discard],
        visible_tiles: Counter,
        dora_indicators: List[int],
        visible_constraints: Dict[str, Tuple[int, int]],
        dora_constraint: DoraConstraint,
        turn_range: Optional[Tuple[int, int]] = None
    ) -> bool:
        """
        匹配舍牌序列是否符合给定模式
        
        Args:
            pattern: 查询模式（如 "3s-1s", "3s-*-1s"）
            discards: 舍牌序列
            visible_tiles: 可见牌统计
            dora_indicators: 宝牌指示牌
            visible_constraints: 可见枚数约束
            dora_constraint: 宝牌约束
            turn_range: 巡目范围
            
        Returns:
            是否匹配
        """
        # 解析模式
        pattern_parts = pattern.split("-")
        
        # 提取手切牌
        tedashi_discards = [d for d in discards if not d.is_tsumogiri]
        
        # 检查巡目范围
        if turn_range:
            min_turn, max_turn = turn_range
            if tedashi_discards:
                if tedashi_discards[0].turn < min_turn or tedashi_discards[-1].turn > max_turn:
                    return False
        
        # 匹配模式
        if not self._match_discard_pattern(pattern_parts, tedashi_discards):
            return False
        
        # 检查可见枚数约束
        if not self._match_visible_constraints(visible_tiles, visible_constraints):
            return False
        
        # 检查宝牌约束
        if not self._match_dora_constraint(dora_indicators, dora_constraint, pattern):
            return False
        
        return True
    
    def _match_discard_pattern(self, pattern_parts: List[str], discards: List[Discard]) -> bool:
        """
        匹配舍牌模式
        
        Args:
            pattern_parts: 模式部分列表 如 ["3s", "1s"] 或 ["3s", "*", "1s"]
            discards: 手切牌列表
            
        Returns:
            是否匹配
        """
        pattern_idx = 0
        discard_idx = 0
        
        while pattern_idx < len(pattern_parts) and discard_idx < len(discards):
            part = pattern_parts[pattern_idx]
            
            if part == "*":
                # 通配符：匹配任意数量的摸切（此处已过滤为手切，所以跳到下一个手切）
                pattern_idx += 1
                discard_idx += 1
            elif part.startswith("*"):
                # *N 格式：匹配恰好N次摸切（暂不实现，留作未来扩展）
                logger.warning(f"暂不支持 *N 格式: {part}")
                return False
            else:
                # 具体牌：必须完全匹配
                # TODO: 需要将牌字符串转换为牌编码进行比较
                # 这里简化处理，假设已经是标准化后的格式
                if discard_idx >= len(discards):
                    return False
                
                # 简化：直接比较字符串（实际应该比较牌编码）
                # discard_tile_str = tile_to_string(discards[discard_idx].tile)
                # if discard_tile_str != part:
                #     return False
                
                pattern_idx += 1
                discard_idx += 1
        
        # 检查是否完全匹配
        return pattern_idx == len(pattern_parts) and discard_idx == len(discards)
    
    def _match_visible_constraints(
        self,
        visible_tiles: Counter,
        visible_constraints: Dict[str, Tuple[int, int]]
    ) -> bool:
        """
        检查可见枚数约束
        
        Args:
            visible_tiles: 可见牌统计
            visible_constraints: 可见枚数约束
            
        Returns:
            是否满足约束
        """
        for tile_str, (min_count, max_count) in visible_constraints.items():
            # TODO: 将牌字符串转换为牌编码
            # tile_code = string_to_tile(tile_str)
            # count = visible_tiles.get(tile_code, 0)
            
            # 简化处理（实际需要转换）
            count = 0  # 占位
            
            if not (min_count <= count <= max_count):
                return False
        
        return True
    
    def _match_dora_constraint(
        self,
        dora_indicators: List[int],
        dora_constraint: DoraConstraint,
        pattern: str
    ) -> bool:
        """
        检查宝牌约束
        
        Args:
            dora_indicators: 宝牌指示牌
            dora_constraint: 宝牌约束
            pattern: 舍牌模式
            
        Returns:
            是否满足约束
        """
        if not dora_indicators:
            return True
        
        if dora_constraint.type == "irrelevant":
            # 宝牌无关：宝牌指示物与舍牌序列花色不同
            # 提取舍牌序列的花色
            pattern_suit = self._extract_suit_from_pattern(pattern)
            if not pattern_suit:
                return True
            
            # 检查宝牌指示物的花色
            # TODO: 需要将牌编码转换为花色
            # indicator_suit = get_suit(dora_indicators[0])
            # return indicator_suit != pattern_suit
            
            return True  # 占位
        
        elif dora_constraint.type == "specific":
            # 宝牌为X：检查宝牌指示物是否对应该宝牌
            if not dora_constraint.dora_tile:
                return True
            
            # TODO: 需要将宝牌转换为指示物，然后比较
            # expected_indicator = dora_to_indicator(dora_constraint.dora_tile)
            # return dora_indicators[0] == expected_indicator
            
            return True  # 占位
        
        return True
    
    def _extract_suit_from_pattern(self, pattern: str) -> Optional[str]:
        """
        从模式中提取花色
        
        Args:
            pattern: 舍牌模式
            
        Returns:
            花色（"m", "p", "s"）或 None
        """
        match = re.search(r"[1-9]([mps])", pattern)
        if match:
            return match.group(1)
        return None
    
    def compile_pattern(self, pattern: str) -> List[str]:
        """
        编译模式为内部表示
        
        Args:
            pattern: 查询模式
            
        Returns:
            模式部分列表
        """
        return pattern.split("-")


def match_pattern(
    pattern: str,
    discards: List[Discard],
    visible_tiles: Counter,
    dora_indicators: List[int],
    visible_constraints: Dict[str, Tuple[int, int]],
    dora_constraint: DoraConstraint,
    turn_range: Optional[Tuple[int, int]] = None
) -> bool:
    """
    便捷函数：匹配舍牌序列
    
    Args:
        pattern: 查询模式
        discards: 舍牌序列
        visible_tiles: 可见牌统计
        dora_indicators: 宝牌指示牌
        visible_constraints: 可见枚数约束
        dora_constraint: 宝牌约束
        turn_range: 巡目范围
        
    Returns:
        是否匹配
    """
    matcher = PatternMatcher()
    return matcher.match_pattern(
        pattern, discards, visible_tiles, dora_indicators,
        visible_constraints, dora_constraint, turn_range
    )


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    matcher = PatternMatcher()
    
    # 测试模式编译
    pattern = "3s-*-1s"
    parts = matcher.compile_pattern(pattern)
    print(f"模式 '{pattern}' 编译为: {parts}")
    
    # 测试花色提取
    suit = matcher._extract_suit_from_pattern("7s-9s")
    print(f"模式 '7s-9s' 的花色: {suit}")
