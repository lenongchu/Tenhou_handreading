"""
搭子独立性筛选（independence filter）

从手牌中尽可能拆除面子（顺子/刻子）与字牌刻子后，若存在一种拆法使目标两枚数牌
在所余牌中无法与邻牌组成完整面子，则视为「独立搭子」。用于避免长连如 45678 中
45、56、67、78 被重复统计（仅边界 45、78 通过）。
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


def _reserve_two_tiles(
    m: List[int],
    p: List[int],
    s: List[int],
    suit_a: str,
    idx_a: int,
    suit_b: str,
    idx_b: int,
) -> Optional[Tuple[List[int], List[int], List[int], List[int], List[int], List[int]]]:
    """
    从手牌计数中各扣 1 枚目标牌，成功则返回
    (rem_m, rem_p, rem_s, prot_m, prot_p, prot_s)；prot 仅两枚目标位为 1。
    """
    prot_m, prot_p, prot_s = [0] * 9, [0] * 9, [0] * 9
    rm, rp, rs = m[:], p[:], s[:]

    def take(vec, prot, idx):
        if vec[idx] < 1:
            return False
        vec[idx] -= 1
        prot[idx] += 1
        return True

    if suit_a == "m":
        if not take(rm, prot_m, idx_a):
            return None
    elif suit_a == "p":
        if not take(rp, prot_p, idx_a):
            return None
    else:
        if not take(rs, prot_s, idx_a):
            return None

    if suit_b == "m":
        if not take(rm, prot_m, idx_b):
            return None
    elif suit_b == "p":
        if not take(rp, prot_p, idx_b):
            return None
    else:
        if not take(rs, prot_s, idx_b):
            return None

    return (rm, rp, rs, prot_m, prot_p, prot_s)


def _has_any_meld(
    m: Tuple[int, ...], p: Tuple[int, ...], s: Tuple[int, ...], z: Tuple[int, ...]
) -> bool:
    """可拆除的顺子（三色）或刻子（含字）是否存在。"""
    for vec in (m, p, s):
        for i in range(9):
            if vec[i] >= 3:
                return True
        for i in range(7):
            if vec[i] and vec[i + 1] and vec[i + 2]:
                return True
    for i in range(7):
        if z[i] >= 3:
            return True
    return False


def _remainder_taatsu_independent(R: List[int], i: int, j: int) -> bool:
    """
    在所余某一花色 9 枚向量 R（含目标搭子）上，判断两索引 i<=j 是否「不与邻牌成面子」。
    """
    if i > j:
        i, j = j, i
    if i == j:
        # 对子：不能再有第三张同数
        return R[i] <= 2
    if j == i + 1:
        # 两面/边张：不能同时存在 i-1 与 i+2（即不能补成顺）
        left_ext = i > 0 and R[i - 1] > 0
        right_ext = j < 8 and R[j + 1] > 0
        return not left_ext and not right_ext
    if j == i + 2:
        # 嵌张：中间不能存在
        return R[i + 1] == 0
    # 非标准两枚搭子间距：不额外限制
    return True


def combo_passes_independence_filter(hand_tiles: List[int], mapped_tile_strings: List[str]) -> bool:
    """
    搭子 combo 是否通过独立性筛选。

    - 仅当恰好 2 张目标且为同花色数牌（含 5.p）时执行；否则视为通过（不筛）。
    - 若存在一种从「非保留牌」中拆除面子/刻子的方式，使合并后该花色所余满足
      _remainder_taatsu_independent，则返回 True。
    """
    if len(mapped_tile_strings) != 2:
        return True
    ta, tb = mapped_tile_strings[0], mapped_tile_strings[1]
    su_a, ia = _parse_number_tile_suit_index(ta)
    su_b, ib = _parse_number_tile_suit_index(tb)
    if su_a is None or su_b is None or su_a != su_b:
        return True

    m, p, s, z = _hand_to_mps_z_counts(hand_tiles)
    reserved = _reserve_two_tiles(m, p, s, su_a, ia, su_b, ib)
    if reserved is None:
        return False
    rm, rp, rs, prot_m, prot_p, prot_s = reserved
    rz = z[:]  # 字牌不参与保留，全部留在可拆池

    prot_m_t = tuple(prot_m)
    prot_p_t = tuple(prot_p)
    prot_s_t = tuple(prot_s)
    suit_key = su_a
    idx_lo, idx_hi = (ia, ib) if ia <= ib else (ib, ia)

    @lru_cache(maxsize=None)
    def dfs(rm_t, rp_t, rs_t, rz_t):
        """在可拆余牌 (rm,rp,rs,rz) 上递归拆面子，任一终态满足独立性即 True。"""
        if not _has_any_meld(rm_t, rp_t, rs_t, rz_t):
            Rm = [rm_t[i] + prot_m_t[i] for i in range(9)]
            Rp = [rp_t[i] + prot_p_t[i] for i in range(9)]
            Rs = [rs_t[i] + prot_s_t[i] for i in range(9)]
            R = Rm if suit_key == "m" else Rp if suit_key == "p" else Rs
            return _remainder_taatsu_independent(R, idx_lo, idx_hi)

        rm = list(rm_t)
        rp = list(rp_t)
        rs = list(rs_t)
        rz = list(rz_t)

        # 字刻子
        for i in range(7):
            if rz[i] >= 3:
                nz = rz[:]
                nz[i] -= 3
                if dfs(tuple(rm), tuple(rp), tuple(rs), tuple(nz)):
                    return True

        for name, vec in ("m", rm), ("p", rp), ("s", rs):
            v = vec
            for i in range(9):
                if v[i] >= 3:
                    nv = v[:]
                    nv[i] -= 3
                    if name == "m":
                        if dfs(tuple(nv), tuple(rp), tuple(rs), tuple(rz)):
                            return True
                    elif name == "p":
                        if dfs(tuple(rm), tuple(nv), tuple(rs), tuple(rz)):
                            return True
                    else:
                        if dfs(tuple(rm), tuple(rp), tuple(nv), tuple(rz)):
                            return True
            for i in range(7):
                if v[i] and v[i + 1] and v[i + 2]:
                    nv = v[:]
                    nv[i] -= 1
                    nv[i + 1] -= 1
                    nv[i + 2] -= 1
                    if name == "m":
                        if dfs(tuple(nv), tuple(rp), tuple(rs), tuple(rz)):
                            return True
                    elif name == "p":
                        if dfs(tuple(rm), tuple(nv), tuple(rs), tuple(rz)):
                            return True
                    else:
                        if dfs(tuple(rm), tuple(rp), tuple(nv), tuple(rz)):
                            return True
        return False

    return dfs(tuple(rm), tuple(rp), tuple(rs), tuple(rz))
