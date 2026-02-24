# tenhou-paifu-to-json → GameState / Discard 转换示例

## 一、tenhou-paifu-to-json 输出结构

```json
{
  "games": [
    {
      "data": {
        "bakaze": "E",
        "dora_marker": "9p",
        "honba": 0,
        "kyoku": 1,
        "kyotaku": 0,
        "oya": 0,
        "scores": [25000, 25000, 25000, 25000],
        "tehais": [
          ["8m", "8m", "1z", "4p", "2s", "1m", "7z", "0m", "6z", "7s", "6p", "2z", "5p"],
          ["7m", "2m", "3s", "6z", "4p", "6z", "5z", "3m", "6s", "1s", "2z", "8p", "5z"],
          ["9m", "7z", "1m", "5m", "7p", "2m", "6s", "9s", "7s", "4m", "1p", "1p", "2s"],
          ["7p", "1z", "3m", "1s", "6m", "2z", "7p", "8s", "7m", "1s", "3p", "9m", "2m"]
        ]
      },
      "game": [
        { "junme": 1, "actor": 0, "pai": "9p", "type": "tsumo" },
        { "junme": 1, "actor": 0, "pai": "2z", "type": "dahai", "tsumogiri": false },
        { "junme": 1, "actor": 1, "pai": "3p", "type": "tsumo" },
        { "junme": 1, "actor": 1, "pai": "6s", "type": "dahai", "tsumogiri": false },
        { "junme": 2, "actor": 2, "consumed": ["3z", "3z"], "pai": "3z", "target": 0, "type": "pon" },
        { "junme": 2, "actor": 2, "pai": "2p", "type": "dahai", "tsumogiri": false }
      ]
    }
  ]
}
```

### tenhou-paifu-to-json 要点

| 字段 | 含义 | 示例 |
|------|------|------|
| `data.bakaze` | 场风 E=东/S=南/W=西 | `"E"` |
| `data.kyoku` | 场内局号 1-4 | `1` = 东1局 |
| `data.honba` | 本场数 | `0` |
| `data.tehais[i]` | 玩家 i 的初始手牌，字符串列表 | `["8m", "8m", "1z", ...]` |
| `game[].type` | 事件类型 | `tsumo`, `dahai`, `pon`, `chii`… |
| `game[].actor` | 玩家编号 0-3 | `0` |
| `game[].pai` | 牌符 "1m"-"9m", "1p"-"9p", "0m"=赤5万 | `"9p"` |
| `game[].tsumogiri` | 是否摸切（仅 dahai） | `false` |
| `game[].consumed` | 副露时自己出的牌（chii/pon） | `["3z", "3z"]` |

---

## 二、转换后的 GameState / Discard 结构

每个小局（`games[i]`）对应 4 个 `GameState`（玩家 0–3）。

### 1. GameState（以玩家 0 为例）

```python
GameState(
    player_id=0,
    round_num=0,        # bakaze E + kyoku 1 → 东1局
    honba=0,
    oya=0,
    discards=[
        Discard(turn=1, tile=112, is_tsumogiri=False, ...),  # 2z: base 28 → tile 112
    ],
    hand_tiles={...},   # 终局手牌（set）
    initial_hand={...}, # 初始 13 张
    hand_tiles_history=[
        # 打出 2z 后的手牌（list，每张牌为 base*4，同种牌可重复以保留枚数）
        # 例：初始 8m,8m,1z,4p,2s,1m,7z,0m,6z,7s,6p,2z,5p → tsumo 9p → dahai 2z → 13 张
        # 8m=28, 1z=108, 4p=48, 2s=76, 1m=0, 7z=128, 0m=16, 6z=124, 7s=96, 6p=56, 5p=52, 9p=68
        [28, 28, 108, 48, 76, 0, 128, 16, 124, 96, 56, 52, 68],
    ],
    dora_indicators=[36],  # 9p 的 base=9, base*4=36
    visible_tiles=Counter(...),
    calls=[],
)
```

### 2. Discard

```python
Discard(
    turn=1,              # 该玩家第几次出牌（来自 turns_count）
    tile=112,            # 2z（南）: base 28, tile = 28*4 = 112
    is_tsumogiri=False,  # 对应 JSON 的 tsumogiri
    riichi_happened=False,
    call_happened=False,
)
```

### 3. CallInfo（以 pon 为例）

tenhou-paifu-to-json：

```json
{ "junme": 2, "actor": 2, "consumed": ["3z", "3z"], "pai": "3z", "target": 0, "type": "pon" }
```

转换后：

```python
CallInfo(
    call_type="pon",
    pai="3z",
    consumed=["3z", "3z"],
    from_discard_turn=2,  # 该副露后第一次舍牌的巡目
)
```

---

## 三、牌符与编码对应关系

| tenhou 牌符 | base (0-33) | tile (0-135, 用 base*4) |
|-------------|-------------|--------------------------|
| 1m | 0 | 0 |
| 5m | 4 | 16 |
| 0m (赤5万) | 4 | 16 |
| 1p | 9 | 36 |
| 5p | 13 | 52 |
| 9p | 17 | 68 |
| 1z (东) | 27 | 108 |
| 2z (南) | 28 | 112 |
| 7z (中) | 33 | 132 |

手牌内部用 `List[int]` 存，同一张牌多张用相同 `base*4` 重复出现，例如 3 张 5p → `[52, 52, 52]`。

---

## 四、关键转换逻辑一览

| tenhou-paifu-to-json | → | GameState / Discard |
|----------------------|---|---------------------|
| `data.kyoku` + `data.bakaze` | → | `round_num` (0=东1, 4=南1, …) |
| `data.tehais[i]` | → | `initial_hand`, 解析中的 `hands[i]` |
| `game[].type == "tsumo"` | → | `hands[actor].append(t)` |
| `game[].type == "dahai"` | → | `Discard` + 从 `hands` 移除 + `hand_tiles_history.append(list(hands))` |
| `game[].type == "pon"` | → | `CallInfo` + 从 `hands` 移除 consumed |
| `data.dora_marker` | → | `dora_indicators` |
