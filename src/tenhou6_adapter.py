"""
Tenhou6 格式适配器

将 tenhou-paifu-to-json 输出的 tenhou6 JSON 格式解析为与 MjlogParser 兼容的
GameState / Discard 结构，便于分析层无缝切换。

Tenhou6 格式参考: https://github.com/Riichi-Mahjong-Statistics-Seminar/tenhou-paifu-to-json
"""

import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter as PyCounter

from .mjlog_parser import GameState, Discard, TileUtils, CallInfo

logger = logging.getLogger(__name__)

# tenhou6 牌符 -> base_tile (0-36)
# "1m"-"9m" -> 0-8, "1p"-"9p" -> 9-17, "1s"-"9s" -> 18-26
# "1z"-"7z" 或 东/南/西/北/白/发/中 -> 27-33
# "0m"/"0p"/"0s" -> 赤五独立 base 34/35/36（不与 5m/5p/5s 共用）
TENHOU6_TO_BASE: Dict[str, int] = {}
for suit, offset in [("m", 0), ("p", 9), ("s", 18)]:
    for n in range(1, 10):
        TENHOU6_TO_BASE[f"{n}{suit}"] = offset + n - 1
    TENHOU6_TO_BASE[f"0{suit}"] = 34 + {"m": 0, "p": 1, "s": 2}[suit]  # 0m=34, 0p=35, 0s=36
for i in range(1, 8):
    TENHOU6_TO_BASE[f"{i}z"] = 26 + i
for cn, base in [("东", 27), ("南", 28), ("西", 29), ("北", 30), ("白", 31), ("发", 32), ("中", 33)]:
    TENHOU6_TO_BASE[cn] = base


def _pai_to_tile(pai: str) -> int:
    """tenhou6 牌符 -> tile 编码 (0-147)，使用 base*4 作为代表（用于舍牌等单张场景）"""
    base = TENHOU6_TO_BASE.get(pai)
    if base is None:
        raise ValueError(f"未知牌符: {pai}")
    return base * 4


def _tehai_to_hand_tiles(tehais: List[str]) -> List[int]:
    """将 tehais (牌符列表) 转为 tile 编码列表。使用 base*4，允许重复以保留枚数。"""
    result = []
    for pai in tehais:
        base = TENHOU6_TO_BASE.get(pai)
        if base is None:
            continue
        result.append(base * 4)
    return result


