"""
等价变体生成工具

根据输入的舍牌序列、目标牌、可见牌约束，生成所有等价的变体（6个）。
直接与舍牌序列精确比对，不使用标准化。

等价规则：
1. 花色等价：1s-3s ≡ 1m-3m ≡ 1p-3p
2. 镜像等价：1s-3s ≡ 9s-7s（数字 1↔9, 2↔8, 3↔7, 4↔6）
3. 顺序敏感：3s-1s ≠ 1s-3s

摸切符号：牌后加 t 表示必须摸切，如 "3mt-1m" 表示 3m 摸切、1m 手切。
* : 任意数量的摸切
$ : 任意一张手切（吃/碰之后的牌必为手切，如 c0p6p-$）
"""
import re
from typing import List, Dict, Tuple, Optional, Union
from itertools import product, permutations

from .mjlog_parser import MjlogParser


# 字牌占位符：z=任意字牌, zf=自风, kf=客风, yp=役牌(自风/场风/三元), z1/z2/z3=互不相同的字牌, kf1/kf2/kf3=互不相同的客风
HONOR_PLACEHOLDERS = frozenset({"z", "zt", "zf", "kf", "yp", "z1", "z2", "z3", "kf1", "kf2", "kf3"})
HONOR_NAMES = ("东", "南", "西", "北", "白", "发", "中")
# 1z-4z 风牌对应中文，用于 @p:kf 客风校验
Z_TO_WIND = {"1z": "东", "2z": "南", "3z": "西", "4z": "北"}

# 赤五输入：0m=赤5m, 0p=赤5p, 0s=赤5s（编码独立 base 34/35/36，与 5m/5p/5s 视为不同牌）
RED_FIVES = frozenset({"0m", "0p", "0s"})

# 吃碰占位符前缀：解析后为 @c:... 或 @p:...；语义为具体吃的/碰的牌（如 4mc3m5m=用3m5m吃4m）
# 含吃或数牌碰时不生成花色/镜像等价变体，仅碰字牌时仍生成变体
CALL_PREFIX = "@"


def _wind_to_z(wind: str) -> str:
    """东/南/西/北 -> 1z/2z/3z/4z"""
    m = {"东": "1z", "南": "2z", "西": "3z", "北": "4z"}
    return m.get(wind, wind)


# tenhou-paifu-to-json 的 consumed 可能为 "1z"/"3z" 或 "东"/"西" 等，需统一为 1z-7z 再与 yakuhai_set 比较
HONOR_TO_Z = {"东": "1z", "南": "2z", "西": "3z", "北": "4z", "白": "5z", "发": "6z", "中": "7z"}


def _honor_tile_to_z(tile: str) -> str:
    """将字牌统一为 1z-7z 格式，便于役牌比较"""
    if tile in HONOR_TO_Z:
        return HONOR_TO_Z[tile]
    if isinstance(tile, str) and len(tile) == 2 and tile[0].isdigit() and tile[1] == "z":
        return tile
    return tile


def parse_call_area_constraint(s: str) -> Optional[Tuple[str, Optional[List[str]], Optional[str]]]:
    """
    解析副露区域约束字符串。
    "pzfzf" -> ("pon", None, "zf") 碰自风
    "pypyp" -> ("pon", None, "yp") 碰役牌
    "4mc3m5m" -> ("chii", ["3m","5m"], None) 用3m5m吃4m
    "p1z1z" -> ("pon", ["1z","1z"], None) 碰东
    """
    s = (s or "").strip()
    if len(s) < 2:
        return None
    s_lower = s.lower()
    if "c" in s_lower and ("m" in s_lower or "p" in s_lower or "s" in s_lower):
        idx = s_lower.index("c")
        consumed_str = s[idx + 1:]
        tiles = _split_tiles(consumed_str)
        if len(tiles) >= 2:
            return ("chii", tiles[:2], None)
    elif s_lower.startswith("p") and len(s) >= 2:
        rest = s_lower[1:]
        if rest == "zfzf":
            return ("pon", None, "zf")
        if rest == "ypyp":
            return ("pon", None, "yp")
        if rest == "kfkf":
            return ("pon", None, "kf")
        tiles = _split_tiles(rest)
        if len(tiles) >= 2:
            return ("pon", tiles, None)
    return None


def player_could_satisfy_call_area_constraints(player_state, oya: int, constraints: List[str]) -> bool:
    """
    粗略检查该玩家是否可能满足 call_area_constraints（不按巡目过滤）。
    用于玩家级预过滤：若连「总量」都不够，则无需遍历舍牌。
    返回 True 表示有可能满足（需在具体舍牌巡目再次精确检查）。
    """
    if not constraints:
        return True
    calls = getattr(player_state, "calls", []) or []
    round_num = getattr(player_state, "round_num", 0)
    player_id = getattr(player_state, "player_id", 0)
    jikaze_name = MjlogParser.get_jikaze(player_id, oya, round_num)
    jikaze_z = _wind_to_z(jikaze_name)
    field = round_num // 4
    bakaze_name = ["东", "南", "西", "北"][field]
    bakaze_z = _wind_to_z(bakaze_name)
    yakuhai_set = {jikaze_z, bakaze_z, "5z", "6z", "7z"}
    kyokuze_z = [_wind_to_z(w) for w in MjlogParser.get_kyokuze_list(player_id, oya, round_num)]

    used = set()
    for raw in constraints[:4]:
        spec = parse_call_area_constraint(raw)
        if not spec:
            continue
        ev_type, tiles, placeholder = spec
        found = False
        for i, c in enumerate(calls):
            if i in used:
                continue
            if getattr(c, "call_type", None) != ev_type:
                continue
            got = getattr(c, "consumed", []) or []
            got_z = [_honor_tile_to_z(t) for t in got[:2]] if len(got) >= 2 else []
            if placeholder == "zf":
                if len(got_z) >= 2 and got_z[0] == got_z[1] == jikaze_z:
                    found = True
            elif placeholder == "yp":
                if len(got_z) >= 2 and all(t in yakuhai_set for t in got_z):
                    found = True
            elif placeholder == "kf":
                if len(got_z) >= 2 and got_z[0] == got_z[1] and got_z[0] in kyokuze_z:
                    found = True
            elif tiles:
                if len(got) == len(tiles) and sorted(got) == sorted(tiles):
                    found = True
            if found:
                used.add(i)
                break
        if not found:
            return False
    return True


