"""
等价变体生成工具

根据输入的舍牌序列、目标牌、可见牌约束，生成所有等价的变体。
直接与舍牌序列精确比对，不使用标准化。

等价规则（仅花色对称，无镜像）：
1. 花色等价：1s-3s ≡ 1m-3m ≡ 1p-3p（m/p/s 排列映射）
2. 变体数：0 种花色 1 个；1 种花色 3 个；2 或 3 种花色 6 个
3. 顺序敏感：3s-1s ≠ 1s-3s

摸切符号：牌后加 t 表示必须摸切，f 表示手切或摸切皆可；如 "3mt-1m" = 3m 摸切、1m 手切；"3mf" = 3m 手摸切皆可。
k 后缀：牌后加 k 表示该牌为关联牌（手牌中存在与打出牌数字差 ≤ 2 的搭子或对子），如 "2pk"。
* : 任意数量的摸切
$ : 任意一张手切（吃/碰之后的牌必为手切，如 c0p6p-$）
cd1/cd2 : 拆搭（两张同花色数值差0-2的手切，含对子1m-1m、两面1m-2m、嵌张1m-3m等；1s9s不是搭子）。cd2 约束：拆搭花色≠目标牌花色
cdm/cdp/cds : 拆万字/饼/索搭
"""
import re
from typing import List, Dict, Tuple, Optional, Union
from itertools import product, permutations

from .mjlog_parser import MjlogParser
from .related_tile_utils import is_related_discard, hand_to_suit_counts, base_to_discard_num_and_suit


# 字牌占位符：z=任意字牌, zf=自风, kf=客风, yp=役牌(自风/场风/三元), ap=安牌(满足其一：该字牌可见1-3枚 或 非场风非三元可见0张，不含本张), z1/z2/z3=互不相同的字牌, kf1/kf2/kf3=互不相同的客风
HONOR_PLACEHOLDERS = frozenset({"z", "zt", "zf", "kf", "yp", "ap", "z1", "z2", "z3", "kf1", "kf2", "kf3"})
HONOR_NAMES = ("东", "南", "西", "北", "白", "发", "中")
# 1z-4z 风牌对应中文，用于 @p:kf 客风校验
Z_TO_WIND = {"1z": "东", "2z": "南", "3z": "西", "4z": "北"}

# 赤五输入：0m=赤5m, 0p=赤5p, 0s=赤5s（编码独立 base 34/35/36，与 5m/5p/5s 视为不同牌）
RED_FIVES = frozenset({"0m", "0p", "0s"})

# 花色通配符：单字符 m/p/s = 任意万/饼/索（与 1m,2p,3s 等具体牌区分：后者为数字+花色两字符）
SUIT_WILDCARDS = frozenset({"m", "p", "s"})

# 吃碰占位符前缀：解析后为 @c:... 或 @p:...；语义为具体吃的/碰的牌（如 4mc3m5m=用3m5m吃4m）
# 含副露时也应用全局花色映射（4mc3m5m→4pc3p5p 等）；字牌不参与映射，不影响变体生成
CALL_PREFIX = "@"
# 拆搭占位符：@cd:1 任意拆搭、@cd:2 拆搭花色≠目标牌花色、@cd:m/p/s 拆万/饼/索搭
CD_PREFIX = "@cd:"
# 逻辑符号：@n:base,excl 表示 NOT（base 但排除 excl）；@o:a,b,c 表示 OR（匹配其一）
NOT_PREFIX = "@n:"
OR_PREFIX = "@o:"
# 关联牌：@k:tile 表示该舍牌须为关联牌（手牌中存在数字差≤2的搭子或对子）
RELATED_PREFIX = "@k:"

# 各花色 base 集合（含赤五）：用于 prior_discard_exclusion NOTm/p/s
_SUIT_FORBIDDEN_BASES = {
    "m": frozenset(range(9)) | {34},   # 1m-9m, 0m
    "p": frozenset(range(9, 18)) | {35},
    "s": frozenset(range(18, 27)) | {36},
}


def get_forbidden_bases_from_exclusion_str(prior_str: str) -> frozenset:
    """
    前段不可打：从模式字符串解析禁止的 base 集合。
    支持 OR 组合多元素（如 4mOR2m）、数字范围 [39]p（3p~9p），与舍牌模式语法一致。
    例：NOTm -> 万字全部；4mOR2m -> {4m,2m}；[39]p -> 3p~9p 共 7 张的 base。
    返回：frozenset[int]，元素为 base 编码 0-36（与 MjlogParser.string_to_tile 返回值一致）。
    调用处若用 prior_tiles [(tile_str, bool)] 比较，应用 string_to_tile(t[0]) in forbidden，勿用 // 4。
    """
    if not prior_str or not prior_str.strip():
        return frozenset()
    s = prior_str.strip()
    parsed, _ = parse_discard_element(s)
    return _forbidden_bases_from_parsed_tile(parsed)


def _forbidden_bases_from_parsed_tile(tile: str) -> frozenset:
    """从解析后的单元素（@n:m、@o:4m,2m 等）计算禁止 base 集合。返回 base 0-36（非 tile 码 0-147）。"""
    if tile.startswith(NOT_PREFIX):
        rest = tile[len(NOT_PREFIX):].strip()
        comps = [x.strip() for x in rest.split(",") if x.strip()]
        if len(comps) == 1 and comps[0] in SUIT_WILDCARDS:
            return _SUIT_FORBIDDEN_BASES[comps[0]]
        if len(comps) >= 2 and comps[0] == "*":
            out = set()
            for e in comps[1:]:
                out.add(MjlogParser.string_to_tile(e))
            return frozenset(out)
        if len(comps) >= 2:
            out = set()
            for e in comps[1:]:
                out.add(MjlogParser.string_to_tile(e))
            return frozenset(out)
    if tile.startswith(RELATED_PREFIX):
        return _forbidden_bases_from_parsed_tile(tile[len(RELATED_PREFIX):])
    if tile.startswith(OR_PREFIX):
        parts = [x.strip() for x in tile[len(OR_PREFIX):].split(",") if x.strip()]
        out = set()
        for p in parts:
            tp = p[len(RELATED_PREFIX):] if p.startswith(RELATED_PREFIX) else (p[:-1] if (p.endswith("t") or p.endswith("f")) else p)
            if tp.startswith(NOT_PREFIX) or tp.startswith(OR_PREFIX):
                out |= _forbidden_bases_from_parsed_tile(tp)
            else:
                out.add(MjlogParser.string_to_tile(tp))
        return frozenset(out)
    if tile in SUIT_WILDCARDS:
        return _SUIT_FORBIDDEN_BASES[tile]
    if tile in RED_FIVES or (len(tile) >= 2 and tile[-1] in "mps" and tile[0].isdigit()):
        return frozenset([MjlogParser.string_to_tile(tile)])
    if len(tile) == 2 and tile[0] in "1234567" and tile[1] == "z":
        return frozenset([MjlogParser.string_to_tile(tile)])
    return frozenset()


def split_discard_pattern(s: str) -> List[str]:
    """
    将舍牌模式字符串按顺序分隔符切分为元素列表。
    支持 "-" 与 "AND"（大小写不敏感），如 3mAND4m-zNOT1z -> ["3m","4m","zNOT1z"]
    """
    if not s or not s.strip():
        return []
    s = s.strip()
    # 用正则按 - 或 AND 切分，保留顺序；AND 前后可有可选空格
    parts = re.split(r"\s*-\s*|\s*[Aa][Nn][Dd]\s*", s)
    return [p.strip() for p in parts if p.strip()]


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


