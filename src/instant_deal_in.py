# -*- coding: utf-8 -*-
"""
即时铳率引擎（事件流重放）

目标：在“命中样本所在的当巡时点”判断玩家是否可对目标牌荣和（含完整振听）并计算理论 ron 点。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .mjlog_parser import MjlogParser

logger = logging.getLogger(__name__)

try:
    from mahjong.hand_calculating.hand import HandCalculator
    from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
    from mahjong.meld import Meld

    HAS_MAHJONG = True
except Exception:
    HandCalculator = None  # type: ignore
    HandConfig = None  # type: ignore
    OptionalRules = None  # type: ignore
    Meld = None  # type: ignore
    HAS_MAHJONG = False


# 赤五 base(34/35/36) → 标准 5m/5p/5s 的 base(4/13/22)，用于 mahjong 库 34 种牌统计时归并
RED_TO_STANDARD = {34: 4, 35: 13, 36: 22}
# 赤五 base(34/35/36) → mahjong 库 136 编码中的 tile 索引（赤五有固定位置）
RED_TO_136 = {34: 16, 35: 52, 36: 88}
WIND_STR_TO_BASE = {"东": 27, "南": 28, "西": 29, "北": 30}
BAKAZE_TO_BASE = {"E": 27, "S": 28, "W": 29, "N": 30, "东": 27, "南": 28, "西": 29, "北": 30}


def _tile_to_base(tile_str: Optional[str]) -> Optional[int]:
    if not tile_str or not isinstance(tile_str, str):
        return None
    try:
        return MjlogParser.string_to_tile(tile_str)
    except Exception:
        return None


def _normalize_base(base: int) -> int:
    return RED_TO_STANDARD.get(base, base)


def _base_to_136(base: int) -> int:
    if base in RED_TO_136:
        return RED_TO_136[base]
    # 避免把普通 5m/5p/5s 映射到赤宝牌索引
    if base in (4, 13, 22):
        return base * 4 + 1
    return base * 4


def _base_to_wait_label(base: int) -> str:
    return MjlogParser.tile_to_string(base * 4)


def _remove_one_tile(tiles: List[int], base: int) -> bool:
    # 优先精确移除；失败时按“同牌种（含赤五归并）”兜底
    for i, b in enumerate(tiles):
        if b == base:
            del tiles[i]
            return True
    normalized = _normalize_base(base)
    for i, b in enumerate(tiles):
        if _normalize_base(b) == normalized:
            del tiles[i]
            return True
    return False


@dataclass(frozen=True)
class CallEventSnapshot:
    call_type: str
    pai_base: int
    consumed_bases: Tuple[int, ...]
    from_discard_turn: int


@dataclass(frozen=True)
class DiscardSnapshot:
    player_id: int
    turn: int
    hand_bases: Tuple[int, ...]  # 暗手（不含副露组）
    active_calls: Tuple[CallEventSnapshot, ...]
    discards_norm: Tuple[int, ...]
    riichi_declared: bool
    same_turn_furiten: bool
    riichi_furiten: bool
    dora_bases: Tuple[int, ...]
    round_wind: int
    player_wind: int


def extract_tenhou6_rounds(raw: Union[str, Dict]) -> List[Tuple[Dict, List[Dict]]]:
    """
    从 tenhou6 原始 JSON 中提取小局（去重后一局一条）：
    返回 [(game_data, game_events), ...]
    """
    if not raw:
        return []
    
    data = None
    if isinstance(raw, dict):
        data = raw
    else:
        s = raw.strip()
        if not (s.startswith("{") and "games" in raw):
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
    
    if not data:
        return []
    out: List[Tuple[Dict, List[Dict]]] = []
    seen = set()
    for g in data.get("games", []) or []:
        gd = g.get("data", {}) or {}
        rid = (
            (gd.get("bakaze") or "E").upper(),
            int(gd.get("kyoku", 1)),
            int(gd.get("honba", 0)),
            int(gd.get("oya", 0)),
        )
        if rid in seen:
            continue
        seen.add(rid)
        out.append((gd, g.get("game", []) or []))
    return out


class RoundInstantDealInAnalyzer:
    """
    单局事件重放 + 即时荣和判定。
    """

    def __init__(self, game_data: Dict, game_events: List[Dict], round_num: int, oya: int):
        self.game_data = game_data
        self.game_events = game_events or []
        self.round_num = int(round_num)
        self.oya = int(oya)

        bakaze = (self.game_data.get("bakaze") or "E")
        self.round_wind = BAKAZE_TO_BASE.get(str(bakaze).upper(), 27)
        self.player_winds = [
            WIND_STR_TO_BASE.get(MjlogParser.get_player_wind(pid, self.oya), 27)
            for pid in range(4)
        ]

        self.snapshots: Dict[Tuple[int, int], DiscardSnapshot] = {}
        self._waits_cache: Dict[Tuple, Set[int]] = {}
        self._ron_cache: Dict[Tuple[Tuple, int], int] = {}
        self._calculator = HandCalculator() if HAS_MAHJONG else None

        self._replay()

    def evaluate(self, player_id: int, turn: int, target_tile: str) -> Dict:
        """
        在 (player_id, turn) 时点，判断是否可对 target_tile 荣和。
        """
        target_base = _tile_to_base(target_tile)
        if target_base is None:
            return {
                "deal_in_hit": False,
                "deal_in_point": 0,
                "furiten_state": "none",
                "furiten_reason": "目标牌无效",
                "waits_snapshot": [],
            }

        snapshot = self.snapshots.get((player_id, turn))
        if snapshot is None:
            return {
                "deal_in_hit": False,
                "deal_in_point": 0,
                "furiten_state": "none",
                "furiten_reason": "未找到当巡快照",
                "waits_snapshot": [],
            }

        return self._evaluate_snapshot(snapshot, target_base)

    def _replay(self) -> None:
        tehais_raw = self.game_data.get("tehais", [[], [], [], []]) or [[], [], [], []]
        hands: List[List[int]] = []
        for i in range(4):
            raw_hand = tehais_raw[i] if i < len(tehais_raw) else []
            hand_bases = [b for b in (_tile_to_base(x) for x in raw_hand) if b is not None]
            hands.append(hand_bases)

        turns = [0, 0, 0, 0]
        pending_riichi_actor: Optional[int] = None
        riichi_declared = [False, False, False, False]
        same_turn_furiten = [False, False, False, False]
        riichi_furiten = [False, False, False, False]
        discards_norm: List[Set[int]] = [set(), set(), set(), set()]
        calls: List[List[CallEventSnapshot]] = [[], [], [], []]

        dora_bases: List[int] = []
        dora0 = _tile_to_base(self.game_data.get("dora_marker"))
        if dora0 is not None:
            dora_bases.append(dora0)

        for ev in self.game_events:
            if not isinstance(ev, dict):
                continue
            ev_type = str(ev.get("type") or "")
            actor = int(ev.get("actor", 0) or 0)
            if actor < 0 or actor > 3:
                actor = 0

            if ev_type == "tsumo":
                b = _tile_to_base(ev.get("pai"))
                if b is not None:
                    hands[actor].append(b)
                same_turn_furiten[actor] = False
                continue

            if ev_type in ("riichi", "reach"):
                pending_riichi_actor = actor
                continue

            if ev_type in ("riichi_accepted", "reach_accepted"):
                continue

            if ev_type == "dora":
                dora_new = _tile_to_base(
                    ev.get("dora_marker") or ev.get("hai") or ev.get("pai")
                )
                if dora_new is not None:
                    dora_bases.append(dora_new)
                continue

            if ev_type in ("chii", "pon", "kan", "daiminkan", "kakan", "ankan"):
                self._apply_call_event(ev_type, actor, ev, turns, hands, calls)
                continue

            if ev_type != "dahai":
                continue

            discarded_base = _tile_to_base(ev.get("pai"))
            if discarded_base is None:
                continue

            turns[actor] += 1
            turn = turns[actor]
            _remove_one_tile(hands[actor], discarded_base)

            if pending_riichi_actor == actor:
                riichi_declared[actor] = True
                pending_riichi_actor = None

            discards_norm[actor].add(_normalize_base(discarded_base))
            snapshot = DiscardSnapshot(
                player_id=actor,
                turn=turn,
                hand_bases=tuple(hands[actor]),
                active_calls=tuple(calls[actor]),
                discards_norm=tuple(sorted(discards_norm[actor])),
                riichi_declared=riichi_declared[actor],
                same_turn_furiten=same_turn_furiten[actor],
                riichi_furiten=riichi_furiten[actor],
                dora_bases=tuple(dora_bases),
                round_wind=self.round_wind,
                player_wind=self.player_winds[actor],
            )
            self.snapshots[(actor, turn)] = snapshot

            # 该打牌被三家“看到”，若本可荣和但未和，触发同巡振听；立直后则转永久振听
            for pid in range(4):
                if pid == actor:
                    continue
                temp_snapshot = DiscardSnapshot(
                    player_id=pid,
                    turn=turns[pid],
                    hand_bases=tuple(hands[pid]),
                    active_calls=tuple(calls[pid]),
                    discards_norm=tuple(sorted(discards_norm[pid])),
                    riichi_declared=riichi_declared[pid],
                    same_turn_furiten=same_turn_furiten[pid],
                    riichi_furiten=riichi_furiten[pid],
                    dora_bases=tuple(dora_bases),
                    round_wind=self.round_wind,
                    player_wind=self.player_winds[pid],
                )
                r = self._evaluate_snapshot(temp_snapshot, discarded_base)
                if r.get("deal_in_hit"):
                    same_turn_furiten[pid] = True
                    if riichi_declared[pid]:
                        riichi_furiten[pid] = True

    def _apply_call_event(
        self,
        ev_type: str,
        actor: int,
        ev: Dict,
        turns: List[int],
        hands: List[List[int]],
        calls: List[List[CallEventSnapshot]],
    ) -> None:
        consumed_bases = [b for b in (_tile_to_base(x) for x in (ev.get("consumed") or [])) if b is not None]
        pai_base = _tile_to_base(ev.get("pai"))
        from_discard_turn = turns[actor] + 1
        calls[actor].append(
            CallEventSnapshot(
                call_type=ev_type,
                pai_base=-1 if pai_base is None else pai_base,
                consumed_bases=tuple(consumed_bases),
                from_discard_turn=from_discard_turn,
            )
        )

        # 更新暗手张数（用于后续荣和判定）
        if ev_type == "chii":
            for b in consumed_bases[:2]:
                _remove_one_tile(hands[actor], b)
        elif ev_type == "pon":
            for b in consumed_bases[:2]:
                _remove_one_tile(hands[actor], b)
        elif ev_type == "daiminkan":
            for b in consumed_bases[:3]:
                _remove_one_tile(hands[actor], b)
        elif ev_type == "ankan":
            for b in consumed_bases[:4]:
                _remove_one_tile(hands[actor], b)
        elif ev_type == "kakan":
            # 加杠只会再从手里出 1 张
            if pai_base is not None:
                _remove_one_tile(hands[actor], pai_base)
            elif consumed_bases:
                _remove_one_tile(hands[actor], consumed_bases[0])
        elif ev_type == "kan":
            remove_n = 3 if len(consumed_bases) >= 3 else len(consumed_bases)
            for b in consumed_bases[:remove_n]:
                _remove_one_tile(hands[actor], b)

    def _evaluate_snapshot(self, snapshot: DiscardSnapshot, target_base: int) -> Dict:
        waits = self._get_waits(snapshot)
        waits_snapshot = [_base_to_wait_label(b) for b in sorted(waits)]
        furiten_state, furiten_reason = self._get_furiten_state(snapshot, waits)

        target_norm = _normalize_base(target_base)
        if target_norm not in waits:
            return {
                "deal_in_hit": False,
                "deal_in_point": 0,
                "furiten_state": furiten_state,
                "furiten_reason": furiten_reason or "目标牌不在当时待牌",
                "waits_snapshot": waits_snapshot,
            }

        if furiten_state != "none":
            return {
                "deal_in_hit": False,
                "deal_in_point": 0,
                "furiten_state": furiten_state,
                "furiten_reason": furiten_reason,
                "waits_snapshot": waits_snapshot,
            }

        point = self._get_ron_point(snapshot, target_base)
        if point <= 0:
            return {
                "deal_in_hit": False,
                "deal_in_point": 0,
                "furiten_state": "none",
                "furiten_reason": "无役或牌型不成立",
                "waits_snapshot": waits_snapshot,
            }

        return {
            "deal_in_hit": True,
            "deal_in_point": int(point),
            "furiten_state": "none",
            "furiten_reason": "",
            "waits_snapshot": waits_snapshot,
        }

    def _get_furiten_state(self, snapshot: DiscardSnapshot, waits: Set[int]) -> Tuple[str, str]:
        if snapshot.riichi_furiten:
            return ("riichi", "立直振听")
        if snapshot.same_turn_furiten:
            return ("same_turn", "同巡振听")
        if waits and set(snapshot.discards_norm).intersection(waits):
            return ("discard", "舍张振听")
        return ("none", "")

    def _snapshot_signature(self, snapshot: DiscardSnapshot) -> Tuple:
        hand_sig = tuple(sorted(snapshot.hand_bases))
        calls_sig = tuple(
            (c.call_type, c.pai_base, c.consumed_bases)
            for c in snapshot.active_calls
        )
        return (
            hand_sig,
            calls_sig,
            bool(snapshot.riichi_declared),
            tuple(snapshot.dora_bases),
            int(snapshot.player_wind),
            int(snapshot.round_wind),
        )

    def _get_waits(self, snapshot: DiscardSnapshot) -> Set[int]:
        sig = self._snapshot_signature(snapshot)
        cached = self._waits_cache.get(sig)
        if cached is not None:
            return cached

        waits: Set[int] = set()
        # 性能优化：如果不听牌，则不可能荣和
        if not self._is_tenpai_bases(snapshot.hand_bases):
            self._waits_cache[sig] = waits
            return waits

        for base in range(34):
            if self._get_ron_point_by_sig(snapshot, sig, base) > 0:
                waits.add(base)
        self._waits_cache[sig] = waits
        return waits

    def _is_tenpai_bases(self, hand_bases: Union[List[int], Tuple[int, ...]]) -> bool:
        """判断手牌是否听牌（向听数=0）"""
        try:
            from mahjong.shanten import Shanten
        except ImportError:
            return False
        
        tiles_34 = [0] * 34
        for b in hand_bases:
            b = RED_TO_STANDARD.get(b, b)
            if 0 <= b < 34:
                tiles_34[b] += 1
        
        # 自动推断已副露的面子数：(13 - hand_len) // 3
        # mahjong 库的 calculate_shanten 在 13 张牌以下时会自动处理面子
        try:
            shanten = Shanten().calculate_shanten(tiles_34, use_chiitoitsu=True, use_kokushi=True)
            return shanten == 0
        except Exception:
            return False

    def _get_ron_point(self, snapshot: DiscardSnapshot, target_base: int) -> int:
        sig = self._snapshot_signature(snapshot)
        return self._get_ron_point_by_sig(snapshot, sig, target_base)

    def _get_ron_point_by_sig(self, snapshot: DiscardSnapshot, sig: Tuple, target_base: int) -> int:
        key = (sig, int(target_base))
        if key in self._ron_cache:
            return self._ron_cache[key]
        point = self._calculate_ron_point(snapshot, target_base)
        self._ron_cache[key] = point
        return point

    def _calculate_ron_point(self, snapshot: DiscardSnapshot, target_base: int) -> int:
        if not HAS_MAHJONG or self._calculator is None:
            return 0

        melds, meld_tile_bases = self._build_melds(snapshot.active_calls)

        all_tile_bases = list(snapshot.hand_bases) + [target_base] + meld_tile_bases
        if not all_tile_bases:
            return 0

        tiles_136 = [_base_to_136(b) for b in all_tile_bases]
        win_tile_136 = _base_to_136(target_base)
        dora_136 = [_base_to_136(_normalize_base(b)) for b in snapshot.dora_bases]

        options = OptionalRules(
            has_open_tanyao=True,
            has_aka_dora=True,
        )
        config = HandConfig(
            is_tsumo=False,
            is_riichi=bool(snapshot.riichi_declared),
            player_wind=snapshot.player_wind,
            round_wind=snapshot.round_wind,
            options=options,
        )

        try:
            response = self._calculator.estimate_hand_value(
                tiles=tiles_136,
                win_tile=win_tile_136,
                melds=melds,
                dora_indicators=dora_136,
                config=config,
                use_hand_divider_cache=True,
            )
        except Exception:
            return 0

        if getattr(response, "error", None):
            return 0

        cost = getattr(response, "cost", None) or {}
        return int(cost.get("main") or 0)

    def _build_melds(self, calls: Tuple[CallEventSnapshot, ...]) -> Tuple[List, List[int]]:
        if not calls:
            return [], []

        records: List[Dict] = []
        for c in calls:
            ctype = c.call_type
            pai_base = c.pai_base
            consumed = list(c.consumed_bases)

            if ctype == "kakan":
                upgraded = False
                for rec in reversed(records):
                    if rec["type"] in ("pon", "kakan", "kan", "daiminkan"):
                        if _normalize_base(rec["pai_base"]) == _normalize_base(pai_base):
                            rec["type"] = "kakan"
                            while len(rec["tiles"]) < 4:
                                rec["tiles"].append(pai_base)
                            rec["tiles"] = rec["tiles"][:4]
                            rec["pai_base"] = pai_base
                            upgraded = True
                            break
                if upgraded:
                    continue

            if ctype == "chii":
                tiles = (consumed + ([pai_base] if pai_base >= 0 else []))[:3]
            elif ctype == "pon":
                tiles = (consumed + ([pai_base] if pai_base >= 0 else []))[:3]
            elif ctype == "ankan":
                seed = consumed if consumed else ([pai_base] * 4 if pai_base >= 0 else [])
                while len(seed) < 4 and seed:
                    seed.append(seed[-1])
                tiles = seed[:4]
            else:
                seed = consumed + ([pai_base] if pai_base >= 0 else [])
                if not seed and pai_base >= 0:
                    seed = [pai_base]
                while len(seed) < 4 and seed:
                    seed.append(seed[-1])
                tiles = seed[:4]

            if not tiles:
                continue
            records.append({"type": ctype, "pai_base": pai_base, "tiles": tiles})

        melds = []
        meld_tile_bases: List[int] = []
        for rec in records:
            ctype = rec["type"]
            tiles_bases = rec["tiles"]
            meld_tile_bases.extend(tiles_bases)
            if not HAS_MAHJONG:
                continue

            if ctype == "chii":
                meld_type = Meld.CHI
                opened = True
            elif ctype == "pon":
                meld_type = Meld.PON
                opened = True
            elif ctype == "ankan":
                meld_type = Meld.KAN
                opened = False
            elif ctype == "kakan":
                meld_type = Meld.SHOUMINKAN
                opened = True
            else:
                meld_type = Meld.KAN
                opened = True

            pai_base = rec["pai_base"]
            called_tile = _base_to_136(pai_base) if pai_base >= 0 else None
            tiles_136 = [_base_to_136(b) for b in tiles_bases]
            try:
                melds.append(
                    Meld(
                        meld_type=meld_type,
                        tiles=tiles_136,
                        opened=opened,
                        called_tile=called_tile,
                    )
                )
            except Exception:
                continue

        return melds, meld_tile_bases