def round_could_satisfy_call_constraints(
    round_players: list,
    call_constraint: Optional[str],
    call_area_constraints: Optional[List[str]],
    oya: int,
) -> bool:
    """
    局级预过滤：call_constraint 与 call_area_constraints 是否有任何玩家可能满足。
    - call_constraint "has_call": 至少一人有副露
    - call_constraint "no_call": 至少一人无副露
    - call_area_constraints: 至少一人可能满足（总量足够，不按巡目）
    """
    if call_constraint and call_constraint != "any":
        any_has_call = any(
            len(getattr(p, "calls", []) or []) > 0 for p in round_players
        )
        if call_constraint == "has_call" and not any_has_call:
            return False
        if call_constraint == "no_call" and all(
            len(getattr(p, "calls", []) or []) > 0 for p in round_players
        ):
            return False
    if call_area_constraints:
        if not any(
            player_could_satisfy_call_area_constraints(p, oya, call_area_constraints)
            for p in round_players
        ):
            return False
    return True


def _find_yp_call_index(
    calls: list, ev_type: str, yakuhai_set: set, exclude_indices: set
) -> Optional[int]:
    """找到首个役牌碰的下标，用于一对一匹配。consumed 统一为 1z-7z 后与 yakuhai_set 比较"""
    for i, c in enumerate(calls):
        if i in exclude_indices:
            continue
        if getattr(c, "call_type", None) != ev_type:
            continue
        got = getattr(c, "consumed", []) or []
        got_z = [_honor_tile_to_z(t) for t in got[:2]]
        if len(got_z) >= 2 and all(t in yakuhai_set for t in got_z):
            return i
    return None


def _find_kf_call_index(
    calls: list, ev_type: str, kyokuze_z: List[str], exclude_indices: set
) -> Optional[int]:
    """找到首个客风碰的下标"""
    for i, c in enumerate(calls):
        if i in exclude_indices:
            continue
        if getattr(c, "call_type", None) != ev_type:
            continue
        got = getattr(c, "consumed", []) or []
        if len(got) >= 2 and got[0] == got[1] and got[0] in kyokuze_z:
            return i
    return None


def player_satisfies_call_area_constraints(
    player_state,
    round_players: list,
    oya: int,
    constraints: List[str],
    current_discard_turn: Optional[int] = None,
) -> bool:
    """
    检查该玩家是否满足所有副露区域约束（AND 关系）。
    constraints: 如 ["pypyp", "pypyp", "4mc3m5m"]，最多 4 个。
    多约束采用一对一匹配：每个约束必须匹配不同的副露，如两个 pypyp 需两个不同的役牌碰。
    current_discard_turn: 当前舍牌巡目；仅考虑 from_discard_turn <= current_discard_turn 的副露
        （即该巡舍牌前已完成的副露）。None 表示不按巡目过滤。
    """
    if not constraints:
        return True
    all_calls = getattr(player_state, "calls", []) or []
    if current_discard_turn is not None:
        calls = [c for c in all_calls if getattr(c, "from_discard_turn", 1) <= current_discard_turn]
    else:
        calls = all_calls
    round_num = getattr(player_state, "round_num", 0)
    player_id = getattr(player_state, "player_id", 0)
    jikaze_name = MjlogParser.get_jikaze(player_id, oya, round_num)
    jikaze_z = _wind_to_z(jikaze_name)
    field = round_num // 4
    bakaze_name = ["东", "南", "西", "北"][field]
    bakaze_z = _wind_to_z(bakaze_name)
    yakuhai_set = {jikaze_z, bakaze_z, "5z", "6z", "7z"}
    kyokuze_z = [_wind_to_z(w) for w in MjlogParser.get_kyokuze_list(player_id, oya, round_num)]

    used_indices: set = set()

    for raw in constraints[:4]:
        spec = parse_call_area_constraint(raw)
        if not spec:
            continue
        ev_type, tiles, placeholder = spec
        if placeholder == "zf":
            want = [jikaze_z, jikaze_z]
            idx = _find_call_index_with_consumed(calls, ev_type, want, tuple(used_indices))
            if idx is None:
                return False
            used_indices.add(idx)
        elif placeholder == "yp":
            idx = _find_yp_call_index(calls, ev_type, yakuhai_set, used_indices)
            if idx is None:
                return False
            used_indices.add(idx)
        elif placeholder == "kf":
            idx = _find_kf_call_index(calls, ev_type, kyokuze_z, used_indices)
            if idx is None:
                return False
            used_indices.add(idx)
        elif tiles:
            idx = _find_call_index_with_consumed(calls, ev_type, tiles, tuple(used_indices))
            if idx is None:
                return False
            used_indices.add(idx)
    return True


def _split_tiles(s: str) -> List[str]:
    """将 6s8s、3m5m、1z1z 等拆成 ['6s','8s'], ['3m','5m'], ['1z','1z']"""
    out = []
    i = 0
    while i < len(s):
        if i + 1 < len(s) and s[i + 1] in "mps":
            out.append(s[i : i + 2])
            i += 2
        elif i + 1 < len(s) and s[i + 1] == "z" and s[i].isdigit():
            out.append(s[i : i + 2])
            i += 2
        else:
            i += 1
    return out


# 搜索规格：单项 (type, tiles) 表示必须匹配；列表 [(type,tiles),...] 表示 OR（匹配其一即可）
ConsumedSearchItem = Union[Tuple[str, List[str]], List[Tuple[str, List[str]]]]


def pattern_has_riichi(query_pattern: List[str]) -> bool:
    """检查舍牌模式是否含立直宣言牌(r)"""
    for elem in query_pattern:
        s = (elem or "").strip()
        if s.endswith("r") and len(s) >= 2:
            return True
    return False


def get_consumed_search_patterns(query_pattern: List[str]) -> List[ConsumedSearchItem]:
    """
    从含吃碰的模式提取 consumed 的搜索规格，用于直接字符串匹配。
    规律：c/p 后面的字符即 consumed 内容，直接对应 JSON 中 "consumed": ["x","y"]。

    例：c6s8s → ["consumed": ["6s","8s"]]；p1z1z → ["consumed": ["1z","1z"]]
    pkfkf（客风碰）：生成 ["1z","1z"],["2z","2z"],["3z","3z"],["4z","4z"] 的 OR 搜索，
    预过滤通过后再在匹配阶段判断该 ?z 是否为该玩家的客风。
    """
    result: List[ConsumedSearchItem] = []
    for elem in query_pattern:
        s = elem.strip()
        if not s or len(s) < 2:
            continue
        s_lower = s.lower()
        if "c" in s_lower and ("m" in s_lower or "p" in s_lower or "s" in s_lower):
            if s_lower.startswith("c"):
                consumed_str = s[1:]
            else:
                idx = s_lower.index("c")
                consumed_str = s[idx + 1 :]
            tiles = _split_tiles(consumed_str)
            if len(tiles) >= 2:
                result.append(("chii", tiles[:2]))
        elif s_lower.startswith("p") and len(s) >= 2:
            rest = s_lower[1:]
            if rest == "kfkf":
                result.append([
                    ("pon", ["1z", "1z"]),
                    ("pon", ["2z", "2z"]),
                    ("pon", ["3z", "3z"]),
                    ("pon", ["4z", "4z"]),
                ])
                continue
            tiles = _split_tiles(rest)
            if len(tiles) >= 2 and "z" in rest:
                result.append(("pon", tiles[:2]))
    return result