def _parse_round_from_tenhou6(
    game_data: Dict[str, Any],
    game_events: List[Dict[str, Any]],
) -> List[GameState]:
    """
    从 tenhou6 单局数据解析出 4 个玩家的 GameState。
    
    game_data: { bakaze, dora_marker, honba, kyoku, oya, scores, tehais }
    game_events: [ { actor, type, pai?, tsumogiri?, consumed?, target? }, ... ]
    """
    oya = int(game_data.get("oya", 0))
    honba = int(game_data.get("honba", 0))
    kyoku = int(game_data.get("kyoku", 1))  # 场内局号 1-4
    bakaze = (game_data.get("bakaze") or "E").upper()
    # tenhou-paifu-to-json: kyoku=1-4 为场内局号，bakaze 为场风 E=东/S=南/W=西
    # round_num: 0=东1, 1=东2, 2=东3, 3=东4, 4=南1, 5=南2, ...
    if bakaze in ("E", "東"):
        field_offset = 0
    elif bakaze in ("S", "南"):
        field_offset = 4
    elif bakaze in ("W", "西"):
        field_offset = 8
    else:
        field_offset = 0
    round_num = field_offset + (kyoku - 1)
    
    # 初始手牌
    tehais_raw = game_data.get("tehais", [[], [], [], []])
    
    game_states = [
        GameState(player_id=i, round_num=round_num, honba=honba, oya=oya)
        for i in range(4)
    ]
    
    for i, th in enumerate(tehais_raw):
        if i < 4 and th:
            tiles = _tehai_to_hand_tiles(th)
            game_states[i].hand_tiles = set(tiles)  # GameState 仍用 set，解析中用 list 便于保留枚数
            game_states[i].initial_hand = set(tiles)
    
    # 宝牌指示牌
    dora_marker = game_data.get("dora_marker")
    if dora_marker:
        base = TENHOU6_TO_BASE.get(dora_marker)
        if base is not None:
            ind = base * 4
            for s in game_states:
                s.dora_indicators = [ind]
                s.visible_tiles[ind] += 1
    
    # 追踪每个玩家的手牌、舍牌、巡目、立直/副露状态。用 list 存储以正确保留同种牌枚数
    hands: List[List[int]] = [_tehai_to_hand_tiles(tehais_raw[i]) if i < len(tehais_raw) and tehais_raw[i] else [] for i in range(4)]
    turns_count = [0] * 4
    riichi_seen = False
    riichi_declared = [False] * 4
    call_seen = False
    # 立直 step1（riichi/reach）：下一张该玩家的 dahai 即为立直宣言牌
    pending_riichi_actor: Optional[int] = None
    dora_indicators_list = list(game_states[0].dora_indicators)
    
    for ev in game_events:
        if ev is None:
            continue
        ev_type = ev.get("type")
        actor = ev.get("actor", 0)
        
        if ev_type == "tsumo":
            pai = ev.get("pai")
            if pai:
                t = _pai_to_tile(pai)
                hands[actor].append(t)
        
        elif ev_type == "dahai":
            pai = ev.get("pai")
            if not pai:
                continue
            base = TENHOU6_TO_BASE.get(pai)
            if base is None:
                continue
            t = base * 4  # Discard 编码用 base*4 代表
            tsumogiri = ev.get("tsumogiri", False)
            turns_count[actor] += 1
            
            # 若上一事件为该玩家的立直 step1，则此 dahai 为立直宣言牌
            is_riichi_decl = actor == pending_riichi_actor
            if is_riichi_decl:
                pending_riichi_actor = None
            
            d = Discard(
                turn=turns_count[actor],
                tile=t,
                is_tsumogiri=tsumogiri,
                riichi_happened=riichi_seen,
                opponent_riichi_happened=any(riichi_declared[i] for i in range(4) if i != actor),
                call_happened=call_seen,
                is_riichi_declaration=is_riichi_decl,
            )
            game_states[actor].discards.append(d)
            
            # 移除手牌中该 base 的任意一张
            for idx, x in enumerate(hands[actor]):
                if x // 4 == base:
                    del hands[actor][idx]
                    break
            game_states[actor].hand_tiles_history.append(list(hands[actor]))
            
            for i in range(4):
                if i != actor:
                    game_states[i].visible_tiles[t] += 1
        
        elif ev_type in ("reach", "riichi", "riichi_accepted"):
            # tenhou-paifu-to-json 输出 "riichi"/"riichi_accepted"，部分数据为 "reach"/"reach_accepted"
            # 任一立直相关事件均标记 riichi_seen，确保同巡内后续舍牌正确获得 riichi_happened=True
            riichi_seen = True
            if isinstance(actor, int) and 0 <= actor < 4:
                riichi_declared[actor] = True
            # 仅 step1（riichi/reach，非 riichi_accepted）时，下一张该玩家的 dahai 为立直宣言牌
            if ev_type in ("reach", "riichi"):
                pending_riichi_actor = actor
        elif ev_type in ("chii", "pon", "kan", "daiminkan", "kakan", "ankan"):
            call_seen = True
            pai = ev.get("pai")
            consumed = ev.get("consumed", [])
            consumed_str = [c for c in consumed if isinstance(c, str)]
            if pai and consumed_str:
                from_discard = turns_count[actor] + 1  # 副露后下一张舍牌的巡目
                game_states[actor].calls.append(
                    CallInfo(call_type=ev_type, pai=pai, consumed=consumed_str, from_discard_turn=from_discard)
                )
            # 可见牌：pai（鸣牌）和 consumed（自己贡献）都对其他玩家可见
            all_tiles = ([pai] if pai else []) + consumed
            for p in all_tiles:
                if p:
                    tb = TENHOU6_TO_BASE.get(p)
                    if tb is not None:
                        tt = tb * 4
                        for i in range(4):
                            if i != actor:
                                game_states[i].visible_tiles[tt] += 1
            # 仅从手牌移除 consumed（自己贡献的牌）；pai 来自对手河牌，不在手牌中，不可移除
            for p in consumed_str:
                tb = TENHOU6_TO_BASE.get(p)
                if tb is not None:
                    to_remove = [x for x in hands[actor] if x // 4 == tb]
                    if to_remove:
                        hands[actor].remove(to_remove[0])
        
        elif ev_type == "dora":
            # 追加宝牌指示牌
            pai = ev.get("hai") or ev.get("pai")
            if pai:
                base = TENHOU6_TO_BASE.get(pai)
                if base is not None:
                    ind = base * 4
                    dora_indicators_list.append(ind)
                    for s in game_states:
                        s.dora_indicators = list(dora_indicators_list)
                        s.visible_tiles[ind] += 1

        elif ev_type == "agari":
            # 和牌：actor=和牌者，fromwho=供牌者；actor==fromwho 则为自摸
            actor = ev.get("actor", 0)
            fromwho = ev.get("fromwho", actor)
            if actor not in game_states[0].round_winners:
                game_states[0].round_winners.append(actor)
            if actor != fromwho:
                # 荣和：fromwho 放铳（一炮双响时同一人只记 1 次）
                if game_states[0].round_deal_in is None:
                    game_states[0].round_deal_in = fromwho

        elif ev_type == "ryuukyoku":
            # 流局：无和牌、无放铳，round_winners/round_deal_in 保持空/None
            pass

    # 将结局信息同步到 4 个 GameState（每个玩家共享同一局的结局）
    round_winners = game_states[0].round_winners
    round_deal_in = game_states[0].round_deal_in
    for s in game_states:
        s.round_winners = list(round_winners)
        s.round_deal_in = round_deal_in

    # 同步最终手牌（转为 set 以兼容 GameState 类型）
    for i in range(4):
        game_states[i].hand_tiles = set(hands[i])
    
    return game_states


def _round_identity(data: Dict[str, Any]) -> Tuple[str, int, int, int]:
    """提取局的唯一标识 (bakaze, kyoku, honba, oya)，用于一炮双响去重"""
    bakaze = (data.get("bakaze") or "E").upper()
    kyoku = int(data.get("kyoku", 1))
    honba = int(data.get("honba", 0))
    oya = int(data.get("oya", 0))
    return (bakaze, kyoku, honba, oya)


def parse_tenhou6_json(json_data: Dict[str, Any]) -> List[GameState]:
    """
    解析 tenhou6 JSON，返回与 MjlogParser.parse() 相同结构的 GameState 列表。
    
    一炮双响（一人打牌两家荣和）时，tenhou-paifu-to-json 会为每个 AGARI 产生一个 game 条目，
    导致同一局被重复输出。此处按 (bakaze, kyoku, honba, oya) 去重，只保留每个局的首次出现。
    
    Args:
        json_data: tenhou6 格式的 dict（来自 tenhou-paifu-to-json 输出）
        
    Returns:
        List[GameState]，每局 4 个玩家，按 round 顺序排列
    """
    games = json_data.get("games", [])
    all_states: List[GameState] = []
    seen_rounds: set = set()
    
    for g in games:
        data = g.get("data", {})
        rid = _round_identity(data)
        if rid in seen_rounds:
            # 一炮双响/三家和了：同一局被 tenhou-paifu-to-json 输出多次，只统计一次
            continue
        seen_rounds.add(rid)
        game_events = g.get("game", [])
        states = _parse_round_from_tenhou6(data, game_events)
        all_states.extend(states)
    
    return all_states


def load_tenhou6_json(json_str: str) -> List[GameState]:
    """从 JSON 字符串解析 tenhou6 格式"""
    data = json.loads(json_str)
    return parse_tenhou6_json(data)


def xml_to_tenhou6_json(xml_content: str, paifu_cli_path: Optional[str] = None) -> str:
    """
    调用 tenhou-paifu-to-json 将 XML 转为 tenhou6 JSON 字符串。
    
    若未安装 tenhou-paifu-to-json，则返回空字符串（调用方应回退到 MjlogParser）。
    
    Args:
        xml_content: mjlog XML 内容
        paifu_cli_path: tenhou-paifu-to-json 的 main.py 路径，None 时自动查找
    """
    import subprocess
    import tempfile
    import sys
    from pathlib import Path
    
    # 尝试定位 tenhou-paifu-to-json
    if paifu_cli_path is None:
        try:
            import tenhou_paifu_to_json  # type: ignore
            # 若作为包安装，可能有 convert 函数
            return ""
        except ImportError:
            pass
        # 检查常见位置（支持 tenhou-paifu-to-json 或 tenhou-paifu-to-json-main）
        for p in [
            Path(__file__).parent.parent / "tenhou-paifu-to-json-main" / "src" / "main.py",
            Path(__file__).parent.parent / "tenhou-paifu-to-json" / "src" / "main.py",
            Path(__file__).parent.parent / "tenhou-paifu-to-json" / "main.py",
            Path("tenhou-paifu-to-json-main/src/main.py"),
            Path("tenhou-paifu-to-json/src/main.py"),
            Path("tenhou-paifu-to-json/main.py"),
        ]:
            if p.exists():
                paifu_cli_path = str(p)
                break
    if not paifu_cli_path:
        return ""
    
    xml_path = None
    json_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        ) as fx:
            fx.write(xml_content)
            xml_path = fx.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fj:
            json_path = fj.name

        subprocess.run(
            [sys.executable, paifu_cli_path, xml_path, json_path],
            check=True,
            capture_output=True,
            timeout=30,
        )
        with open(json_path, "r", encoding="utf-8") as f:
            out = f.read()
        return out
    except subprocess.TimeoutExpired as e:
        logger.debug(f"tenhou-paifu-to-json 超时: {e}")
        return ""
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")
        logger.debug(f"tenhou-paifu-to-json 退出码 {e.returncode}: {stderr[:200]}")
        return ""
    except Exception as e:
        logger.debug(f"tenhou-paifu-to-json 转换失败: {e}")
        return ""
    finally:
        import os
        for p in [xml_path, json_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
