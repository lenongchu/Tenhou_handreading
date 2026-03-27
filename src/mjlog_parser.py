"""
牌谱数据结构与牌符工具

保留 GameState、Discard 及牌编码转换等工具，解析逻辑已迁移至 tenhou6_adapter。
"""

from dataclasses import dataclass, field
from typing import List, Set, Optional, Union
from collections import Counter as PyCounter


@dataclass
class CallInfo:
    """副露信息：该玩家的一次吃/碰/杠"""
    call_type: str  # "chii" | "pon" | "kan" | "daiminkan" | "kakan" | "ankan"
    pai: str       # 鸣牌（被吃/碰的牌，如 "7s"）
    consumed: List[str]  # 自己的牌，如 ["6s","8s"]
    from_discard_turn: int = 1  # 此副露后第一次舍牌的巡目；匹配时只考虑 discard.turn >= from_discard_turn


@dataclass
class Discard:
    """舍牌信息"""
    turn: int           # 巡目（该玩家的第几次出牌）
    tile: int           # 牌编码（0-147，含赤五 base 34-36）
    is_tsumogiri: bool  # 是否摸切
    riichi_happened: bool = False   # 打出此牌时，是否已有人立直
    opponent_riichi_happened: bool = False  # 打出此牌时，是否已有其他三家立直
    call_happened: bool = False     # 打出此牌时，是否已有人副露（吃/碰/杠）
    is_riichi_declaration: bool = False  # 该舍牌是否为立直宣言牌（打出此牌宣告立直）
    # 记录该时刻所有玩家的当前巡目（1-based），用于同步手牌快照。格式：[p0_turn, p1_turn, p2_turn, p3_turn]
    # 若某玩家尚未出牌，则其巡目为 0。
    all_players_turns: Optional[List[int]] = None


@dataclass
class GameState:
    """游戏状态快照"""
    player_id: int                      # 玩家编号（0-3）
    round_num: int = 0                  # 小局号（0=东1, 1=东2, 2=东3, 3=东4, 4=南1...）
    honba: int = 0                      # 本场数（连庄次数）
    oya: int = 0                        # 该局亲家（庄家）的 player_id
    discards: List[Discard] = field(default_factory=list)  # 本家舍牌序列
    hand_tiles: Set[int] = field(default_factory=set)      # 当前手牌（0-147，含赤五）
    initial_hand: Set[int] = field(default_factory=set)    # 初始配牌
    hand_tiles_history: List[Union[Set[int], List[int]]] = field(default_factory=list)  # 每一巡打牌后的手牌快照（list 可保留同种牌枚数）
    dora_indicators: List[int] = field(default_factory=list)  # 宝牌指示牌
    visible_tiles: PyCounter = field(default_factory=PyCounter)  # 其他3家可见牌统计
    calls: List["CallInfo"] = field(default_factory=list)  # 本家副露列表，用于 @p:kf 客风校验
    # 小局结局（仅终局时有效，用于和了率/放铳率统计）
    round_winners: List[int] = field(default_factory=list)   # 和牌者列表（自摸1人，一炮双响2-3人）
    round_deal_in: Optional[int] = None  # 放铳者，None 表示自摸或流局；一炮双响时仅计1次