def _single_consumed_matches(raw: str, ev_type: str, tiles: List[str]) -> bool:
    """单条 consumed 是否在 raw 中匹配"""
    if len(tiles) < 2 or ev_type not in raw:
        return False
    t1, t2 = tiles[0], tiles[1]
    escaped1, escaped2 = re.escape(t1), re.escape(t2)
    pattern_ord = rf'"consumed"\s*:\s*\[\s*"{escaped1}"\s*,\s*"{escaped2}"\s*\]'
    pattern_rev = rf'"consumed"\s*:\s*\[\s*"{escaped2}"\s*,\s*"{escaped1}"\s*\]'
    return bool(re.search(pattern_ord, raw) or re.search(pattern_rev, raw))


def _consumed_eq(a_list, b_list) -> bool:
    """consumed 精确相等（0p 与 5p 视为不同牌）；字牌统一为 1z-7z 后比较以兼容 东/西 等格式"""
    if len(a_list) != len(b_list):
        return False
    a_norm = [_honor_tile_to_z(t) for t in a_list]
    b_norm = [_honor_tile_to_z(t) for t in b_list]
    sa, sb = sorted(a_norm), sorted(b_norm)
    return all(ta == tb for ta, tb in zip(sa, sb))


def _calls_have_consumed(calls: list, ev_type: str, want_tiles: List[str]) -> bool:
    """检查 calls 中是否有 ev_type 类型的副露且 consumed 精确匹配"""
    return _find_call_index_with_consumed(calls, ev_type, want_tiles, exclude_indices=()) is not None


def _find_call_index_with_consumed(
    calls: list, ev_type: str, want_tiles: List[str], exclude_indices: Tuple[int, ...] = ()
) -> Optional[int]:
    """返回首个匹配的 call 下标，未找到返回 None。用于一对一匹配。"""
    for i, c in enumerate(calls):
        if i in exclude_indices:
            continue
        if getattr(c, "call_type", None) != ev_type:
            continue
        got = getattr(c, "consumed", []) or []
        if isinstance(got, list) and _consumed_eq(want_tiles, got):
            return i
    return None


def player_has_matching_consumed(player_state, search_patterns: List[ConsumedSearchItem]) -> bool:
    """
    检查该玩家是否发生过 search_patterns 中的副露。
    多舍牌模式下必须用此检查：仅当「本玩家」有该副露时，其舍牌才计入该模式；
    否则会误将 A 玩家的舍牌计入 B 玩家副露的模式（如 c0p4p 与 c0p6p 同局时互相污染）。
    """
    if not search_patterns:
        return True
    calls = getattr(player_state, "calls", []) or []
    for item in search_patterns:
        if isinstance(item, tuple):
            ev_type, tiles = item
            if not _calls_have_consumed(calls, ev_type, tiles):
                return False
        else:
            if not any(
                _calls_have_consumed(calls, ev_type, tiles)
                for ev_type, tiles in item
            ):
                return False
    return True


def round_has_matching_consumed(round_players: list, search_patterns: List[ConsumedSearchItem]) -> bool:
    """
    检查该小局内是否有任一玩家发生过 search_patterns 中的副露。
    用于在局级过滤：仅当本局确有该副露时才进行匹配，避免半庄级预过滤误通过。
    """
    if not search_patterns or not round_players:
        return True
    for item in search_patterns:
        if isinstance(item, tuple):
            ev_type, tiles = item
            if not any(
                _calls_have_consumed(getattr(p, "calls", []) or [], ev_type, tiles)
                for p in round_players
            ):
                return False
        else:
            # OR 组：任一 (ev_type, tiles) 被任一玩家满足即可
            if not any(
                _calls_have_consumed(getattr(p, "calls", []) or [], ev_type, tiles)
                for ev_type, tiles in item
                for p in round_players
            ):
                return False
    return True


def log_contains_consumed(raw: str, search_patterns: List[ConsumedSearchItem]) -> bool:
    """
    直接字符串搜索：牌谱 raw 中是否包含与 search_patterns 匹配的 consumed。
    - 单项 (type, tiles)：必须匹配
    - 列表 [(type,tiles),...]：OR 组，匹配其一即可
    所有项都满足时返回 True。
    """
    if not search_patterns or not raw:
        return True
    for item in search_patterns:
        if isinstance(item, tuple):
            ev_type, tiles = item
            if not _single_consumed_matches(raw, ev_type, tiles):
                return False
        else:
            if not any(_single_consumed_matches(raw, ev_type, tiles) for ev_type, tiles in item):
                return False
    return True


def _is_honor_tile(tile_str: str) -> bool:
    """判断是否为字牌（中文名或 1z-7z）"""
    if tile_str in HONOR_NAMES:
        return True
    if len(tile_str) == 2 and tile_str[0].isdigit() and tile_str[1] == "z":
        return 1 <= int(tile_str[0]) <= 7
    return False


def _tile_base_eq(a: str, b: str) -> bool:
    """两牌是否同种。已弃用：consumed/舍牌匹配等均使用精确匹配，0m/0p/0s 与 5m/5p/5s 视为不同牌。"""
    return a == b


