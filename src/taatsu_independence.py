"""
搭子独立性筛选（independence filter）

在同花色 9 枚计数向量上定义结构价值 (M, T)（字典序）：
- M：最多能拆出的面子数（顺子 / 刻子，各 3 张）
- T：在 M 已达最大的前提下，剩余牌中最多能拆出的搭子/对子数（对子、两面/边张、嵌张，各 2 张）

对目标两枚数牌：若整手该花色 V_origin 与删去这两枚后 V_after 满足
V_origin == (V_after[0], V_after[1] + 1)，则视为该搭子在「最优结构」中恰好贡献一个搭子单位，
且未挤占面子；用于抑制长连中段的伪搭子（如 456789 中的 78、89）。
"""
from functools import lru_cache
from typing import List, Optional, Tuple

from .mjlog_parser import MjlogParser
from .related_tile_utils import base_to_discard_num_and_suit


def _hand_to_mps_z_counts(hand_tiles: List[int]) -> Tuple[List[int], List[int], List[int], List[int]]:
    """手牌 → 万/饼/索各 9 枚数字 + 字牌 7 枚（1z～7z），赤五计入对应花色的 5。"""
    m, p, s = [0] * 9, [0] * 9, [0] * 9
    z = [0] * 7
    for tile in hand_tiles:
        b = tile // 4
        if 0 <= b < 9:
            m[b] += 1
        elif 9 <= b < 18:
            p[b - 9] += 1
        elif 18 <= b < 27:
            s[b - 18] += 1
        elif b == 34:
            m[4] += 1
        elif b == 35:
            p[4] += 1
        elif b == 36:
            s[4] += 1
        elif 27 <= b <= 33:
            z[b - 27] += 1
    return m, p, s, z


def _parse_number_tile_suit_index(tile_str: str) -> Tuple[Optional[str], Optional[int]]:
    """
    目标牌串 → (花色 m/p/s, 点数 0～8 表示 1～9)。字牌或无法解析返回 (None, None)。
    """
    ts = (tile_str or "").strip()
    if not ts:
        return None, None
    honors = "东南西北白发中"
    if ts in honors:
        return None, None
    if len(ts) == 2 and ts[0].isdigit() and ts[1] == "z" and 1 <= int(ts[0]) <= 7:
        return None, None
    if len(ts) == 3 and ts[0].isdigit() and ts[1] == "." and ts[2] in "mps":
        return ts[2], int(ts[0]) - 1
    if len(ts) == 2 and ts[0].isdigit() and ts[1] in "mps":
        return ts[1], int(ts[0]) - 1
    if ts in ("0m", "0p", "0s"):
        return ts[1], 4
    try:
        b = MjlogParser.string_to_tile(ts)
    except ValueError:
        return None, None
    num, suit = base_to_discard_num_and_suit(b)
    if num is None or suit is None:
        return None, None
    return suit, num - 1


def _suit_vec(m: List[int], p: List[int], s: List[int], suit: str) -> List[int]:
    if suit == "m":
        return m
    if suit == "p":
        return p
    return s


def _remove_two_ranks(vec: List[int], ia: int, ib: int) -> Optional[List[int]]:
    """从单花色 9 维向量中各删 1 枚（同索引删 2 枚）。"""
    v = vec[:]
    if ia == ib:
        if v[ia] < 2:
            return None
        v[ia] -= 2
    else:
        if v[ia] < 1 or v[ib] < 1:
            return None
        v[ia] -= 1
        v[ib] -= 1
    return v


@lru_cache(maxsize=None)
def _max_T_blocks(state: Tuple[int, ...]) -> int:
    """
    仅用搭子/对子（不占面子）：对子、相邻两面/边张、嵌张；两两不交，求最多块数。
    """
    c = list(state)
    if sum(c) == 0:
        return 0
    best = 0
    for i in range(9):
        if c[i] >= 2:
            nc = tuple(c[j] - 2 if j == i else c[j] for j in range(9))
            best = max(best, 1 + _max_T_blocks(nc))
    for i in range(8):
        if c[i] and c[i + 1]:
            nc = list(c)
            nc[i] -= 1
            nc[i + 1] -= 1
            best = max(best, 1 + _max_T_blocks(tuple(nc)))
    for i in range(7):
        if c[i] and c[i + 2]:
            nc = list(c)
            nc[i] -= 1
            nc[i + 2] -= 1
            best = max(best, 1 + _max_T_blocks(tuple(nc)))
    return best


@lru_cache(maxsize=None)
def _suit_value_MT(state: Tuple[int, ...]) -> Tuple[int, int]:
    """
    单花色结构价值 (M, T)，字典序最大：先最大化面子数 M，再最大化搭子/对子数 T。
    面子：刻子（3 同）或顺子（连续 3 张）。
    """
    c = list(state)
    # 不取面子，整副牌只数 T
    best = (0, _max_T_blocks(state))
    for i in range(9):
        if c[i] >= 3:
            nc = tuple(c[j] - 3 if j == i else c[j] for j in range(9))
            m, t = _suit_value_MT(nc)
            cand = (1 + m, t)
            if cand > best:
                best = cand
    for i in range(7):
        if c[i] and c[i + 1] and c[i + 2]:
            nc = list(c)
            nc[i] -= 1
            nc[i + 1] -= 1
            nc[i + 2] -= 1
            m, t = _suit_value_MT(tuple(nc))
            cand = (1 + m, t)
            if cand > best:
                best = cand
    return best


def combo_passes_independence_filter(hand_tiles: List[int], mapped_tile_strings: List[str]) -> bool:
    """
    搭子 combo 是否通过独立性筛选。

    - 仅当恰好 2 张目标且为同花色数牌（含 5.p）时执行；否则视为通过（不筛）。
    - 在该花色计数上：V_origin == (V_after[0], V_after[1] + 1) 时通过。
    """
    if len(mapped_tile_strings) != 2:
        return True
    ta, tb = mapped_tile_strings[0], mapped_tile_strings[1]
    su_a, ia = _parse_number_tile_suit_index(ta)
    su_b, ib = _parse_number_tile_suit_index(tb)
    if su_a is None or su_b is None or su_a != su_b:
        return True

    m, p, s, _z = _hand_to_mps_z_counts(hand_tiles)
    vec = _suit_vec(m, p, s, su_a)
    vec_after = _remove_two_ranks(vec, ia, ib)
    if vec_after is None:
        return False

    v_o = _suit_value_MT(tuple(vec))
    v_a = _suit_value_MT(tuple(vec_after))
    # 核心：恰好损失一个搭子单位、面子数不变
    return v_o == (v_a[0], v_a[1] + 1)