def player_could_satisfy_any_call_area_constraints(
    player_state, oya: int, constraint_sets: List[List[str]]
) -> bool:
    """检查该玩家是否可能满足任一 constraint set（用于等价变体预过滤）"""
    if not constraint_sets:
        return True
    return any(
        player_could_satisfy_call_area_constraints(player_state, oya, cs)
        for cs in constraint_sets
    )


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
    call_area_constraint_sets: Optional[List[List[str]]] = None,
) -> bool:
    """
    局级预过滤：call_constraint 与 call_area_constraints 是否有任何玩家可能满足。
    - call_constraint "has_call": 至少一人有副露
    - call_constraint "no_call": 至少一人无副露
    - call_area_constraints: 单组约束，至少一人可能满足
    - call_area_constraint_sets: 多组约束（等价变体），至少一人可能满足任一组
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
    if call_area_constraint_sets:
        if not any(
            player_could_satisfy_call_area_constraints(p, oya, cs)
            for p in round_players for cs in call_area_constraint_sets
        ):
            return False
    elif call_area_constraints:
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
        got_z = [_honor_tile_to_z(t) for t in got[:2]]
        if len(got_z) >= 2 and got_z[0] == got_z[1] and got_z[0] in kyokuze_z:
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


# 风牌 base 27-30（东=27,南=28,西=29,北=30），用于安牌「非场风、非三元」判定
_WIND_BASES = {27, 28, 29, 30}


def _ap_condition_met(visible_tiles, tile_str: str, bakaze: Optional[str]) -> bool:
    """
    安牌条件（OR）：(1) 场上可见1-3枚该字牌（不含本张） 或 (2) 可见0张的非场风、非三元字牌（不含本张）。
    满足其一即为安牌，不会因额外条件减少样本。
    """
    if not visible_tiles or not _is_honor_tile(tile_str):
        return False
    tile_base = MjlogParser.string_to_tile(tile_str)
    base = tile_base // 4
    equiv = MjlogParser.get_count_equivalent_bases(tile_base)
    count_this = sum(c for t, c in visible_tiles.items() if t // 4 in equiv)
    count_this_excl = max(0, count_this - 1)
    # (1) 该字牌场上可见1-3枚（不含本张）
    if 1 <= count_this_excl <= 3:
        return True
    # (2) 非场风、非三元字牌可见0张（不含本张）
    if not bakaze:
        return False
    bakaze_base = MjlogParser.string_to_tile(bakaze) // 4
    non_bakaze_sangen_bases = _WIND_BASES - {bakaze_base}
    for b in non_bakaze_sangen_bases:
        count_b = sum(c for t, c in visible_tiles.items() if t // 4 == b)
        count_b_excl = max(0, count_b - (1 if base == b else 0))
        if count_b_excl != 0:
            return False
    return True


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
    # 碰：p1z1z 或 pkfkf、pzfzf、pypyp
    if s_lower.startswith("p") and len(s) >= 2:
        rest = s_lower[1:]
        if rest == "kfkf":
            return (f"{CALL_PREFIX}p:kf", False)
        if rest == "zfzf":
            return (f"{CALL_PREFIX}p:zf", False)
        if rest == "ypyp":
            return (f"{CALL_PREFIX}p:yp", False)
        if len(rest) >= 4 and rest[0].isdigit() and rest[1] == "z":
            return (f"{CALL_PREFIX}p:{rest}", False)  # p1z1z -> @p:1z1z
    return None


def _is_simple_tile_or_placeholder(t: str) -> bool:
    """判断是否为可用于 OR 的简单牌或占位符（不含 NOT/OR/c/吃碰等）"""
    t = t.strip()
    if not t:
        return False
    if t in ("*", "$") or t in HONOR_PLACEHOLDERS or t in RED_FIVES or t in SUIT_WILDCARDS:
        return True
    if len(t) >= 2 and t[-1] in "mps" and (t[0].isdigit() or t in RED_FIVES):
        return True
    if len(t) == 2 and t[0] in "1-7" and t[1] == "z":
        return True
    # 带 t 摸切或 f 手摸切皆可：3mt、3mf
    if (t.endswith("t") or t.endswith("f")) and len(t) >= 3:
        return _is_simple_tile_or_placeholder(t[:-1])
    # 带 k 关联牌：2pk、[25]mk
    if t.startswith(RELATED_PREFIX):
        return _is_simple_tile_or_placeholder(t[len(RELATED_PREFIX):])
    return False


def parse_discard_element(s: str) -> Tuple[str, Optional[bool]]:
    """
    解析舍牌模式元素。
    "3mt" -> ("3m", True)  摸切
    "3mf" -> ("3m", None) 手摸切皆可
    "3m" -> ("3m", False) 手切
    "0m","0p","0s" -> 赤5m/赤5p/赤5s
    "4mc3m5m" -> 用 3m5m 吃 4m；"c5m6m" -> 用 56m 吃 4m 或 7m
    "p1z1z" -> 用两个东碰；"pkfkf" -> 客风碰
    "z" -> ("z", False) 任意字牌；"zf"/"kf" 等
    "zNOT1z" -> ("@n:z,1z", False) 任意字牌但排除东
    "3mOR5m" -> ("@o:3m,5m", False) 3m 或 5m
    "m"/"p"/"s" -> 任意万字/饼子/索子（单字符，与 1m,2p,3s 等区分）
    """
    s = s.strip()
    if not s:
        return ("*", False)
    if s == "*":
        return ("*", False)
    if s == "$":
        return ("$", False)  # 任意一张手切
    # 花色通配符 m/p/s 及 mf/pf/sf：mf/pf/sf = 任意该花色且手摸切皆可
    if s in ("mf", "pf", "sf"):
        return (s[0], None)
    if s in SUIT_WILDCARDS:
        return (s, False)
    # [xy] 数字范围：[25]m = 2m,3m,4m,5m 其一；[17]z = 1z..7z；支持 t/r/f/k 后缀
    m_range = re.match(r"^\[(\d)(\d)\]([mpsz])(t|r|f|k)?$", s, re.IGNORECASE)
    if m_range:
        x, y, suit_or_z, suffix = m_range.group(1), m_range.group(2), m_range.group(3).lower(), m_range.group(4)
        lo, hi = int(x), int(y)
        if lo <= hi:
            tiles = []
            if suit_or_z == "z":
                for n in range(lo, hi + 1):
                    tiles.append(f"{n}z" + (suffix or ""))
            else:
                for n in range(lo, hi + 1):
                    tiles.append(f"{n}{suit_or_z}" + (suffix or ""))
            tsumo_val = True if suffix == "t" else (None if suffix == "f" else False)
            if suffix == "k" and suit_or_z != "z":
                # [25]mk -> @o:@k:2m,@k:3m,@k:4m,@k:5m（仅数牌支持关联牌）
                k_tiles = [f"{RELATED_PREFIX}{t}" for t in tiles]
                return (f"{OR_PREFIX}{','.join(k_tiles)}", False)
            return (f"{OR_PREFIX}{','.join(tiles)}", tsumo_val)
    # NOT[xy] 排除范围：NOT[45]m = 4m,5m 之外均可；NOT[17]z = 1z..7z 之外均可
    m_not_range = re.match(r"^[Nn][Oo][Tt]\[(\d)(\d)\]([mpsz])$", s)
    if m_not_range:
        x, y, suit_or_z = m_not_range.group(1), m_not_range.group(2), m_not_range.group(3).lower()
        lo, hi = int(x), int(y)
        if lo <= hi:
            excl = []
            if suit_or_z == "z":
                excl = [f"{n}z" for n in range(lo, hi + 1)]
            else:
                excl = [f"{n}{suit_or_z}" for n in range(lo, hi + 1)]
            return (f"{NOT_PREFIX}*,{','.join(excl)}", False)
    # OR: 3mOR5m / 3mOR5mOR7m -> @o:3m,5m,7m；3mOR[25]m 中 [25]m 展开为 2m,3m,4m,5m
    if "OR" in s.upper():
        parts = re.split(r"\s*[Oo][Rr]\s*", s)
        parts = [p.strip() for p in parts if p.strip()]
        expanded = []
        for p in parts:
            m_r = re.match(r"^\[(\d)(\d)\]([mpsz])(t|r|f|k)?$", p, re.IGNORECASE)
            if m_r:
                x, y, suit_or_z, suffix = m_r.group(1), m_r.group(2), m_r.group(3).lower(), m_r.group(4)
                lo, hi = int(x), int(y)
                if lo <= hi:
                    if suffix == "k" and suit_or_z != "z":
                        for n in range(lo, hi + 1):
                            expanded.append(f"{RELATED_PREFIX}{n}{suit_or_z}")
                    else:
                        for n in range(lo, hi + 1):
                            expanded.append(f"{n}{suit_or_z}" + (suffix or ""))
                    continue
            expanded.append(p)
        if len(expanded) >= 2 and all(_is_simple_tile_or_placeholder(p) for p in expanded):
            has_f = any(p.endswith("f") for p in expanded)
            return (f"{OR_PREFIX}{','.join(expanded)}", None if has_f else False)
    # NOT 花色：NOTm/NOTp/NOTs、NOTmf/NOTpf/NOTsf（f=手摸切皆可）
    if re.match(r"^[Nn][Oo][Tt][mps]f?$", s):
        suit = s[-2] if s.endswith("f") else s[-1]
        tsumo_val = None if s.endswith("f") else False
        return (f"{NOT_PREFIX}{suit.lower()}", tsumo_val)
    # NOT 字牌：zNOT1z / zNOT1zNOT2z -> @n:z,1z 或 @n:z,1z,2z
    if "NOT" in s.upper():
        parts = re.split(r"\s*[Nn][Oo][Tt]\s*", s)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2:
            base = parts[0]
            excl = parts[1:]
            # base 须为 z/zt/zf/kf 等字牌占位；excl 须为 1z-7z
            if base in ("z", "zt", "zf", "kf", "yp") or base in HONOR_PLACEHOLDERS:
                valid_excl = all(
                    len(e) == 2 and e[0] in "1234567" and e[1] == "z"
                    for e in excl
                )
                if valid_excl:
                    return (f"{NOT_PREFIX}{base},{','.join(excl)}", base == "zt")
    # 拆搭：cd1/cd2/cdm/cdp/cds
    if s.lower() in ("cd1", "cd2", "cdm", "cdp", "cds"):
        # cd1->@cd:1, cd2->@cd:2, cdm->@cd:m, cdp->@cd:p, cds->@cd:s
        suf = s.lower()[2:]  # "1","2","m","p","s"
        return (f"{CD_PREFIX}{suf}", False)
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
    # r 后缀：立直宣言牌（该牌为打出时宣告立直；与 c/p 不可同时出现）
    if s.endswith("r") and len(s) >= 2:
        is_tsumo = False # 默认为手切立直 (r)
        base = s[:-1]
        if base.endswith("t"):
            is_tsumo = True # 摸切立直 (tr)
            base = base[:-1]
        elif base.endswith("f"):
            is_tsumo = None # 手摸皆可立直 (fr)
            base = base[:-1]
            
        if base in RED_FIVES or (len(base) >= 2 and base[-1] in "mps" and (base[0].isdigit() or base in RED_FIVES)):
            return (f"{CALL_PREFIX}r:{base}", is_tsumo)
        if base in HONOR_PLACEHOLDERS:
            return (f"{CALL_PREFIX}r:{base}", is_tsumo)
    if s.endswith("f") and len(s) >= 2:
        base = s[:-1]
        if base in HONOR_PLACEHOLDERS or base in SUIT_WILDCARDS:
            return (base, None)  # 手摸切皆可
        if base in RED_FIVES or (len(base) >= 2 and base[-1] in "mps" and (base[0].isdigit() or base in RED_FIVES)):
            return (base, None)
        if len(base) == 2 and base[0] in "1-7" and base[1] == "z":
            return (base, None)
    if s.endswith("t") and len(s) >= 2:
        base = s[:-1]
        if base in HONOR_PLACEHOLDERS:
            if base == "z":
                return ("z", True)
            return (base, True)
        if base in RED_FIVES:
            return (base, True)  # 赤五摸切
        return (base, True)  # 普通牌摸切
    # k 后缀：关联牌（手牌中存在数字差≤2的搭子或对子）；仅数牌有效，可与 t/f/r 组合
    if s.endswith("k") and len(s) >= 2:
        base = s[:-1]
        is_tsumo = False
        inner = base
        if base.endswith("t"):
            is_tsumo = True
            inner = base[:-1]
        elif base.endswith("f"):
            is_tsumo = None
            inner = base[:-1]
        # inner 可能含 r：2pr -> @r:2p
        if inner.endswith("r") and len(inner) >= 2:
            r_inner = inner[:-1]
            if r_inner.endswith("t"):
                is_tsumo = True
                r_inner = r_inner[:-1]
            elif r_inner.endswith("f"):
                is_tsumo = None
                r_inner = r_inner[:-1]
            if r_inner in RED_FIVES or (len(r_inner) >= 2 and r_inner[-1] in "mps" and (r_inner[0].isdigit() or r_inner in RED_FIVES)):
                return (f"{RELATED_PREFIX}{CALL_PREFIX}r:{r_inner}", is_tsumo)
            if r_inner in HONOR_PLACEHOLDERS:
                return (f"{RELATED_PREFIX}{CALL_PREFIX}r:{r_inner}", is_tsumo)
        if inner in RED_FIVES or (len(inner) >= 2 and inner[-1] in "mps" and (inner[0].isdigit() or inner in RED_FIVES)):
            return (f"{RELATED_PREFIX}{inner}", is_tsumo)
        if len(inner) == 2 and inner[0] in "1-7" and inner[1] == "z":
            return (f"{RELATED_PREFIX}{inner}", is_tsumo)
        if inner in HONOR_PLACEHOLDERS:
            return (f"{RELATED_PREFIX}{inner}", is_tsumo)
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


def parse_multi_targets(target_str: str) -> List[Tuple[List[str], bool]]:
    """
    解析多目标牌字符串，支持同时分析多个目标。
    "6s 2m 5p" 或 "6s,2m,5p" -> [(["6s"],False), (["2m"],False), (["5p"],False)]
    "6s" -> [(["6s"],False)]
    "1m3m" -> [(["1m","3m"],True)]  # 搭子仍为单一目标
    """
    s = (target_str or "").strip()
    if not s:
        return [(["2m"], False)]
    parts = re.split(r'[\s,;]+', s)
    result = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        tiles, is_combo = parse_target_tiles(p)
        result.append((tiles, is_combo))
    return result if result else [(["2m"], False)]


def mirror_number(n: int) -> int:
    """镜像数字: 1↔9, 2↔8, 3↔7, 4↔6, 5↔5。等价变体已取消镜像，仅保留供 _transform_tile_for_constraint 等兼容。"""
    return 10 - n


def _get_suits_in_pattern(parsed_pattern: List[Tuple[str, bool]]) -> set:
    """从解析后的舍牌模式中收集出现的花色 m/p/s"""
    suits = set()

    def _add_suits_from_tile(tile: str):
        if tile in SUIT_WILDCARDS:
            suits.add(tile)
        elif tile.startswith(NOT_PREFIX):
            rest = tile[len(NOT_PREFIX):].strip()
            comps = [x.strip() for x in rest.split(",") if x.strip()]
            if comps and comps[0] == "*":
                for e in comps[1:]:
                    if len(e) >= 2 and e[-1] in "mps":
                        suits.add(e[-1])
            elif rest in SUIT_WILDCARDS:
                suits.add(rest)
        elif tile.startswith(OR_PREFIX):
            for part in tile[len(OR_PREFIX):].split(","):
                part = part.strip()
                tp = part[:-1] if part.endswith("t") else part
                if tp in SUIT_WILDCARDS:
                    suits.add(tp)
                elif tp in RED_FIVES or (len(tp) >= 2 and tp[-1] in "mps"):
                    suits.add(tp[-1])
        elif tile.startswith("@r:"):
            sub = tile[3:]
            if sub in ("ap", "yp"):
                pass
            elif sub in RED_FIVES or (len(sub) >= 2 and sub[-1] in "mps"):
                suits.add(sub[-1])
        elif tile.startswith(CD_PREFIX):
            suf = tile[len(CD_PREFIX):]
            if suf in "mps":
                suits.add(suf)
        elif tile.startswith(CALL_PREFIX) and ":" in tile:
            rest = tile.split(":", 1)[1]
            if rest in ("ap", "yp"):
                pass
            else:
                for i in range(0, len(rest) - 1, 2):
                    if i + 1 < len(rest) and rest[i + 1] in "mps":
                        suits.add(rest[i + 1])
        elif tile in ("ap", "yp"):
            pass
        elif tile in RED_FIVES or (len(tile) >= 2 and tile[-1] in "mps"):
            suits.add(tile[-1])

    for tile, _ in parsed_pattern:
        _add_suits_from_tile(tile)
    return suits


# 字牌占位符中含字母 p 但不是花色：ap(安牌)、apr(立直宣言安牌)、yp(役牌)、ypr；带 f/t/r 后缀如 apf/apt 等也不参与映射
_RESERVED_CONTAIN_P = frozenset({"ap", "apr", "yp", "ypr"})


def _is_ap_or_yp_token(raw: str) -> bool:
    """ap/yp 及其带 f/t/r 后缀的形式（apf, apt, apr, ypf, ypt, ypr 等）整词不参与花色替换"""
    if raw in _RESERVED_CONTAIN_P:
        return True
    if raw.startswith("ap") and len(raw) > 2 and all(c in "ftr" for c in raw[2:]):
        return True
    if raw.startswith("yp") and len(raw) > 2 and all(c in "ftr" for c in raw[2:]):
        return True
    return False


def _apply_suit_mapping_to_string(raw: str, mapping: Dict[str, str]) -> str:
    """
    对原始舍牌元素做花色映射：仅替换 m/p/s 字符，t、r 等后缀保持不变。
    例：2mt + m→p -> 2pt；NOTm + m→p -> NOTp
    ap/apr/apf/apt 与 yp/ypr/ypf/ypt 等为整词占位符，其中的 p 不是花色，不参与映射。
    """
    if _is_ap_or_yp_token(raw):
        return raw
    return "".join(mapping[c] if c in "mps" else c for c in raw)


def _apply_suit_mapping_to_tile(tile: str, mapping: Dict[str, str]) -> str:
    """对单张牌/占位符应用花色映射 mapping: {m,p,s} -> {m,p,s}"""
    if tile in ("*", "$"):
        return tile
    if tile.startswith(RELATED_PREFIX):
        inner = _apply_suit_mapping_to_tile(tile[len(RELATED_PREFIX):], mapping)
        return f"{RELATED_PREFIX}{inner}"
    if tile.startswith(CD_PREFIX):
        suf = tile[len(CD_PREFIX):]
        if suf in "mps":
            return f"{CD_PREFIX}{mapping[suf]}"
        return tile
    if tile.startswith("@r:"):
        sub = tile[3:]
        if sub in ("ap", "yp"):
            return tile
        if sub in RED_FIVES or (len(sub) >= 2 and sub[-1] in "mps"):
            return f"@r:{sub[0]}{mapping[sub[-1]]}"
        return tile
    if tile.startswith(NOT_PREFIX):
        rest = tile[len(NOT_PREFIX):].strip()
        comps = [x.strip() for x in rest.split(",") if x.strip()]
        if comps and comps[0] == "*":
            transformed = []
            for e in comps[1:]:
                if len(e) >= 2 and e[-1] in "mps":
                    transformed.append(f"{e[0]}{mapping[e[-1]]}")
                else:
                    transformed.append(e)
            return f"{NOT_PREFIX}*,{','.join(transformed)}" if transformed else tile
        if rest in SUIT_WILDCARDS:
            return f"{NOT_PREFIX}{mapping[rest]}"
        return tile
    if tile.startswith(OR_PREFIX):
        parts = tile[len(OR_PREFIX):].split(",")
        transformed = []
        for p in parts:
            p = p.strip()
            suffix = ("t" if p.endswith("t") else ("f" if p.endswith("f") else ""))
            tile_part = p[:-1] if suffix else p
            if tile_part in RED_FIVES:
                transformed.append(f"0{mapping[tile_part[-1]]}" + suffix)
            elif len(tile_part) >= 2 and tile_part[-1] in "mps":
                transformed.append(f"{tile_part[0]}{mapping[tile_part[-1]]}" + suffix)
            elif tile_part in SUIT_WILDCARDS:
                transformed.append(mapping[tile_part] + suffix)
            else:
                transformed.append(p)
        return f"{OR_PREFIX}{','.join(transformed)}"
    if tile.startswith(CALL_PREFIX) and ":" in tile:
        prefix, rest = tile.split(":", 1)
        if rest in ("ap", "yp"):
            return tile
        if any(c in rest for c in "mps"):
            new_rest = ""
            i = 0
            while i < len(rest):
                if i + 1 < len(rest) and rest[i + 1] in "mps":
                    new_rest += rest[i] + mapping[rest[i + 1]]
                    i += 2
                else:
                    new_rest += rest[i]
                    i += 1
            return f"{prefix}:{new_rest}"
        return tile
    if tile in SUIT_WILDCARDS:
        return mapping[tile]
    if tile in RED_FIVES or (len(tile) >= 2 and tile[-1] in "mps"):
        return f"{tile[0]}{mapping[tile[-1]]}"
    return tile


def _apply_suit_mapping_to_pattern(pattern: List[Tuple[str, bool]], mapping: Dict[str, str]) -> List[Tuple[str, bool]]:
    """对舍牌模式应用花色映射"""
    return [(_apply_suit_mapping_to_tile(t, mapping), ts) for t, ts in pattern]


def _get_suit_mappings_for_variants(suits_in_pattern: set) -> List[Dict[str, str]]:
    """
    根据舍牌序列中出现的花色，返回需要的映射列表。
    - 0 种花色：1 个映射（恒等）
    - 1 种花色：3 个映射（该花色分别映到 m/p/s）
    - 2 或 3 种花色：6 个映射（全排列）
    """
    base = ["m", "p", "s"]
    all_perms = list(permutations(base))
    if not suits_in_pattern:
        return [dict(zip(base, base))]
    if len(suits_in_pattern) == 1:
        (suit,) = suits_in_pattern
        result = []
        for target in base:
            for p in all_perms:
                m = dict(zip(base, p))
                if m[suit] == target:
                    result.append(m)
                    break
        return result
    return [dict(zip(base, p)) for p in all_perms]


def _change_suit(tile: str, new_suit: str) -> str:
    """将数牌的花色改为 new_suit，字牌、*、$、@cd: 与吃碰占位符不变；@r:、@o: 后牌参与花色变换；@n: 不变；t/f 后缀保留"""
    suffix = ("t" if tile.endswith("t") else ("f" if tile.endswith("f") else ""))
    if suffix:
        tile = tile[:-1]
    if tile in ("*", "$"):
        return tile + suffix
    if tile.startswith(CD_PREFIX):
        return tile
    if tile.startswith("@r:"):
        return f"@r:{_change_suit(tile[3:], new_suit)}{suffix}"
    if tile.startswith(OR_PREFIX):
        rest = tile[len(OR_PREFIX):]
        opts = [x.strip() for x in rest.split(",") if x.strip()]
        transformed = [_change_suit(o, new_suit) for o in opts]
        return f"{OR_PREFIX}{','.join(transformed)}"
    # 以下分支：tile 已被 strip suffix，最后需加回
    # @n: 须在 CALL_PREFIX(@) 之前，因 @n: 也以 @ 开头
    if tile.startswith(NOT_PREFIX):
        rest = tile[len(NOT_PREFIX):].strip()
        comps = [x.strip() for x in rest.split(",") if x.strip()]
        if comps and comps[0] == "*":
            transformed = []
            for e in comps[1:]:
                if len(e) >= 2 and e[-1] in "mps":
                    transformed.append(f"{e[0]}{new_suit}")
                else:
                    transformed.append(e)
            return f"{NOT_PREFIX}*,{','.join(transformed)}" if transformed else tile
        if rest in SUIT_WILDCARDS:
            return f"{NOT_PREFIX}{new_suit}{suffix}"  # NOTm -> NOTp 等
        return tile + suffix
    if tile.startswith(CALL_PREFIX):
        return tile + suffix
    if tile in SUIT_WILDCARDS:
        return new_suit + suffix
    if len(tile) >= 2 and tile[-1] in "mps" and (tile[0].isdigit() or tile in RED_FIVES):
        return f"{tile[0]}{new_suit}" + suffix
    return tile + suffix


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
    """对模式应用花色变换，保留摸切标记。赤五参与：0m→0s 等。*、$、@cd: 不变；@n:m/p/s 随主花色变换；@o: 内数牌参与。"""
    return [
        (_change_suit(tile, suit) if tile not in ("*", "$") and not tile.startswith(CD_PREFIX) else tile, is_tsumogiri)
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
        elif tile.startswith(CD_PREFIX):
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
        elif tile.startswith(NOT_PREFIX):
            rest = tile[len(NOT_PREFIX):].strip()
            comps = [x.strip() for x in rest.split(",") if x.strip()]
            if comps and comps[0] == "*":
                transformed = []
                for e in comps[1:]:
                    if e in RED_FIVES:
                        transformed.append(f"0{suit_red}")
                    elif len(e) >= 2 and e[-1] in "mps" and e[0].isdigit():
                        transformed.append(f"{e[0]}{suit_num}")
                    else:
                        transformed.append(e)
                result.append((f"{NOT_PREFIX}*,{','.join(transformed)}" if transformed else tile, is_tsumogiri))
            elif rest in SUIT_WILDCARDS:
                result.append((f"{NOT_PREFIX}{suit_num}", is_tsumogiri))
            else:
                result.append((tile, is_tsumogiri))
        elif tile.startswith(OR_PREFIX):
            parts = [x.strip() for x in tile[len(OR_PREFIX):].split(",") if x.strip()]
            transformed = []
            for p in parts:
                if p in RED_FIVES:
                    transformed.append(f"0{suit_red}")
                elif len(p) >= 2 and p[-1] in "mps" and p[0].isdigit():
                    transformed.append(f"{p[0]}{suit_num}")
                elif p in SUIT_WILDCARDS:
                    transformed.append(suit_num)
                else:
                    transformed.append(p)
            result.append((f"{OR_PREFIX}{','.join(transformed)}", is_tsumogiri))
        elif tile in RED_FIVES:
            result.append((f"0{suit_red}", is_tsumogiri))
        elif tile in SUIT_WILDCARDS:
            result.append((suit_num, is_tsumogiri))
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
        elif tile.startswith(CD_PREFIX):
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
        elif tile.startswith(NOT_PREFIX):
            rest = tile[len(NOT_PREFIX):].strip()
            comps = [x.strip() for x in rest.split(",") if x.strip()]
            if comps and comps[0] == "*":
                transformed = []
                for e in comps[1:]:
                    if len(e) >= 2 and e[-1] in "mps" and e[0].isdigit():
                        transformed.append(f"{mirror_number(int(e[0]))}{e[-1]}")
                    else:
                        transformed.append(e)
                result.append((f"{NOT_PREFIX}*,{','.join(transformed)}" if transformed else tile, is_tsumogiri))
            else:
                result.append((tile, is_tsumogiri))
        elif tile in SUIT_WILDCARDS:
            result.append((tile, is_tsumogiri))  # m/p/s 无数可镜像
        elif tile.startswith(OR_PREFIX):
            parts = [x.strip() for x in tile[len(OR_PREFIX):].split(",") if x.strip()]
            transformed = []
            for p in parts:
                suffix = ("t" if p.endswith("t") else ("f" if p.endswith("f") else ""))
                base = p[:-1] if suffix else p
                if len(base) >= 2 and base[-1] in "mps" and base[0].isdigit():
                    transformed.append(f"{mirror_number(int(base[0]))}{base[-1]}" + suffix)
                elif base in SUIT_WILDCARDS:
                    transformed.append(base + suffix)
                else:
                    transformed.append(p)
            result.append((f"{OR_PREFIX}{','.join(transformed)}", is_tsumogiri))
        elif tile in RED_FIVES:
            result.append((tile, is_tsumogiri))
        elif len(tile) >= 2 and tile[-1] in "mps" and tile[0].isdigit():
            n = int(tile[0])
            result.append((f"{mirror_number(n)}{tile[-1]}", is_tsumogiri))
        else:
            result.append((tile, is_tsumogiri))
    return result


def _transform_tile_for_constraint(tile_str: str, suit: str, mirror: bool) -> str:
    """转换可见约束中的牌（mirror 已废弃，保留签名兼容）"""
    if len(tile_str) >= 2 and tile_str[-1] in 'mps' and tile_str[0].isdigit():
        return f"{tile_str[0]}{suit}"
    return tile_str


def _transform_tile_with_mapping(tile_str: str, mapping: Dict[str, str]) -> str:
    """对目标牌/约束牌应用花色映射"""
    if tile_str in RED_FIVES or (len(tile_str) >= 2 and tile_str[-1] in "mps"):
        return f"{tile_str[0]}{mapping[tile_str[-1]]}"
    return tile_str


def _transform_visible_constraints(
    visible_constraints: Optional[Dict[str, Tuple[int, int]]],
    suit: str,
    mirror: bool
) -> Dict[str, Tuple[int, int]]:
    """转换可见约束到指定花色（mirror 已废弃）"""
    if not visible_constraints:
        return {}
    result = {}
    for tile_str, (min_count, max_count) in visible_constraints.items():
        new_tile = _transform_tile_for_constraint(tile_str, suit, mirror)
        result[new_tile] = (min_count, max_count)
    return result


def _transform_visible_constraints_with_mapping(
    visible_constraints: Optional[Dict[str, Tuple[int, int]]],
    mapping: Dict[str, str]
) -> Dict[str, Tuple[int, int]]:
    """对可见约束应用花色映射"""
    if not visible_constraints:
        return {}
    return {_transform_tile_with_mapping(t, mapping): v for t, v in visible_constraints.items()}


def _expand_pure_honor_pattern(parsed: List[Tuple[str, bool]]) -> List[List[Tuple[str, bool]]]:
    """
    展开纯字牌模式为具体变体。
    - zt-zt: 49 种 (7×7)
    - z1-z2: 42 种 (7×6)
    - z1-z2-z3: 210 种 (7×6×5)
    - 含 zf/kf/ap 的不展开，保留占位符（1 种）
    """
    if any(t in ("zf", "kf", "kf1", "kf2", "kf3", "ap") for t, _ in parsed):
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
    visible_constraints: Optional[Dict[str, Tuple[int, int]]] = None,
    prior_discard_exclusion: Optional[str] = None,
    call_area_constraints: Optional[List[str]] = None,
    prior_discard_required: Optional[str] = None,
) -> List[Dict]:
    """
    根据舍牌序列、目标牌、可见牌约束，生成所有等价变体。
    仅花色对称，无镜像对称。

    - 0 种花色（纯字牌等）：1 个变体；若目标牌为数牌/赤五则按目标牌花色仍生成 3 变体（如 apr+3p -> 3m/3p/3s）
    - 1 种花色：3 个变体（该花色映到 m/p/s）
    - 2 或 3 种花色：6 个变体（全排列）
    """
    if not discard_pattern:
        return []

    parsed_pattern = [parse_discard_element(p) for p in discard_pattern]
    target_tiles, is_combo = parse_target_tiles(target_tile)

    def _is_number_tile(tile: str) -> bool:
        return len(tile) >= 2 and tile[-1] in 'mps' and tile[0].isdigit()

    def _is_red_five(tile: str) -> bool:
        return tile in RED_FIVES

    def _pattern_has_number_tile(tile: str) -> bool:
        if _is_number_tile(tile):
            return True
        if tile.startswith("@r:"):
            return _is_number_tile(tile[3:])
        if tile.startswith(RELATED_PREFIX):
            inner = tile[len(RELATED_PREFIX):]
            if inner.startswith("@r:"):
                return _is_number_tile(inner[3:])
            return _is_number_tile(inner)
        return False

    has_number = any(_pattern_has_number_tile(tile) for tile, _ in parsed_pattern)
    has_chi = any(tile.startswith("@c:") for tile, _ in parsed_pattern)
    has_pon = any(tile.startswith("@p:") for tile, _ in parsed_pattern)
    has_riichi = any(tile.startswith("@r:") for tile, _ in parsed_pattern)
    has_number_pon = any(
        tile.startswith("@p:") and any(c in tile[3:] for c in "mps")
        for tile, _ in parsed_pattern
    )
    if has_riichi and (has_chi or has_pon):
        raise ValueError("舍牌模式不能同时包含立直宣言(r)与吃/碰(c/p)，立直玩家不可副露")

    # 纯字牌模式：舍牌无花色，但若目标牌为数牌/赤五，仍按目标牌花色生成等价变体（如 apr + 3p -> 3m/3p/3s）
    if not has_number:
        honor_variants = _expand_pure_honor_pattern(parsed_pattern)
        prior = prior_discard_exclusion.strip() if prior_discard_exclusion else None
        prior_req = prior_discard_required.strip() if prior_discard_required else None
        call_area = list(call_area_constraints) if call_area_constraints else None
        # 从目标牌中收集花色（数牌、赤五）
        target_suits = set()
        for t in target_tiles:
            if t in RED_FIVES:
                target_suits.add(t[-1])
            elif len(t) >= 2 and t[-1] in "mps" and (t[0].isdigit() or t in RED_FIVES):
                target_suits.add(t[-1])
        if target_suits:
            mappings = _get_suit_mappings_for_variants(target_suits)
            result = []
            for p in honor_variants:
                for mapping in mappings:
                    target_new = [_transform_tile_with_mapping(t, mapping) for t in target_tiles]
                    target_new = target_new[0] if len(target_new) == 1 else target_new
                    visible_new = _transform_visible_constraints_with_mapping(visible_constraints, mapping)
                    prior_mapped = _apply_suit_mapping_to_string(prior, mapping) if prior else None
                    prior_req_mapped = _apply_suit_mapping_to_string(prior_req, mapping) if prior_req else None
                    call_area_new = [_apply_suit_mapping_to_string(s, mapping) for s in (call_area or [])]
                    result.append({
                        "discard": p, "target": target_new,
                        "visible_constraints": visible_new, "is_combo": is_combo,
                        "prior_discard_exclusion": prior_mapped, "prior_discard_required": prior_req_mapped,
                        "call_area_constraints": call_area_new,
                        "mapping": mapping,
                    })
            return result
        t = target_tiles[0] if len(target_tiles) == 1 else target_tiles
        mapping = {"m": "m", "p": "p", "s": "s"}
        return [
            {"discard": p, "target": t, "visible_constraints": dict(visible_constraints) if visible_constraints else {}, "is_combo": is_combo, "prior_discard_exclusion": prior, "prior_discard_required": prior_req, "call_area_constraints": call_area, "mapping": mapping}
            for p in honor_variants
        ]

    # 花色对称：含副露时也应用映射（4mc3m5m→4pc3p5p 等）；字牌不参与映射
    suits_in_pattern = _get_suits_in_pattern(parsed_pattern)
    mappings = _get_suit_mappings_for_variants(suits_in_pattern)

    variants = []
    for mapping in mappings:
        # 在原始字符串上替换 m/p/s，再解析（保证 t/r 不丢失）
        mapped_raw = [_apply_suit_mapping_to_string(elem, mapping) for elem in discard_pattern]
        discard_new = [parse_discard_element(e) for e in mapped_raw]
        target_new = [_transform_tile_with_mapping(t, mapping) for t in target_tiles]
        target_new = target_new[0] if len(target_new) == 1 else target_new
        visible_new = _transform_visible_constraints_with_mapping(visible_constraints, mapping)
        prior_mapped = _apply_suit_mapping_to_string(prior_discard_exclusion.strip(), mapping) if prior_discard_exclusion else None
        prior_req = prior_discard_required.strip() if prior_discard_required else None
        prior_req_mapped = _apply_suit_mapping_to_string(prior_req, mapping) if prior_req else None
        call_area_new = [_apply_suit_mapping_to_string(s, mapping) for s in call_area_constraints] if call_area_constraints else None
        variants.append({
            "discard": discard_new,
            "target": target_new,
            "visible_constraints": visible_new,
            "is_combo": is_combo,
            "prior_discard_exclusion": prior_mapped,
            "prior_discard_required": prior_req_mapped,
            "call_area_constraints": call_area_new,
            "mapping": mapping,
        })
    return variants


def prior_required_pattern_to_variants(prior_str_mapped: str) -> List[Dict]:
    """
    将已映射的「前段有打」模式字符串转为供 match_discard_to_variant 使用的单变体列表。
    语法与舍牌模式一致（如 [29]m-3pf），仅用于前段序列的「包含」匹配。
    """
    if not prior_str_mapped or not prior_str_mapped.strip():
        return []
    parts = split_discard_pattern(prior_str_mapped.strip())
    parsed = [parse_discard_element(p) for p in parts]
    return [{"discard": parsed, "target": None, "visible_constraints": {}, "is_combo": False}]


def match_discard_pattern_contained(
    full_discards: List[Tuple[str, bool]],
    variants: List[Dict],
    context: Optional[Dict] = None,
) -> bool:
    """
    检查 full_discards 中是否存在某段连续子序列匹配任一变体（用于「前段有打」）。
    需检查所有长度为 L 的连续子序列 full_discards[i:i+L]，而非仅后缀。
    否则单元素模式如 [37]mOR[37]s 会错误地只匹配「最后一张」而非「任一张」。
    cd1/cd2 等拆搭占位符需在完整序列中查找搭子对，故传入 full_discards_for_cd 供 _discard_is_chaida 使用。
    """
    ctx = dict(context) if context else {}
    for v in variants:
        L = len(v["discard"])
        if L == 0:
            return True
        for i in range(len(full_discards) - L + 1):
            ctx["full_discards_for_cd"] = full_discards
            ctx["slice_start_for_cd"] = i
            if match_discard_to_variant(full_discards[i : i + L], variants, ctx):
                return True
    return False


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


# [已废弃] 以下函数已注释，请使用 generate_equivalent_variants + match_discard_to_variant
# def normalize_discard_pattern(tiles: List[str]) -> str:
#     """已废弃：返回原始序列的字符串形式，不做等价标准化。"""
#     return "-".join(tiles) if tiles else ""
#
# def map_target_tile(query_pattern: List[str], actual_pattern: List[str], target_tile: str) -> str:
#     """已废弃：当 query_pattern 与 actual_pattern 等价时映射目标牌。"""
#     full_discards = [(t, False) for t in actual_pattern]
#     matched = match_discard_to_variant(full_discards, generate_equivalent_variants(query_pattern, target_tile, None))
#     return matched["target"] if matched else target_tile


def _consumed_matches_call(pat_tile: str, calls: list, context: Optional[Dict] = None,
                           require_immediate: bool = False) -> bool:
    """
    检查 pat_tile (@c:xyz 或 @p:xyz) 是否与 calls 中某次副露匹配。
    仅考虑在该舍牌之前发生的副露（context["current_discard_turn"]）。
    require_immediate: 若为 True，要求该舍牌必须为副露后立即打出的那张（from_discard_turn == current_turn），
       以确保巡目/立直等约束正确作用（如 c0p6p-$ 中 $ 必须是吃完后立刻打出的牌）。
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