def _parse_call_element(s: str) -> Optional[Tuple[str, bool]]:
    """
    解析吃/碰元素。返回 (占位符, False) 或 None。
    吃：任意 (牌)c(牌)(牌) 或 c(牌)(牌)，如 4mc3m5m、1sc2s3s、c5m6m。
    碰：p(牌)(牌) 如 p1z1z、pkfkf。
    """
    s = s.strip()
    if not s or len(s) < 2:
        return None
    s_lower = s.lower()
    # 吃：4mc3m5m 或 c5m6m
    if "c" in s_lower and ("m" in s_lower or "p" in s_lower or "s" in s_lower):
        if s_lower.startswith("c"):
            return (f"{CALL_PREFIX}c:{s[1:]}", False)  # c5m6m
        idx = s_lower.index("c")
        if idx > 0 and idx < len(s) - 1:
            return (f"{CALL_PREFIX}c:{s}", False)  # 4mc3m5m
    # 碰：p1z1z 或 pkfkf
    if s_lower.startswith("p") and len(s) >= 2:
        rest = s_lower[1:]
        if rest == "kfkf":
            return (f"{CALL_PREFIX}p:kf", False)
        if len(rest) >= 4 and rest[0].isdigit() and rest[1] == "z":
            return (f"{CALL_PREFIX}p:{rest}", False)  # p1z1z -> @p:1z1z
    return None


def parse_discard_element(s: str) -> Tuple[str, bool]:
    """
    解析舍牌模式元素。
    "3mt" -> ("3m", True)  摸切
    "3m" -> ("3m", False) 手切
    "0m","0p","0s" -> 赤5m/赤5p/赤5s
    "4mc3m5m" -> 用 3m5m 吃 4m；"c5m6m" -> 用 56m 吃 4m 或 7m
    "p1z1z" -> 用两个东碰；"pkfkf" -> 客风碰
    "z" -> ("z", False) 任意字牌；"zf"/"kf" 等
    """
    s = s.strip()
    if not s:
        return ("*", False)
    if s == "*":
        return ("*", False)
    if s == "$":
        return ("$", False)  # 任意一张手切
    # 吃/碰
    call = _parse_call_element(s)
    if call is not None:
        return call
    if s in HONOR_PLACEHOLDERS:
        if s == "zt":
            return ("z", True)
        return (s, False)
    if s in RED_FIVES:
        return (s, False)
    # r 后缀：立直宣言牌（该牌为打出时宣告立直，必为摸切；与 c/p 不可同时出现）
    if s.endswith("r") and len(s) >= 2:
        base = s[:-1]
        if base in RED_FIVES or (len(base) >= 2 and base[-1] in "mps" and (base[0].isdigit() or base in RED_FIVES)):
            return (f"{CALL_PREFIX}r:{base}", True)  # 立直宣言牌必为摸切
        if base in HONOR_PLACEHOLDERS:
            return (f"{CALL_PREFIX}r:{base}", True)
    if s.endswith("t") and len(s) >= 2:
        base = s[:-1]
        if base in HONOR_PLACEHOLDERS:
            if base == "z":
                return ("z", True)
            return (base, True)
        if base in RED_FIVES:
            return (base, True)  # 赤五摸切
        return (base, True)  # 普通牌摸切
    return (s, False)


def parse_target_tiles(target_str: str) -> Tuple[List[str], bool]:
    """
    解析目标牌字符串。
    "2m" -> (["2m"], False) 单张
    "1m3m" 或 "1m-3m" -> (["1m","3m"], True) 搭子/组合
    "13m" -> (["1m","3m"], True) 简写：多数字+花色，花色应用于所有数字
    
    Returns:
        (tile_list, is_combo): is_combo=True 表示多张牌组合（搭子）
    """
    s = target_str.strip().replace(" ", "").replace(",", "-")
    honors = "东南西北白发中"

    # 简写格式：如 "13m"、"46p" = 多个数字 + 一个花色
    if len(s) >= 2 and s[-1] in "mps" and all(c.isdigit() for c in s[:-1]):
        digits, suit = s[:-1], s[-1]
        tiles = [d + suit for d in digits]
        return (tiles, len(tiles) > 1) if tiles else (["2m"], False)

    z_to_honor = {"1z": "东", "2z": "南", "3z": "西", "4z": "北", "5z": "白", "6z": "发", "7z": "中"}
    tiles = []
    i = 0
    while i < len(s):
        if s[i] == "-":
            i += 1
            continue
        if i + 1 < len(s) and s[i].isdigit() and s[i + 1] in "mps":
            tiles.append(s[i : i + 2])
            i += 2
        elif i + 1 < len(s) and s[i].isdigit() and s[i + 1] == "z" and 1 <= int(s[i]) <= 7:
            tiles.append(z_to_honor.get(s[i:i+2], s[i:i+2]))  # 统一为中文，与 tile_to_string 一致
            i += 2
        elif s[i] in honors:
            tiles.append(s[i])
            i += 1
        else:
            i += 1
    is_combo = len(tiles) > 1
    return (tiles, is_combo) if tiles else (["2m"], False)  # fallback


def mirror_number(n: int) -> int:
    """镜像数字: 1↔9, 2↔8, 3↔7, 4↔6, 5↔5"""
    return 10 - n


def _change_suit(tile: str, new_suit: str) -> str:
    """将数牌的花色改为 new_suit，字牌、*、$ 与吃碰占位符不变；@r: 后牌参与花色变换"""
    if tile in ("*", "$"):
        return tile
    if tile.startswith("@r:"):
        return f"@r:{_change_suit(tile[3:], new_suit)}"
    if tile.startswith(CALL_PREFIX):
        return tile
    if len(tile) >= 2 and tile[-1] in "mps" and (tile[0].isdigit() or tile in RED_FIVES):
        return f"{tile[0]}{new_suit}"  # 0m->0s, 5m->5s
    return tile


def _apply_suit(tiles: List[str], suit: str) -> List[str]:
    """将数牌统一改为指定花色，*、$ 通配符不变"""
    return [_change_suit(t, suit) if t not in ("*", "$") else t for t in tiles]


def _apply_mirror(tiles: List[str]) -> List[str]:
    """对数牌应用镜像，字牌、*、$、赤五、吃碰占位符不变；@r: 后牌参与镜像"""
    result = []
    for t in tiles:
        if t in ("*", "$"):
            result.append(t)
        elif t.startswith("@r:"):
            result.append(f"@r:{_apply_mirror([t[3:]])[0]}")
        elif t.startswith(CALL_PREFIX):
            result.append(t)
        elif t in RED_FIVES:
            result.append(t)  # 0m/0p/0s 视为 5，镜像仍为 5
        elif len(t) >= 2 and t[-1] in "mps" and t[0].isdigit():
            n = int(t[0])
            result.append(f"{mirror_number(n)}{t[-1]}")
        else:
            result.append(t)
    return result


def _apply_suit_to_pattern(
    pattern: List[Tuple[str, bool]], suit: str
) -> List[Tuple[str, bool]]:
    """对模式应用花色变换，保留摸切标记。赤五参与：0m→0s 等。*、$ 不变。"""
    return [
        (_change_suit(tile, suit) if tile not in ("*", "$") else tile, is_tsumogiri)
        for tile, is_tsumogiri in pattern
    ]


