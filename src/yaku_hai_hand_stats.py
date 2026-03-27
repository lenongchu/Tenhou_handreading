"""
役牌手持统计（yaku honor tiles in hand）

按座计算该局对该玩家的役牌集合（自风 / 场风 / 三元），
再对手牌按 base 计数，得到役牌种类数、对子种数（含刻子仍记 1 种对子）、刻子种数（≥3 枚的役牌种类数）。
"""
from typing import Dict, List, Set

from .mjlog_parser import MjlogParser


def compute_yaku_hai_hand_stats(hand_tiles: List[int], yaku_bases: Set[int]) -> Dict[str, int]:
    """
    根据手牌与役牌 base 集合（已含自风、场风、三元，去重）统计三维整数（上限 5 用于直方图桶）。

    Returns:
        kinds: 手中至少 1 枚的役牌**种类**数
        pair_kinds: 至少 2 枚的役牌种类数（3 枚/4 枚仍计 1）
        triple_kinds: 至少 3 枚的役牌种类数（刻子）
    """
    # 手牌 base 频数（frequency）
    ctr: Dict[int, int] = {}
    for t in hand_tiles:
        b = t // 4
        ctr[b] = ctr.get(b, 0) + 1
    kinds = pair_kinds = triple_kinds = 0
    for b in yaku_bases:
        n = ctr.get(b, 0)
        if n >= 1:
            kinds += 1
        if n >= 2:
            pair_kinds += 1
        if n >= 3:
            triple_kinds += 1
    cap = 5
    return {
        "kinds": min(kinds, cap),
        "pair_kinds": min(pair_kinds, cap),
        "triple_kinds": min(triple_kinds, cap),
    }


def yaku_pair_units_bucket(pair_kinds_capped: int) -> int:
    """
    将「至少 2 枚的役牌种类数」pair_kinds（已 cap 到 0～5）映射为展示用四桶：
    0=零对、1=一对、2=两对、3=三对及以上（含 3/4/5 种对子）。
    """
    # 未 capped 的原始 pair_kinds 若超过 5，应先经 compute 的 cap；此处再夹紧到桶索引 0～3
    pk = max(0, min(5, int(pair_kinds_capped)))
    return min(pk, 3)


def yaku_hai_per_tile_counts(hand_tiles: List[int], yaku_bases: Set[int]) -> Dict[str, int]:
    """各役牌子在手中的枚数（仅出现 n>0 的键），用于样本展示。"""
    ctr: Dict[int, int] = {}
    for t in hand_tiles:
        b = t // 4
        ctr[b] = ctr.get(b, 0) + 1
    out: Dict[str, int] = {}
    for b in sorted(yaku_bases):
        n = ctr.get(b, 0)
        if n > 0:
            out[MjlogParser.tile_to_string(b * 4)] = n
    return out