def _is_number_tile(tile_str: str) -> bool:
    """是否为数牌（万筒索，非字牌）"""
    if len(tile_str) < 2:
        return False
    if tile_str[-1] not in "mps":
        return False
    return tile_str[0].isdigit() or tile_str in RED_FIVES


def _number_tile_value(tile_str: str) -> Optional[int]:
    """数牌数值，0m/0p/0s 视为 5"""
    if tile_str in RED_FIVES:
        return 5
    if len(tile_str) >= 2 and tile_str[-1] in "mps" and tile_str[0].isdigit():
        return int(tile_str[0])
    return None


def _is_meld(t1: str, t2: str) -> bool:
    """两数牌是否构成搭子或对子（同花色，数值差0-2）。0=对子如1m-1m，1=两面/边张，2=嵌张。1s9s 不是搭子。"""
    if not _is_number_tile(t1) or not _is_number_tile(t2):
        return False
    if t1[-1] != t2[-1]:
        return False
    v1, v2 = _number_tile_value(t1), _number_tile_value(t2)
    if v1 is None or v2 is None:
        return False
    diff = abs(v1 - v2)
    return diff in (0, 1, 2)


def _discard_is_chaida(
    full_discards: List[Tuple[str, bool]],
    d_idx: int,
    require_suit: Optional[str] = None,
    exclude_suit: Optional[str] = None,
    search_list: Optional[List[Tuple[str, bool]]] = None,
    check_idx_in_search: Optional[int] = None,
) -> bool:
    """
    检查 full_discards[d_idx] 是否为拆搭（与另一张手切构成搭子或对子）。
    require_suit: 必须为该花色（cdm/cdp/cds）
    exclude_suit: 不能为该花色（cd2 约束：拆搭花色≠目标牌花色）
    search_list/check_idx_in_search: 前段有打时，在完整序列中查找搭子对（单元素切片无法找到另一张）
    """
    if search_list is not None and check_idx_in_search is not None:
        discards_to_search = search_list
        idx_to_check = check_idx_in_search
    else:
        discards_to_search = full_discards
        idx_to_check = d_idx
    if idx_to_check < 0 or idx_to_check >= len(discards_to_search):
        return False
    tile_str, is_tsumogiri = discards_to_search[idx_to_check]
    if is_tsumogiri or not _is_number_tile(tile_str):
        return False
    if require_suit and tile_str[-1] != require_suit:
        return False
    if exclude_suit and tile_str[-1] == exclude_suit:
        return False
    for i, (t2, ts2) in enumerate(discards_to_search):
        if i == idx_to_check or ts2:
            continue
        if not _is_number_tile(t2):
            continue
        if _is_meld(tile_str, t2):
            return True
    return False