def _apply_suit_to_pattern_dual(
    pattern: List[Tuple[str, bool]], suit_num: str, suit_red: str
) -> List[Tuple[str, bool]]:
    """数牌与赤五独立花色：非赤五用 suit_num，赤五用 suit_red。用于 7s-0m-9s 等 9+9 变体。"""
    result = []
    for tile, is_tsumogiri in pattern:
        if tile in ("*", "$"):
            result.append((tile, is_tsumogiri))
        elif tile.startswith("@r:"):
            sub = tile[3:]
            if sub in RED_FIVES:
                result.append((f"@r:0{suit_red}", is_tsumogiri))
            elif len(sub) >= 2 and sub[-1] in "mps" and sub[0].isdigit():
                result.append((f"@r:{sub[0]}{suit_num}", is_tsumogiri))
            else:
                result.append((tile, is_tsumogiri))
        elif tile.startswith(CALL_PREFIX):
            result.append((tile, is_tsumogiri))
        elif tile in RED_FIVES:
            result.append((f"0{suit_red}", is_tsumogiri))
        elif len(tile) >= 2 and tile[-1] in "mps" and tile[0].isdigit():
            result.append((f"{tile[0]}{suit_num}", is_tsumogiri))
        else:
            result.append((tile, is_tsumogiri))
    return result


def _apply_mirror_to_pattern(pattern: List[Tuple[str, bool]]) -> List[Tuple[str, bool]]:
    """对模式应用镜像变换，保留摸切标记；*、$、赤五与吃碰占位不变；@r: 后牌参与镜像"""
    result = []
    for tile, is_tsumogiri in pattern:
        if tile in ("*", "$"):
            result.append((tile, is_tsumogiri))
        elif tile.startswith("@r:"):
            sub = tile[3:]
            if sub in RED_FIVES:
                result.append((tile, is_tsumogiri))
            elif len(sub) >= 2 and sub[-1] in "mps" and sub[0].isdigit():
                result.append((f"@r:{mirror_number(int(sub[0]))}{sub[-1]}", is_tsumogiri))
            else:
                result.append((tile, is_tsumogiri))
        elif tile.startswith(CALL_PREFIX):
            result.append((tile, is_tsumogiri))
        elif tile in RED_FIVES:
            result.append((tile, is_tsumogiri))
        elif len(tile) >= 2 and tile[-1] in "mps" and tile[0].isdigit():
            n = int(tile[0])
            result.append((f"{mirror_number(n)}{tile[-1]}", is_tsumogiri))
        else:
            result.append((tile, is_tsumogiri))
    return result


def _transform_tile_for_constraint(tile_str: str, suit: str, mirror: bool) -> str:
    """转换可见约束中的牌"""
    if len(tile_str) >= 2 and tile_str[-1] in 'mps' and tile_str[0].isdigit():
        n = int(tile_str[0])
        if mirror:
            n = mirror_number(n)
        return f"{n}{suit}"
    return tile_str


def _transform_visible_constraints(
    visible_constraints: Optional[Dict[str, Tuple[int, int]]],
    suit: str,
    mirror: bool
) -> Dict[str, Tuple[int, int]]:
    """转换可见约束到指定花色和镜像"""
    if not visible_constraints:
        return {}
    result = {}
    for tile_str, (min_count, max_count) in visible_constraints.items():
        new_tile = _transform_tile_for_constraint(tile_str, suit, mirror)
        result[new_tile] = (min_count, max_count)
    return result


def _expand_pure_honor_pattern(parsed: List[Tuple[str, bool]]) -> List[List[Tuple[str, bool]]]:
    """
    展开纯字牌模式为具体变体。
    - zt-zt: 49 种 (7×7)
    - z1-z2: 42 种 (7×6)
    - z1-z2-z3: 210 种 (7×6×5)
    - 含 zf/kf 的不展开，保留占位符（1 种）
    """
    if any(t in ("zf", "kf", "kf1", "kf2", "kf3") for t, _ in parsed):
        return [parsed]

    distinct_indices = [i for i, (t, _) in enumerate(parsed) if t in ("z1", "z2", "z3")]
    independent_indices = [i for i, (t, _) in enumerate(parsed) if t in ("z", "zt")]

    if not distinct_indices and not independent_indices:
        return [parsed]

    result = []
    for distinct_vals in permutations(HONOR_NAMES, len(distinct_indices)):
        for indep_vals in product(HONOR_NAMES, repeat=len(independent_indices)):
            variant = list(parsed)
            for idx, h in zip(distinct_indices, distinct_vals):
                variant[idx] = (h, variant[idx][1])
            for idx, h in zip(independent_indices, indep_vals):
                variant[idx] = (h, variant[idx][1])
            result.append(variant)
    return result