class TileUtils:
    """
    牌符与编码转换工具（原 MjlogParser 静态方法）。
    解析逻辑已由 tenhou6_adapter 负责。
    """

    HONOR_NAMES = ("东", "南", "西", "北", "白", "发", "中")
    HONOR_Z = tuple(f"{i}z" for i in range(1, 8))

    @staticmethod
    def tile_to_string(tile: int) -> str:
        """牌编码（0-147）→ 字符串（如 3s、东、0m 赤五）"""
        base_tile = tile // 4
        if 0 <= base_tile < 9:
            return f"{base_tile + 1}m"
        elif 9 <= base_tile < 18:
            return f"{base_tile - 9 + 1}p"
        elif 18 <= base_tile < 27:
            return f"{base_tile - 18 + 1}s"
        elif base_tile == 34:
            return "0m"
        elif base_tile == 35:
            return "0p"
        elif base_tile == 36:
            return "0s"
        elif base_tile == 27:
            return "东"
        elif base_tile == 28:
            return "南"
        elif base_tile == 29:
            return "西"
        elif base_tile == 30:
            return "北"
        elif base_tile == 31:
            return "白"
        elif base_tile == 32:
            return "发"
        elif base_tile == 33:
            return "中"
        return f"unknown_{tile}"

    @staticmethod
    def string_to_tile(tile_str: str) -> int:
        """字符串（如 3s、东、0m 赤五）→ base 编码（0-36）"""
        if len(tile_str) == 2:
            num = int(tile_str[0])
            suit = tile_str[1]
            if suit == "m":
                return 34 if num == 0 else num - 1  # 0m = 赤5m = base 34
            elif suit == "p":
                return 35 if num == 0 else 9 + num - 1  # 0p = 赤5p = base 35
            elif suit == "s":
                return 36 if num == 0 else 18 + num - 1  # 0s = 赤5s = base 36
        wind_map = {"东": 27, "南": 28, "西": 29, "北": 30}
        dragon_map = {"白": 31, "发": 32, "中": 33}
        z_map = {"1z": 27, "2z": 28, "3z": 29, "4z": 30, "5z": 31, "6z": 32, "7z": 33}
        if tile_str in wind_map:
            return wind_map[tile_str]
        if tile_str in dragon_map:
            return dragon_map[tile_str]
        if tile_str in z_map:
            return z_map[tile_str]
        raise ValueError(f"无效的牌字符串: {tile_str}")

    @staticmethod
    def indicator_to_dora(indicator: int) -> int:
        """宝牌指示物 → 宝牌（base 0-33）"""
        if 0 <= indicator < 27:
            suit_base = (indicator // 9) * 9
            suit_offset = indicator % 9
            return suit_base + (suit_offset + 1) % 9
        elif 27 <= indicator <= 30:
            return 27 + (indicator - 27 + 1) % 4
        elif 31 <= indicator <= 33:
            return 31 + (indicator - 31 + 1) % 3
        return indicator

    @staticmethod
    def dora_to_indicator(dora: int) -> int:
        """宝牌 → 宝牌指示物（base 0-33）"""
        if 0 <= dora < 27:
            suit_base = (dora // 9) * 9
            suit_offset = dora % 9
            return suit_base + (suit_offset - 1) % 9
        elif 27 <= dora <= 30:
            return 27 + (dora - 27 - 1) % 4
        elif 31 <= dora <= 33:
            return 31 + (dora - 31 - 1) % 3
        return dora

    @staticmethod
    def format_round_display(round_num: int, honba: int) -> str:
        """小局显示：东1局、南2局 2本场 等"""
        if round_num < 4:
            name = f"东{round_num + 1}局"
        elif round_num < 8:
            name = f"南{round_num - 3}局"
        else:
            name = f"第{round_num + 1}局"
        if honba > 0:
            name += f" {honba}本场"
        return name

    @staticmethod
    def get_player_wind(player_id: int, oya: int) -> str:
        """根据 player_id 和亲家 oya 计算座风（东南西北）"""
        winds = ["东", "南", "西", "北"]
        wind_idx = (player_id - oya + 4) % 4
        return winds[wind_idx]

    @staticmethod
    def get_jikaze(player_id: int, oya: int, round_num: int) -> str:
        """
        自风（seat wind）：仅由相对亲家（oya）的座位决定，逆时针为东南西北。
        与役牌 / zf / yp / yaku_honor_bases_for_seat 一致；勿把 round 场序叠加到座位上
        （旧实现用 (seat+field)%4 会在南场把部分座位的役牌集合算错，如 yp 误.match 北）。
        round_num 保留仅为兼容调用签名，不参与计算。
        """
        _ = round_num  # API 兼容（compatibility）；场风请用 get_bakaze
        return TileUtils.get_player_wind(player_id, oya)

    @staticmethod
    def get_bakaze(round_num: int) -> str:
        """
        场风（round wind）：东一至东四为东，南一至南四为南…
        与 honor_ctx 一致；超长局下标封顶，避免 round_num//4 越界。
        """
        winds = ("东", "南", "西", "北")
        idx = min(max(round_num // 4, 0), len(winds) - 1)
        return winds[idx]

    @staticmethod
    def yaku_honor_bases_for_seat(player_id: int, oya: int, round_num: int) -> Set[int]:
        """立直役牌 base 集合：自风、场风（连风时 set 自动去重）、三元。"""
        ji = TileUtils.get_jikaze(player_id, oya, round_num)
        ba = TileUtils.get_bakaze(round_num)
        return {
            TileUtils.string_to_tile(ji),
            TileUtils.string_to_tile(ba),
            TileUtils.string_to_tile("白"),
            TileUtils.string_to_tile("发"),
            TileUtils.string_to_tile("中"),
        }

    @staticmethod
    def get_kyokuze_list(player_id: int, oya: int, round_num: int) -> List[str]:
        """客风：3 个非自风的风牌"""
        jikaze = TileUtils.get_jikaze(player_id, oya, round_num)
        return [w for w in ("东", "南", "西", "北") if w != jikaze]

    @classmethod
    def base_to_honor_str(cls, base: int) -> str:
        """base 27-33 → 字牌字符串（1z-7z）"""
        if 27 <= base <= 33:
            return cls.HONOR_Z[base - 27]
        return None

    @classmethod
    def bases_equivalent_for_count(cls, base_a: int, base_b: int) -> bool:
        """两 base 在统计目标数时是否等价（0m/0p/0s 与 5m/5p/5s 视为不同牌）"""
        return base_a == base_b

    @classmethod
    def get_count_equivalent_bases(cls, base: int):
        """返回该 base 在统计目标数时的等价 base（0m/0p/0s 与 5m/5p/5s 视为不同，仅自身）"""
        return {base}

    @classmethod
    def get_bases_for_target_tile_str(cls, tile_str: str) -> set:
        """
        目标牌字符串 → 统计用 base 集合（与 get_count_equivalent_bases 一致，但支持通配符）。
        5.m / 5.p / 5.s：表示「普通五或赤五」，即 5x 与 0x 两 base 任一在手即计为该张目标。
        4.p 等非 5 数字：. 无赤牌含义，等价于单张 4p（与 4p 相同）。
        """
        if (
            len(tile_str) == 3
            and tile_str[0].isdigit()
            and tile_str[1] == "."
            and tile_str[2] in "mps"
        ):
            d = int(tile_str[0])
            suit = tile_str[2]
            if d == 5:
                return {
                    cls.string_to_tile(f"5{suit}"),
                    cls.string_to_tile(f"0{suit}"),
                }
            return {cls.string_to_tile(f"{d}{suit}")}
        base = cls.string_to_tile(tile_str)
        return set(cls.get_count_equivalent_bases(base))


# 向后兼容：保留 MjlogParser 作为 TileUtils 的别名
MjlogParser = TileUtils
