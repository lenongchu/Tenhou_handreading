"""
牌谱解析器测试
"""

import pytest
from collections import Counter

from src.mjlog_parser import MjlogParser, Discard, GameState


class TestMjlogParser:
    """MjlogParser 测试类"""
    
    def test_tile_to_string(self):
        """测试牌编码转换为字符串"""
        # 数牌
        assert MjlogParser.tile_to_string(0) == "1m"
        assert MjlogParser.tile_to_string(8) == "9m"
        assert MjlogParser.tile_to_string(9) == "1p"
        assert MjlogParser.tile_to_string(17) == "9p"
        assert MjlogParser.tile_to_string(18) == "1s"
        assert MjlogParser.tile_to_string(26) == "9s"
        
        # 风牌
        assert MjlogParser.tile_to_string(27) == "东"
        assert MjlogParser.tile_to_string(28) == "南"
        assert MjlogParser.tile_to_string(29) == "西"
        assert MjlogParser.tile_to_string(30) == "北"
        
        # 三元牌
        assert MjlogParser.tile_to_string(31) == "白"
        assert MjlogParser.tile_to_string(32) == "发"
        assert MjlogParser.tile_to_string(33) == "中"
    
    def test_string_to_tile(self):
        """测试字符串转换为牌编码"""
        # 数牌
        assert MjlogParser.string_to_tile("1m") == 0
        assert MjlogParser.string_to_tile("9m") == 8
        assert MjlogParser.string_to_tile("1p") == 9
        assert MjlogParser.string_to_tile("9p") == 17
        assert MjlogParser.string_to_tile("1s") == 18
        assert MjlogParser.string_to_tile("9s") == 26
        
        # 风牌
        assert MjlogParser.string_to_tile("东") == 27
        assert MjlogParser.string_to_tile("南") == 28
        assert MjlogParser.string_to_tile("西") == 29
        assert MjlogParser.string_to_tile("北") == 30
        
        # 三元牌
        assert MjlogParser.string_to_tile("白") == 31
        assert MjlogParser.string_to_tile("发") == 32
        assert MjlogParser.string_to_tile("中") == 33
    
    def test_indicator_to_dora(self):
        """测试宝牌指示物转换为宝牌"""
        # 数牌
        assert MjlogParser.indicator_to_dora(18) == 19  # 1s -> 2s
        assert MjlogParser.indicator_to_dora(22) == 23  # 5s -> 6s
        assert MjlogParser.indicator_to_dora(26) == 18  # 9s -> 1s (循环)
        
        # 风牌
        assert MjlogParser.indicator_to_dora(27) == 28  # 东 -> 南
        assert MjlogParser.indicator_to_dora(28) == 29  # 南 -> 西
        assert MjlogParser.indicator_to_dora(29) == 30  # 西 -> 北
        assert MjlogParser.indicator_to_dora(30) == 27  # 北 -> 东 (循环)
        
        # 三元牌
        assert MjlogParser.indicator_to_dora(31) == 32  # 白 -> 发
        assert MjlogParser.indicator_to_dora(32) == 33  # 发 -> 中
        assert MjlogParser.indicator_to_dora(33) == 31  # 中 -> 白 (循环)
    
    def test_dora_to_indicator(self):
        """测试宝牌转换为宝牌指示物"""
        # 数牌
        assert MjlogParser.dora_to_indicator(19) == 18  # 2s -> 1s
        assert MjlogParser.dora_to_indicator(23) == 22  # 6s -> 5s
        assert MjlogParser.dora_to_indicator(18) == 26  # 1s -> 9s (循环)
        
        # 风牌
        assert MjlogParser.dora_to_indicator(28) == 27  # 南 -> 东
        assert MjlogParser.dora_to_indicator(29) == 28  # 西 -> 南
        assert MjlogParser.dora_to_indicator(30) == 29  # 北 -> 西
        assert MjlogParser.dora_to_indicator(27) == 30  # 东 -> 北 (循环)
        
        # 三元牌
        assert MjlogParser.dora_to_indicator(32) == 31  # 发 -> 白
        assert MjlogParser.dora_to_indicator(33) == 32  # 中 -> 发
        assert MjlogParser.dora_to_indicator(31) == 33  # 白 -> 中 (循环)
    
    # 解析逻辑已迁移至 tenhou6_adapter，_parse_hand_tiles / _parse_dora_indicators 已移除


class TestDiscard:
    """Discard 测试类"""
    
    def test_discard_creation(self):
        """测试舍牌对象创建"""
        discard = Discard(turn=1, tile=20, is_tsumogiri=False)
        assert discard.turn == 1
        assert discard.tile == 20
        assert discard.is_tsumogiri == False
        
        discard2 = Discard(turn=2, tile=22, is_tsumogiri=True)
        assert discard2.turn == 2
        assert discard2.tile == 22
        assert discard2.is_tsumogiri == True


class TestGameState:
    """GameState 测试类"""
    
    def test_game_state_creation(self):
        """测试游戏状态对象创建"""
        state = GameState(player_id=0)
        assert state.player_id == 0
        assert state.discards == []
        assert state.hand_tiles == set()
        assert state.dora_indicators == []
        assert state.visible_tiles == Counter()
    
    def test_game_state_with_data(self):
        """测试带数据的游戏状态"""
        state = GameState(
            player_id=1,
            discards=[Discard(1, 20, False)],
            hand_tiles={0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12},
            dora_indicators=[23],
            visible_tiles=Counter({20: 1, 21: 2})
        )
        
        assert state.player_id == 1
        assert len(state.discards) == 1
        assert state.discards[0].tile == 20
        assert len(state.hand_tiles) == 13
        assert state.dora_indicators == [23]
        assert state.visible_tiles[20] == 1
        assert state.visible_tiles[21] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
