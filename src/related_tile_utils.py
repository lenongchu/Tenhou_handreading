"""
关联牌判断工具

判断打出的数牌是否与剩余手牌中的搭子/对子存在关联。
关联定义：手里存在与打出牌距离 2 以内的搭子（两面、边张、嵌张）或对子。
"""
from typing import List


def is_related_discard(discard_tile: int, counts: List[int]) -> bool:
    """
    判断打出的数牌是否与剩余手牌中的搭子/对子存在关联。

    :param discard_tile: 1 到 9 的整数，代表打出的数牌 (如 5 代表 5p)
    :param counts: 长度为 9 的列表，代表该花色 1-9 的剩余数量
    """
    # 将 1-9 转换为 0-8 的数组索引
    t = discard_tile - 1

    # 1. 检查所有对子 (y, y)
    for i in range(9):
        if counts[i] >= 2:
            # 如果对子中的牌与打出的牌距离 <= 2
            if abs(i - t) <= 2:
                return True

    # 2. 检查所有两面/边张 (y, y+1)
    for i in range(8):
        if counts[i] >= 1 and counts[i + 1] >= 1:
            # 如果搭子中至少有一张牌与打出的牌距离 <= 2
            if abs(i - t) <= 2 or abs((i + 1) - t) <= 2:
                return True

    # 3. 检查所有嵌张 (y, y+2)
    for i in range(7):
        if counts[i] >= 1 and counts[i + 2] >= 1:
            # 如果嵌张中至少有一张牌与打出的牌距离 <= 2
            if abs(i - t) <= 2 or abs((i + 2) - t) <= 2:
                return True

    return False


def hand_to_suit_counts(hand_tiles: List[int], suit: str) -> List[int]:
    """
    从手牌（tile 编码 0-147）提取指定花色的 1-9 枚数。

    :param hand_tiles: 手牌列表，元素为 tile 编码 (0-147)
    :param suit: 花色 "m" | "p" | "s"
    :return: 长度为 9 的列表，counts[i] 表示 (i+1) 的枚数；赤五计入 5
    """
    counts = [0] * 9
    if suit == "m":
        bases = list(range(9)) + [34]  # 1m-9m, 0m
        base_to_idx = {b: b for b in range(9)}
        base_to_idx[34] = 4  # 0m -> 5
    elif suit == "p":
        bases = list(range(9, 18)) + [35]  # 1p-9p, 0p
        base_to_idx = {b: b - 9 for b in range(9, 18)}
        base_to_idx[35] = 4  # 0p -> 5
    elif suit == "s":
        bases = list(range(18, 27)) + [36]  # 1s-9s, 0s
        base_to_idx = {b: b - 18 for b in range(18, 27)}
        base_to_idx[36] = 4  # 0s -> 5
    else:
        return counts

    base_set = set(bases)
    for tile in hand_tiles:
        base = tile // 4
        if base in base_set:
            idx = base_to_idx[base]
            counts[idx] += 1
    return counts


def base_to_discard_num_and_suit(base: int) -> tuple:
    """
    将 base 编码 (0-36) 转为 (num, suit)，仅数牌有效。

    :param base: 牌 base 编码
    :return: (num, suit) 或 (None, None) 若为字牌
    """
    if 0 <= base < 9:
        return (base + 1, "m")
    if 9 <= base < 18:
        return (base - 8, "p")
    if 18 <= base < 27:
        return (base - 17, "s")
    if base == 34:
        return (5, "m")
    if base == 35:
        return (5, "p")
    if base == 36:
        return (5, "s")
    return (None, None)