def generate_equivalent_variants(
    discard_pattern: List[str],
    target_tile: str,
    visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None
) -> List[Dict]:
    """
    根据舍牌序列、目标牌、可见牌约束，生成所有等价变体。

    - 仅普通数牌：6 个变体 = 3 花色 × 2（原始/镜像）。
    - 同时含普通数牌与赤五（0m/0p/0s）：18 个变体 = 数牌花色×赤五花色 各 3 × 2（镜像）。
      例：7s-0m-9s 等价于 7s-0p-9s、7m-0s-9m、7m-0p-9s、7p-0m-9p 等及镜像；赤五参与花色变换且与数牌独立等价。
    """
    if not discard_pattern:
        return []

    parsed_pattern = [parse_discard_element(p) for p in discard_pattern]
    target_tiles, is_combo = parse_target_tiles(target_tile)

    def _transform_target(tiles: List[str], suit: str, mirror: bool) -> Union[str, List[str]]:
        transformed = [_transform_tile_for_constraint(t, suit, mirror) for t in tiles]
        return transformed[0] if len(transformed) == 1 else transformed

    def _is_number_tile(tile: str) -> bool:
        return len(tile) >= 2 and tile[-1] in 'mps' and tile[0].isdigit()

    def _is_red_five(tile: str) -> bool:
        return tile in RED_FIVES

    # 检查是否包含数牌、赤五、吃、碰、立直宣言
    has_number = any(_is_number_tile(tile) for tile, _ in parsed_pattern) or any(
        tile.startswith("@r:") and _is_number_tile(tile[3:]) for tile, _ in parsed_pattern
    )
    has_red_five = any(_is_red_five(tile) for tile, _ in parsed_pattern) or any(
        tile.startswith("@r:") and tile[3:] in RED_FIVES for tile, _ in parsed_pattern
    )
    has_chi = any(tile.startswith("@c:") for tile, _ in parsed_pattern)
    has_pon = any(tile.startswith("@p:") for tile, _ in parsed_pattern)
    has_riichi = any(tile.startswith("@r:") for tile, _ in parsed_pattern)
    # 立直宣言牌(r)与副露(c/p)互斥：立直玩家不可能有吃碰
    if has_riichi and (has_chi or has_pon):
        raise ValueError("舍牌模式不能同时包含立直宣言(r)与吃/碰(c/p)，立直玩家不可副露")
    # 数牌碰：@p: 后的内容含 m/p/s（如将来支持 p5m5m）；@p:1z1z、@p:kf 为字牌碰
    has_number_pon = any(
        tile.startswith("@p:") and any(c in tile[3:] for c in "mps")
        for tile, _ in parsed_pattern
    )
    # 含吃或数牌碰时不生成等价变体（只保留原模式 1 个）；仅碰字牌时仍可生成变体
    no_suit_mirror_variants = has_chi or (has_pon and has_number_pon)

    # 纯字牌模式：展开或保留占位符
    if not has_number:
        honor_variants = _expand_pure_honor_pattern(parsed_pattern)
        t = target_tiles[0] if len(target_tiles) == 1 else target_tiles
        return [
            {
                "discard": p,
                "target": t,
                "visible_constraints": dict(visible_constraints) if visible_constraints else {},
                "is_combo": is_combo
            }
            for p in honor_variants
        ]

    # 含吃或数牌碰：不生成花色/镜像变体，仅 1 个变体（原模式）
    if no_suit_mirror_variants:
        t = target_tiles[0] if len(target_tiles) == 1 else target_tiles
        return [
            {
                "discard": list(parsed_pattern),
                "target": t,
                "visible_constraints": dict(visible_constraints) if visible_constraints else {},
                "is_combo": is_combo,
            }
        ]

    variants = []
    suits = ['s', 'm', 'p']

    # 同时含普通数牌与赤五：数牌花色 × 赤五花色 互相等价，生成 9 + 9(镜像) = 18 变体
    if has_number and has_red_five:
        for suit_num in suits:
            for suit_red in suits:
                discard_orig = _apply_suit_to_pattern_dual(parsed_pattern, suit_num, suit_red)
                target_orig = _transform_target(target_tiles, suit_num, False)
                visible_orig = _transform_visible_constraints(visible_constraints, suit_num, False)
                variants.append({
                    "discard": discard_orig,
                    "target": target_orig,
                    "visible_constraints": visible_orig,
                    "is_combo": is_combo
                })
                discard_mir = _apply_mirror_to_pattern(
                    _apply_suit_to_pattern_dual(parsed_pattern, suit_num, suit_red)
                )
                target_mir = _transform_target(target_tiles, suit_num, True)
                visible_mir = _transform_visible_constraints(visible_constraints, suit_num, True)
                variants.append({
                    "discard": discard_mir,
                    "target": target_mir,
                    "visible_constraints": visible_mir,
                    "is_combo": is_combo
                })
        return variants

    # 无数牌+赤五混合：原逻辑 3 花色 × 2 镜像 = 6 变体
    for suit in suits:
        discard_orig = _apply_suit_to_pattern(parsed_pattern, suit)
        target_orig = _transform_target(target_tiles, suit, False)
        visible_orig = _transform_visible_constraints(visible_constraints, suit, False)
        variants.append({
            "discard": discard_orig,
            "target": target_orig,
            "visible_constraints": visible_orig,
            "is_combo": is_combo
        })
        discard_mir = _apply_mirror_to_pattern(_apply_suit_to_pattern(parsed_pattern, suit))
        target_mir = _transform_target(target_tiles, suit, True)
        visible_mir = _transform_visible_constraints(visible_constraints, suit, True)
        variants.append({
            "discard": discard_mir,
            "target": target_mir,
            "visible_constraints": visible_mir,
            "is_combo": is_combo
        })
    return variants


def get_acceptable_last_tiles(variants: List[Dict]) -> frozenset:
    """
    获取所有变体模式末尾所需的牌（用于快速排除）。
    若当前舍牌牌面不在此集合中，可跳过匹配。
    字牌占位符 z/zt/zf/kf/z1/z2/z3：将 7 种字牌都加入，避免误排除。
    """
    last_tiles = set()
    for v in variants:
        pattern = v["discard"]
        for i in range(len(pattern) - 1, -1, -1):
            elem = pattern[i]
            tile = elem[0] if isinstance(elem, tuple) else elem
            if tile.startswith(CALL_PREFIX):
                if tile.startswith("@r:"):
                    last_tiles.add(tile[3:])
                continue  # 吃碰占位不参与“末尾牌”判定
            if tile not in ("*", "$"):
                if tile in HONOR_PLACEHOLDERS:
                    last_tiles.update(HONOR_NAMES)
                else:
                    last_tiles.add(tile)
                break
    return frozenset(last_tiles)


def normalize_discard_pattern(tiles: List[str]) -> str:
    """
    已废弃：仅保留供 process_logs 等旧脚本兼容。
    返回原始序列的字符串形式，不做等价标准化。
    新代码请使用 generate_equivalent_variants + match_discard_to_variant。
    """
    import warnings
    warnings.warn("normalize_discard_pattern 已废弃，请使用 generate_equivalent_variants", DeprecationWarning)
    return "-".join(tiles) if tiles else ""


def map_target_tile(query_pattern: List[str], actual_pattern: List[str], target_tile: str) -> str:
    """
    已废弃：仅保留供旧代码兼容。
    当 query_pattern 与 actual_pattern 等价时映射目标牌。
    新代码请使用 generate_equivalent_variants + match_discard_to_variant。
    """
    full_discards = [(t, False) for t in actual_pattern]  # 假定全为手切
    matched = match_discard_to_variant(full_discards, generate_equivalent_variants(query_pattern, target_tile, None))
    return matched["target"] if matched else target_tile


