"""
听牌判断工具

使用 MahjongRepository/mahjong 库计算向听数，shanten=0 表示听牌。
已在天凤牌谱上验证与 tenhou.net 一致。
"""
from typing import List, Union

# 赤五 base 34/35/36 映射到标准 5m/5p/5s (base 4/13/22)，mahjong 库不区分赤五
RED_TO_STANDARD = {34: 4, 35: 13, 36: 22}


def hand_to_tiles_34(hand_tiles: List[int]) -> List[int]:
    """
    将手牌（tile 编码 0-147）转为 mahjong 库的 tiles_34 格式。
    赤五 (base 34/35/36) 并入对应 5m/5p/5s。
    """
    tiles_34 = [0] * 34
    for t in hand_tiles:
        base = t // 4
        base = RED_TO_STANDARD.get(base, base)
        if 0 <= base < 34:
            tiles_34[base] += 1
    return tiles_34


def is_tenpai(hand_tiles: List[int]) -> bool:
    """
    判断手牌是否听牌（shanten=0）。
    
    Args:
        hand_tiles: 手牌 tile 编码列表（舍牌后：0 副露 13 张，1 副露 10 张，2 副露 7 张，3 副露 4 张）
    库内部根据张数自动推断已副露数：init_mentsu=(14-total)//3
    """
    try:
        from mahjong.shanten import Shanten
    except ImportError:
        return False
    tiles_34 = hand_to_tiles_34(hand_tiles)
    total = sum(tiles_34)
    if total > 14 or total < 1:
        return False
    try:
        shanten = Shanten().calculate_shanten(tiles_34, use_chiitoitsu=True, use_kokushi=True)
    except Exception:
        return False
    return shanten == 0