def _match_pattern_at_end(
    full_discards: List[Tuple[str, bool]],
    pattern: List[Tuple[str, bool]],
    context: Optional[Dict] = None,
    out_position_to_tile: Optional[Dict[int, str]] = None,
    out_match_start_index: Optional[List[int]] = None,
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
        out_position_to_tile: 若提供，成功匹配时填充 {pattern_idx: 匹配到的牌}（仅舍牌消耗位置）
    """
    if not full_discards or not pattern:
        return False

    ctx = context or {}
    jikaze = ctx.get("jikaze")
    bakaze = ctx.get("bakaze")
    kyokuze_list = ctx.get("kyokuze_list") or ()
    calls = ctx.get("calls") or ()

    # 役牌集合：自风、场风、三元牌
    yakuhai_set = set()
    if jikaze: yakuhai_set.add(_honor_tile_to_z(jikaze))
    if bakaze: yakuhai_set.add(_honor_tile_to_z(bakaze))
    yakuhai_set.update({"5z", "6z", "7z"})

    d_idx = len(full_discards) - 1
    p_idx = len(pattern) - 1
    matched_honors: set = set() # 存储 z-canon 格式，确保 z1/z2/z3 互不相同
    consumed_any_discard = False  # 用于 z1,z2,z3 的“互不相同”约束

    while p_idx >= 0 and d_idx >= 0:
        tile_str, is_tsumogiri = full_discards[d_idx]
        pat_tile, pat_want_tsumogiri = pattern[p_idx]

        # 关联牌 @k:：消耗一张舍牌，该舍牌须为关联牌（手牌中存在数字差≤2的搭子或对子）
        if pat_tile.startswith(RELATED_PREFIX):
            inner = pat_tile[len(RELATED_PREFIX):]
            # 内层可为 @r:2p 或 2p
            if inner.startswith(f"{CALL_PREFIX}r:"):
                want_tile = inner[3:]
                riichi_flags = ctx.get("discard_riichi_flags") or []
                if d_idx >= len(riichi_flags) or not riichi_flags[d_idx]:
                    return False
                if want_tile != "ap" and tile_str != want_tile:
                    return False
                if want_tile == "ap" and (not _is_honor_tile(tile_str) or not _ap_condition_met(
                    ctx.get("visible_tiles") or {}, tile_str, ctx.get("bakaze"))):
                    return False
            else:
                tile_canon = _honor_tile_to_z(tile_str) if _is_honor_tile(tile_str) else tile_str
                inner_canon = _honor_tile_to_z(inner) if _is_honor_tile(inner) else inner
                if tile_canon != inner_canon:
                    return False
            if pat_want_tsumogiri is not None and is_tsumogiri != pat_want_tsumogiri:
                return False
            hand_after_by_index = ctx.get("hand_after_by_index")
            slice_start = ctx.get("slice_start_for_cd", 0)
            if hand_after_by_index is not None:
                try:
                    base = MjlogParser.string_to_tile(tile_str)
                    num, suit = base_to_discard_num_and_suit(base)
                    if num is not None and suit is not None:
                        hand_after = hand_after_by_index[slice_start + d_idx]
                        counts = hand_to_suit_counts(hand_after, suit)
                        if not is_related_discard(num, counts):
                            return False
                except (IndexError, KeyError, TypeError):
                    return False
            else:
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue

        # 立直宣言牌 @r:：消耗一张舍牌，该舍牌须为立直宣言
        if pat_tile.startswith("@r:"):
            want_tile = pat_tile[3:]
            riichi_flags = ctx.get("discard_riichi_flags") or []
            if d_idx >= len(riichi_flags) or not riichi_flags[d_idx]:
                return False
            # @r:ap = 立直宣言的安牌：须为字牌且满足安牌条件（可见1-3枚+非场风非三元可见0张，不含本张舍牌）
            if want_tile == "ap":
                if not _is_honor_tile(tile_str):
                    return False
                visible_tiles = ctx.get("visible_tiles")
                bakaze = ctx.get("bakaze")
                if not _ap_condition_met(visible_tiles, tile_str, bakaze):
                    return False
            elif tile_str != want_tile:
                return False
            # 摸切要求须一致（pat_want_tsumogiri 为 None 时手摸切皆可）
            if pat_want_tsumogiri is not None and is_tsumogiri != pat_want_tsumogiri:
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue

        # 拆搭 @cd:1/2/m/p/s：消耗一张舍牌，须为手切且与另一手切构成搭子或对子
        if pat_tile.startswith(CD_PREFIX):
            if is_tsumogiri:
                return False
            cd_suffix = pat_tile[len(CD_PREFIX):]
            require_suit = cd_suffix if cd_suffix in "mps" else None
            exclude_suit = None
            if cd_suffix == "2":
                # cd2：拆搭花色≠目标牌花色（从 context 获取）
                target_tile = ctx.get("target_tile")
                if target_tile:
                    tiles = target_tile if isinstance(target_tile, list) else [target_tile]
                    for t in tiles:
                        if isinstance(t, str) and len(t) >= 2 and t[-1] in "mps":
                            exclude_suit = t[-1]
                            break
            # 前段有打时，单元素切片无法找到搭子对，需在完整序列中查找
            search_list = ctx.get("full_discards_for_cd")
            slice_start = ctx.get("slice_start_for_cd", 0)
            check_idx = (slice_start + d_idx) if search_list is not None else None
            if not _discard_is_chaida(
                full_discards, d_idx, require_suit, exclude_suit,
                search_list=search_list, check_idx_in_search=check_idx,
            ):
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue

        # @n: 与 @o: 须在 CALL_PREFIX 之前检查（因皆以 @ 开头）
        if pat_tile.startswith(NOT_PREFIX):
            rest = pat_tile[len(NOT_PREFIX):].strip()
            comps = [x.strip() for x in rest.split(",") if x.strip()]
            # @n:*,4m,5m：NOT[45]m，匹配除 4m、5m 外的任意牌
            if len(comps) >= 2 and comps[0] == "*":
                excl_set = set()
                excl_suit = None  # 排除牌花色；等价变换时仅匹配该花色的舍牌，避免 4m 误匹配 NOT[45]p
                for e in comps[1:]:
                    if _is_honor_tile(e):
                        excl_set.add(_honor_tile_to_z(e))
                    else:
                        excl_set.add(e)
                        if len(e) >= 2 and e[-1] in "mps":
                            excl_suit = e[-1]
                tile_canon = _honor_tile_to_z(tile_str) if _is_honor_tile(tile_str) else tile_str
                if tile_canon in excl_set:
                    return False
                # 等价变换：排除为某花色时，数牌舍牌须同花色（NOT[45]p 只匹配 1p~9p 且非 4p5p，避免 4m 误匹配）
                if excl_suit and (_is_number_tile(tile_str) or tile_str in RED_FIVES):
                    if tile_str[-1] != excl_suit:
                        return False
                consumed_any_discard = True
                if out_position_to_tile is not None:
                    out_position_to_tile[p_idx] = tile_str
                d_idx -= 1
                p_idx -= 1
                continue
            # @n:m / @n:p / @n:s：任意一张非万/饼/索
            if len(comps) == 1 and comps[0] in SUIT_WILDCARDS:
                excl_suit = comps[0]
                tile_suit = None
                if _is_number_tile(tile_str) or tile_str in RED_FIVES:
                    tile_suit = tile_str[-1]
                elif _is_honor_tile(tile_str):
                    tile_suit = None  # 字牌无花色，视为非数牌花色
                if tile_suit == excl_suit:
                    return False
                consumed_any_discard = True
                if out_position_to_tile is not None:
                    out_position_to_tile[p_idx] = tile_str
                d_idx -= 1
                p_idx -= 1
                continue
            if len(comps) >= 2:
                base, excl_z = comps[0], set(comps[1:])
                tile_z = _honor_tile_to_z(tile_str)
                if not _is_honor_tile(tile_str):
                    return False
                if tile_z in excl_z:
                    return False
                if base == "zf" and (jikaze is None or tile_str != jikaze):
                    return False
                if base == "kf" and (not kyokuze_list or tile_str not in kyokuze_list):
                    return False
                consumed_any_discard = True
                if out_position_to_tile is not None:
                    out_position_to_tile[p_idx] = tile_str
                d_idx -= 1
                p_idx -= 1
                continue

        if pat_tile.startswith(OR_PREFIX):
            rest = pat_tile[len(OR_PREFIX):]
            options = [x.strip() for x in rest.split(",") if x.strip()]
            matched_opt = None
            for opt in options:
                want_tsumogiri_opt = True if opt.endswith("t") else (None if opt.endswith("f") else False)
                tile_part = opt[len(RELATED_PREFIX):] if opt.startswith(RELATED_PREFIX) else (opt[:-1] if (opt.endswith("t") or opt.endswith("f")) else opt)
                if tile_part in ("$", "*"):
                    # $=手切 *＝摸切，不比较牌面，只比较摸切状态
                    want_hand = tile_part == "$"
                    if want_hand and not is_tsumogiri:
                        matched_opt = opt
                        break
                    if not want_hand and is_tsumogiri:
                        matched_opt = opt
                        break
                elif tile_part in SUIT_WILDCARDS:
                    if want_tsumogiri_opt is not None and is_tsumogiri != want_tsumogiri_opt:
                        continue
                    if (_is_number_tile(tile_str) or tile_str in RED_FIVES) and tile_str[-1] == tile_part:
                        matched_opt = opt
                        break
                elif tile_part in HONOR_PLACEHOLDERS:
                    if want_tsumogiri_opt is not None and is_tsumogiri != want_tsumogiri_opt:
                        continue
                    if tile_part in ("z", "zt", "z1", "z2", "z3"):
                        if _is_honor_tile(tile_str) and tile_str not in matched_honors:
                            matched_opt = opt
                            matched_honors.add(tile_str)
                            break
                    elif tile_part == "zf":
                        if jikaze is not None and _honor_tile_to_z(tile_str) == _honor_tile_to_z(jikaze):
                            matched_opt = opt
                            break
                    elif tile_part == "yp":
                        if _honor_tile_to_z(tile_str) in yakuhai_set:
                            matched_opt = opt
                            break
                    elif tile_part == "ap":
                        if not _is_honor_tile(tile_str):
                            continue
                        visible_tiles = ctx.get("visible_tiles")
                        bakaze = ctx.get("bakaze")
                        if _ap_condition_met(visible_tiles, tile_str, bakaze):
                            matched_opt = opt
                            break
                    elif tile_part in ("kf", "kf1", "kf2", "kf3"):
                        tile_z = _honor_tile_to_z(tile_str)
                        if kyokuze_list and tile_z in [_honor_tile_to_z(k) for k in kyokuze_list] and tile_z not in matched_honors:
                            matched_opt = opt
                            matched_honors.add(tile_z)
                            break
                else:
                    tile_canon = _honor_tile_to_z(tile_str) if _is_honor_tile(tile_str) else tile_str
                    opt_canon = _honor_tile_to_z(tile_part) if _is_honor_tile(tile_part) else tile_part
                    if tile_canon == opt_canon and (want_tsumogiri_opt is None or is_tsumogiri == want_tsumogiri_opt):
                        matched_opt = opt
                        break
            if matched_opt is None:
                return False
            if matched_opt.startswith(RELATED_PREFIX):
                hand_after_by_index = ctx.get("hand_after_by_index")
                slice_start = ctx.get("slice_start_for_cd", 0)
                if hand_after_by_index is not None:
                    try:
                        base = MjlogParser.string_to_tile(tile_str)
                        num, suit = base_to_discard_num_and_suit(base)
                        if num is not None and suit is not None:
                            hand_after = hand_after_by_index[slice_start + d_idx]
                            counts = hand_to_suit_counts(hand_after, suit)
                            if not is_related_discard(num, counts):
                                return False
                        else:
                            return False
                    except (IndexError, KeyError, TypeError):
                        return False
                else:
                    return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
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
                kyokuze_z_list = [_honor_tile_to_z(k) for k in kyokuze_list]
                for c in calls:
                    if current_turn is not None:
                        from_turn = getattr(c, "from_discard_turn", 1)
                        if from_turn > current_turn:
                            continue
                        if require_immediate_kf and from_turn != current_turn:
                            continue
                    if getattr(c, "call_type", None) == "pon":
                        pai_z = _honor_tile_to_z(getattr(c, "pai", ""))
                        if pai_z in kyokuze_z_list:
                            has_kf_pon = True
                            break
                if not has_kf_pon:
                    return False
            elif pat_tile == f"{CALL_PREFIX}p:zf":
                if not jikaze:
                    return False
                has_zf_pon = False
                current_turn = ctx.get("current_discard_turn")
                for c in calls:
                    if current_turn is not None:
                        if getattr(c, "from_discard_turn", 1) > current_turn:
                            continue
                    if getattr(c, "call_type", None) == "pon":
                        pai_z = _honor_tile_to_z(getattr(c, "pai", ""))
                        if pai_z == _honor_tile_to_z(jikaze):
                            has_zf_pon = True
                            break
                if not has_zf_pon:
                    return False
            elif pat_tile == f"{CALL_PREFIX}p:yp":
                has_yp_pon = False
                current_turn = ctx.get("current_discard_turn")
                for c in calls:
                    if current_turn is not None:
                        if getattr(c, "from_discard_turn", 1) > current_turn:
                            continue
                    if getattr(c, "call_type", None) == "pon":
                        pai_z = _honor_tile_to_z(getattr(c, "pai", ""))
                        if pai_z in yakuhai_set:
                            has_yp_pon = True
                            break
                if not has_yp_pon:
                    return False
            else:
                # @c:xyz / @p:xyz：须校验 calls 中有对应吃/碰（且在该舍牌之前发生）
                # require_immediate: 若副露后紧跟 $ 或具体牌（无 * 隔开），则当前舍牌必须是副露后立刻打出的那张，
                #   以便巡目/立直等约束正确作用
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
                if out_position_to_tile is not None:
                    out_position_to_tile[p_idx] = tile_str
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
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue

        # 摸切要求须一致（pat_want_tsumogiri 为 None 时手摸切皆可）
        if pat_want_tsumogiri is not None and is_tsumogiri != pat_want_tsumogiri:
            return False

        # 字牌占位符匹配
        if pat_tile in ("z", "zt"):
            if not _is_honor_tile(tile_str):
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile == "zf":
            if jikaze is None or _honor_tile_to_z(tile_str) != _honor_tile_to_z(jikaze):
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile == "kf":
            if not kyokuze_list or _honor_tile_to_z(tile_str) not in [_honor_tile_to_z(k) for k in kyokuze_list]:
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile == "yp":
            if _honor_tile_to_z(tile_str) not in yakuhai_set:
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile == "ap":
            # 安牌：场上可见1-3枚字牌+可见0张的非场风、非三元字牌（不含本张舍牌）
            if not _is_honor_tile(tile_str):
                return False
            visible_tiles = ctx.get("visible_tiles")
            bakaze = ctx.get("bakaze")
            if not _ap_condition_met(visible_tiles, tile_str, bakaze):
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue
        if pat_tile in ("z1", "z2", "z3", "kf1", "kf2", "kf3"):
            tile_z = _honor_tile_to_z(tile_str)
            if pat_tile.startswith("kf"):
                # kf1,kf2,kf3：客风，且互不相同
                if not kyokuze_list or tile_z not in [_honor_tile_to_z(k) for k in kyokuze_list]:
                    return False
                if tile_z in matched_honors:
                    return False
                matched_honors.add(tile_z)
            else:
                # z1,z2,z3：任意字牌，互不相同
                if not _is_honor_tile(tile_str):
                    return False
                if tile_z in matched_honors:
                    return False
                matched_honors.add(tile_z)
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue

        # 花色通配符 m/p/s：任意该花色的数牌（含赤五 0m/0p/0s）
        if pat_tile in SUIT_WILDCARDS:
            if not _is_number_tile(tile_str) and tile_str not in RED_FIVES:
                return False
            if tile_str[-1] != pat_tile:
                return False
            consumed_any_discard = True
            if out_position_to_tile is not None:
                out_position_to_tile[p_idx] = tile_str
            d_idx -= 1
            p_idx -= 1
            continue

        # 具体牌：精确匹配（0m/0p/0s 与 5m/5p/5s 视为不同牌）
        if tile_str != pat_tile:
            return False
        consumed_any_discard = True
        if out_position_to_tile is not None:
            out_position_to_tile[p_idx] = tile_str
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
                kyokuze_z_list = [_honor_tile_to_z(k) for k in kyokuze_list]
                has_kf_pon = False
                for c in calls:
                    if current_turn is not None:
                        from_turn = getattr(c, "from_discard_turn", 1)
                        if from_turn > current_turn:
                            continue
                    if getattr(c, "call_type", None) == "pon":
                        pai_z = _honor_tile_to_z(getattr(c, "pai", ""))
                        if pai_z in kyokuze_z_list:
                            has_kf_pon = True
                            break
                if not has_kf_pon:
                    return False
            elif pat_tile == f"{CALL_PREFIX}p:zf":
                if not jikaze: return False
                current_turn = ctx.get("current_discard_turn")
                has_zf_pon = False
                for c in calls:
                    if current_turn is not None and getattr(c, "from_discard_turn", 1) > current_turn: continue
                    if getattr(c, "call_type", None) == "pon" and _honor_tile_to_z(getattr(c, "pai", "")) == _honor_tile_to_z(jikaze):
                        has_zf_pon = True; break
                if not has_zf_pon: return False
            elif pat_tile == f"{CALL_PREFIX}p:yp":
                current_turn = ctx.get("current_discard_turn")
                has_yp_pon = False
                for c in calls:
                    if current_turn is not None and getattr(c, "from_discard_turn", 1) > current_turn: continue
                    if getattr(c, "call_type", None) == "pon" and _honor_tile_to_z(getattr(c, "pai", "")) in yakuhai_set:
                        has_yp_pon = True; break
                if not has_yp_pon: return False
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

    matched = p_idx < 0
    if matched and out_match_start_index is not None:
        out_match_start_index[:] = [d_idx + 1]
    return matched


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
        匹配到的变体（含 position_to_tile、matched_start_index 等），若都不匹配则返回 None。
        matched_start_index: 0-based 索引，表示舍牌序列中模式匹配的起始位置（该位置及之后为模式，之前为「前段」）。
    """
    for v in variants:
        out_pt = {}
        out_start = []
        ctx = dict(context) if context else {}
        if v.get("target") is not None:
            ctx["target_tile"] = v["target"]
        if _match_pattern_at_end(
            full_discards,
            v["discard"],
            ctx,
            out_position_to_tile=out_pt,
            out_match_start_index=out_start,
        ):
            return {
                **v,
                "position_to_tile": out_pt,
                "matched_start_index": out_start[0] if out_start else None,
            }
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