def _consumed_matches_call(pat_tile: str, calls: list, context: Optional[Dict] = None,
                           require_immediate: bool = False) -> bool:
    """
    检查 pat_tile (@c:xyz 或 @p:xyz) 是否与 calls 中某次副露匹配。
    仅考虑在该舍牌之前发生的副露（context["current_discard_turn"]）。
    require_immediate: 若为 True，要求该舍牌必须为副露后立即打出的那张（from_discard_turn == current_turn），
       以确保巡目/立直等场况约束正确作用（如 c0p6p-$ 中 $ 必须是吃完后立刻打出的牌）。
    @c:0p6p -> 需有 chii 且 consumed 精确为 ["0p","6p"]（0p 与 5p 视为不同牌）
    @p:1z1z -> 需有 pon 且 consumed 含 1z、1z
    """
    if not pat_tile.startswith(CALL_PREFIX) or ":" not in pat_tile:
        return False
    rest = pat_tile[len(CALL_PREFIX):]
    if rest.startswith("c:"):
        want_type, want_tiles = "chii", _split_tiles(rest[2:])
    elif rest.startswith("p:"):
        want_type, want_tiles = "pon", _split_tiles(rest[2:])
    else:
        return False
    if len(want_tiles) < 2:
        return False

    current_turn = (context or {}).get("current_discard_turn")

    def consumed_eq(a_list, b_list):
        if len(a_list) != len(b_list):
            return False
        sa, sb = sorted(a_list), sorted(b_list)
        return all(ta == tb for ta, tb in zip(sa, sb))  # 0p 与 5p 视为不同牌

    for c in calls:
        # 仅考虑在打这张牌之前已发生的副露
        if current_turn is not None:
            from_turn = getattr(c, "from_discard_turn", 1)
            if from_turn > current_turn:
                continue
            # 若要求“紧接副露”，则当前舍牌必须就是副露后打出的那张
            if require_immediate and from_turn != current_turn:
                continue
        if getattr(c, "call_type", None) != want_type:
            continue
        got = getattr(c, "consumed", []) or []
        if not isinstance(got, list):
            got = list(got) if got else []
        if consumed_eq(want_tiles, got):
            return True
    return False


def _match_pattern_at_end(
    full_discards: List[Tuple[str, bool]],
    pattern: List[Tuple[str, bool]],
    context: Optional[Dict] = None,
) -> bool:
    """
    检查完整舍牌序列末尾是否匹配模式。

    支持字牌占位符：
    - z: 任意字牌
    - zf: 自风（需 context["jikaze"]）
    - kf: 客风（需 context["kyokuze_list"]）
    - z1,z2,z3: 互不相同的字牌；kf1,kf2,kf3: 互不相同的客风（需 context）

    Args:
        full_discards: [(牌字符串, 是否摸切), ...]，牌为中文或 1m 等
        pattern: [(牌或占位符, 是否要求摸切), ...]
        context: {"jikaze": str, "kyokuze_list": List[str]}，用于 zf/kf
    """
    if not full_discards or not pattern:
        return False

    ctx = context or {}
    jikaze = ctx.get("jikaze")
    kyokuze_list = ctx.get("kyokuze_list") or ()
    calls = ctx.get("calls") or ()

    d_idx = len(full_discards) - 1
    p_idx = len(pattern) - 1
    matched_honors: set = set()
    consumed_any_discard = False  # 用于 z1,z2,z3 的“互不相同”约束

    while p_idx >= 0 and d_idx >= 0:
        tile_str, is_tsumogiri = full_discards[d_idx]
        pat_tile, pat_want_tsumogiri = pattern[p_idx]

        # 立直宣言牌 @r:：消耗一张舍牌，须为摸切且该舍牌为立直宣言
        if pat_tile.startswith("@r:"):
            want_tile = pat_tile[3:]
            riichi_flags = ctx.get("discard_riichi_flags") or []
            if d_idx >= len(riichi_flags) or not riichi_flags[d_idx]:
                return False
            if tile_str != want_tile or not is_tsumogiri:
                return False
            consumed_any_discard = True
            d_idx -= 1
            p_idx -= 1
            continue

        # 吃/碰占位：不消耗舍牌，只跳过该模式元素
        if pat_tile.startswith(CALL_PREFIX):
            # @p:kf 客风碰：须校验该玩家有 客风 pon
            if pat_tile == f"{CALL_PREFIX}p:kf":
                if not kyokuze_list:
                    p_idx -= 1
                    continue
                next_elem = pattern[p_idx + 1] if p_idx + 1 < len(pattern) else None
                next_tile = next_elem[0] if next_elem else None
                require_immediate_kf = (
                    next_tile is not None
                    and next_tile != "*"
                    and not (next_tile.startswith(CALL_PREFIX) if isinstance(next_tile, str) else False)
                )
                has_kf_pon = False
                current_turn = ctx.get("current_discard_turn")
                for c in calls:
                    if current_turn is not None:
                        from_turn = getattr(c, "from_discard_turn", 1)
                        if from_turn > current_turn:
                            continue
                        if require_immediate_kf and from_turn != current_turn:
                            continue
                    if getattr(c, "call_type", None) == "pon":
                        pai_cn = Z_TO_WIND.get(getattr(c, "pai", ""))
                        if pai_cn and pai_cn in kyokuze_list:
                            has_kf_pon = True
                            break
                if not has_kf_pon:
                    return False
            else:
                # @c:xyz / @p:xyz：须校验 calls 中有对应吃/碰（且在该舍牌之前发生）
                # require_immediate: 若副露后紧跟 $ 或具体牌（无 * 隔开），则当前舍牌必须是副露后立刻打出的那张，
                #   以便巡目/立直等场况约束正确作用
                next_elem = pattern[p_idx + 1] if p_idx + 1 < len(pattern) else None
                next_tile = next_elem[0] if next_elem else None
                require_immediate = (
                    next_tile is not None
                    and next_tile != "*"
                    and not (next_tile.startswith(CALL_PREFIX) if isinstance(next_tile, str) else False)
                )
                if not _consumed_matches_call(pat_tile, calls, ctx, require_immediate=require_immediate):
                    return False
            p_idx -= 1
            continue

        if pat_tile == "*":
            if is_tsumogiri:
                consumed_any_discard = True
                d_idx -= 1
                continue
            else:
                p_idx -= 1
                continue

        # $ : 任意一张手切（吃/碰后的牌必为手切）
        if pat_tile == "$":
            if is_tsumogiri:
                return False  # 必须是手切
            consumed_any_discard = True
            d_idx -= 1
            p_idx -= 1
            continue

        # 摸切要求须一致
        if is_tsumogiri != pat_want_tsumogiri:
            return False

        # 字牌占位符匹配
        if pat_tile in ("z", "zt"):
            if tile_str not in HONOR_NAMES and not _is_honor_tile(tile_str):
                return False
            consumed_any_discard = True
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile == "zf":
            if jikaze is None or tile_str != jikaze:
                return False
            consumed_any_discard = True
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile == "kf":
            if not kyokuze_list or tile_str not in kyokuze_list:
                return False
            consumed_any_discard = True
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile in ("z1", "z2", "z3", "kf1", "kf2", "kf3"):
            if pat_tile.startswith("kf") and pat_tile != "kf":
                # kf1,kf2,kf3：客风，且互不相同
                if not kyokuze_list or tile_str not in kyokuze_list:
                    return False
                if tile_str in matched_honors:
                    return False
                matched_honors.add(tile_str)
            else:
                # z1,z2,z3：任意字牌，互不相同
                if tile_str not in HONOR_NAMES and not _is_honor_tile(tile_str):
                    return False
                if tile_str in matched_honors:
                    return False
                matched_honors.add(tile_str)
            consumed_any_discard = True
            d_idx -= 1
            p_idx -= 1
            continue

        # 具体牌：精确匹配（0m/0p/0s 与 5m/5p/5s 视为不同牌）
        if tile_str != pat_tile:
            return False
        consumed_any_discard = True
        d_idx -= 1
        p_idx -= 1

    if not consumed_any_discard:
        return False

    # 继续处理剩余的仅吃/碰元素（不消耗舍牌）
    while p_idx >= 0:
        pat_tile = pattern[p_idx][0]
        if pat_tile.startswith(CALL_PREFIX):
            if pat_tile == f"{CALL_PREFIX}p:kf":
                if not kyokuze_list:
                    p_idx -= 1
                    continue
                current_turn = ctx.get("current_discard_turn")
                has_kf_pon = False
                for c in calls:
                    if current_turn is not None:
                        from_turn = getattr(c, "from_discard_turn", 1)
                        if from_turn > current_turn:
                            continue
                    if getattr(c, "call_type", None) == "pon":
                        pai_cn = Z_TO_WIND.get(getattr(c, "pai", ""))
                        if pai_cn and pai_cn in kyokuze_list:
                            has_kf_pon = True
                            break
                if not has_kf_pon:
                    return False
            else:
                if not _consumed_matches_call(pat_tile, calls, ctx):
                    return False
            p_idx -= 1
            continue
        if pat_tile == "*":
            p_idx -= 1
            continue
        break

    while p_idx >= 0 and pattern[p_idx][0] == "*":
        p_idx -= 1

    return p_idx < 0


def match_discard_to_variant(
    full_discards: List[Tuple[str, bool]],
    variants: List[Dict],
    context: Optional[Dict] = None,
) -> Optional[Dict]:
    """
    检查完整舍牌序列末尾是否匹配任一变体。

    使用完整序列（含摸切），保证 [1m,3m] 要求 1m 与 3m 相邻；
    [3m,*,1m] 允许中间有任意摸切。
    字牌占位符 zf/kf 需 context: {"jikaze": str, "kyokuze_list": List[str]}。

    Args:
        full_discards: [(牌字符串, 是否摸切), ...]，按出牌顺序
        variants: generate_equivalent_variants 返回的变体列表
        context: 可选，含 jikaze、kyokuze_list，用于 zf/kf 匹配

    Returns:
        匹配到的变体，若都不匹配则返回 None
    """
    for v in variants:
        if _match_pattern_at_end(full_discards, v["discard"], context):
            return v
    return None


if __name__ == "__main__":
    # 测试等价变体生成
    print("=== 等价变体生成测试 ===\n")
    
    variants = generate_equivalent_variants(
        discard_pattern=["1s", "3s"],
        target_tile="2s",
        visible_constraints={"4s": (1, 1)}
    )
    
    print(f"输入: 舍牌 1s-3s, 目标 2s, 可见 4s 1枚")
    print(f"生成 {len(variants)} 个变体:\n")
    for i, v in enumerate(variants):
        discard_fmt = [(t + "t" if ts else t) for t, ts in v["discard"]]
        print(f"  {i+1}. discard={discard_fmt} target={v['target']} visible={v['visible_constraints']}")
    
    # 测试匹配（完整序列格式：(牌, 是否摸切)）
    print("\n=== 匹配测试（完整序列，[1m,3m] 要求相邻）===")
    test_cases = [
        # (完整舍牌序列, 描述)
        ([("1s", False), ("3s", False)], "1s-3s 相邻手切"),
        ([("4z", False), ("5z", False), ("9m", False), ("1m", False), ("8m", True), ("3m", False)],
         "4z,5z,9m,1m,8m(摸切),3m - 1m与3m中间有摸切，应不匹配"),
        ([("1m", False), ("3m", False)], "1m-3m 相邻手切"),
        ([("9m", False), ("7m", False)], "9m-7m（镜像）"),
    ]
    for full_discards, desc in test_cases:
        matched = match_discard_to_variant(full_discards, variants)
        status = f"target={matched['target']}" if matched else "无匹配"
        print(f"  {desc}: {status}")

    # 测试 [3m, *, 1m] 模式：允许中间摸切
    print("\n=== 模式 [3m-*-1m] 测试（* 允许摸切）===")
    variants_star = generate_equivalent_variants(["3m", "*", "1m"], "2m", None)
    star_cases = [
        ([("4z", False), ("3m", False), ("8m", True), ("1m", False)], "3m,8m(摸切),1m - 应匹配"),
        ([("3m", False), ("1m", False)], "3m,1m 相邻 - 应匹配"),
        ([("3m", False), ("5z", False), ("1m", False)], "3m,5z(手切),1m - 中间有手切，应不匹配"),
    ]
    for full_discards, desc in star_cases:
        matched = match_discard_to_variant(full_discards, variants_star)
        status = f"target={matched['target']}" if matched else "无匹配"
        print(f"  {desc}: {status}")

    # 测试摸切符号 t：3mt-1m 要求 3m 摸切、1m 手切
    print("\n=== 摸切符号 t 测试（3mt-1m = 3m摸切, 1m手切）===")
    variants_t = generate_equivalent_variants(["3mt", "1m"], "2m", None)
    t_cases = [
        ([("3m", True), ("1m", False)], "3m摸切,1m手切 - 应匹配"),
        ([("3m", False), ("1m", False)], "3m手切,1m手切 - 应不匹配"),
        ([("5m", True), ("3m", True), ("1m", False)], "5m摸切,3m摸切,1m手切 - 应匹配"),
    ]
    for full_discards, desc in t_cases:
        matched = match_discard_to_variant(full_discards, variants_t)
        status = f"target={matched['target']}" if matched else "无匹配"
        print(f"  {desc}: {status}")
