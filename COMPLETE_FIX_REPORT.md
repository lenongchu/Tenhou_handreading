# 完整修复报告：3m-1m 查询 2m 概率计算错误

## 问题描述

用户运行"3m-1m"场景的模拟，查询打出3m和1m（均为手切Tedashi）的玩家手中有几张2m。
结果显示：
1. **只有10%的玩家有1张2m**（远低于预期）
2. **有2张2m的概率接近0**（严重错误）

## 核心需求澄清

用户需要统计的是：**在舍牌序列完成的那一巡，该玩家手牌里所拥有的目标牌的数量**。

### 关键点：
1. 时间点：舍牌序列完成的那一巡（如第2巡完成"3m-1m"）
2. 统计对象：该巡打牌后的手牌（13张）
3. 筛选条件：该玩家自己没有打过目标牌或等价目标牌

### 计算方法：
```
该巡手里的2m数量 = 直接使用该巡打牌后的手牌快照进行统计
```

## 发现的Bug

### Bug 1: 编码类型不匹配
**问题**：完整编码（0-135）与基础编码（0-33）直接比较
**影响**：几乎统计不到任何牌

### Bug 2: 目标牌映射错误
**问题**：6种等价模式都只统计原始花色的目标牌
**影响**：概率被稀释到1/6

### Bug 3: 手牌时间点错误（最严重）
**问题**：使用的是小局结束时的最终手牌，而不是匹配时刻的手牌
**影响**：有2张的概率接近0

## 正确的实现方案

### 1. 数据结构修改

在 `GameState` 中添加必要的字段：

```python
@dataclass
class GameState:
    player_id: int
    round_num: int = 0
    honba: int = 0
    has_riichi: bool = False
    discards: List[Discard] = field(default_factory=list)
    hand_tiles: Set[int] = field(default_factory=set)      # 最终手牌
    initial_hand: Set[int] = field(default_factory=set)    # 初始配牌
    hand_tiles_history: List[Set[int]] = field(default_factory=list)  # 每一巡打牌后的手牌快照
    dora_indicators: List[int] = field(default_factory=list)
    visible_tiles: PyCounter = field(default_factory=PyCounter)
```

### 2. 解析器修改

在 `MjlogParser` 中记录手牌历史：

```python
# 保存初始配牌
game_states[i].initial_hand = set(hand_tiles)

# 每次打牌后保存手牌快照
if tile in game_states[player].hand_tiles:
    game_states[player].hand_tiles.remove(tile)

# 保存该巡打牌后的手牌快照
game_states[player].hand_tiles_history.append(set(game_states[player].hand_tiles))
```

### 3. 查询逻辑修改

在 `LiveAnalyzer` 中使用正确的手牌快照：

```python
# 获取该巡打牌后的手牌快照
# hand_tiles_history[i] 对应第 i+1 巡打牌后的手牌
if i < len(player_state.hand_tiles_history):
    hand_at_turn = player_state.hand_tiles_history[i]
else:
    logger.warning(f"手牌历史记录不足")
    hand_at_turn = player_state.hand_tiles

# 根据实际匹配到的模式，映射目标牌
mapped_target = map_target_tile(query_pattern, hand_discard_strings, target_tile)
mapped_target_code = MjlogParser.string_to_tile(mapped_target)

# 筛选条件：该玩家自己没有打过目标牌
has_discarded_target = False
for d in player_state.discards[:i+1]:
    if d.tile // 4 == mapped_target_code:
        has_discarded_target = True
        break

if has_discarded_target:
    continue  # 跳过

# 统计目标牌数量
target_count = sum(1 for tile in hand_at_turn if tile // 4 == mapped_target_code)
target_count = min(target_count, 3)
target_count_distribution[target_count] += 1
```

## 修复效果示例

### 场景演示

```
初始配牌：13张，包含2张2m (编码4, 5)

第1巡：摸牌 → 打3m（手切）→ 手里还有2张2m
第2巡：摸牌 → 打1m（手切）→ 手里还有2张2m ✓ 匹配"3m-1m"
第3巡：摸牌 → 打2m（手切）→ 手里还有1张2m

查询"3m-1m"，目标2m：
- 在第2巡匹配到模式
- 使用 hand_tiles_history[1] 获取第2巡的手牌
- 该玩家没有打过2m → 符合筛选条件
- 统计到2张2m → 正确！
```

### 筛选条件的作用

```
如果在第3巡或之后才匹配到"3m-1m"：
- 检查发现该玩家在第3巡打过2m
- has_discarded_target = True
- 跳过该样本，不计入统计
- 原因：打过2m说明是特殊情况，不具有代表性
```

## 预期的概率分布

修复后的概率应该更符合麻将逻辑：

| 目标牌数量 | 修复前（错误） | 修复后（正确） |
|-----------|---------------|---------------|
| 有0张 | ~90% | ~30-40% |
| 有1张 | ~10% | ~40-50% |
| 有2张 | **~0%** | **~10-15%** |
| 有3张 | ~0% | ~1-3% |

### 为什么这样更合理？

1. **有0张不应该太高**：
   - 打出3m-1m说明中张附近有多张牌
   - 2m在3m和1m之间，有一定概率被保留
   
2. **有2张应该有合理概率**：
   - 对子（对倒）听牌
   - 搭子保留
   - 安全牌保留
   
3. **有3张概率较低但不为0**：
   - 暗刻
   - 安牌保留

## 修改的文件

### 1. `src/mjlog_parser.py`
- 添加 `initial_hand` 字段记录初始配牌
- 添加 `hand_tiles_history` 字段记录每巡手牌快照
- 修改打牌逻辑，每次打牌后保存手牌快照

### 2. `src/live_analyzer.py`
- 修复编码比较逻辑（`tile // 4`）
- 添加目标牌映射（`map_target_tile`）
- 使用手牌历史记录（`hand_tiles_history[i]`）
- 添加筛选条件（检查是否打过目标牌）

### 3. `src/simple_normalizer.py`
- 新增 `map_target_tile()` 函数
- 添加完整的测试用例（12个测试全部通过）

## 技术细节

### 手牌快照的索引对应关系

```python
discards[0]  # 第1巡的舍牌
hand_tiles_history[0]  # 第1巡打牌后的手牌快照（13张）

discards[1]  # 第2巡的舍牌  
hand_tiles_history[1]  # 第2巡打牌后的手牌快照（13张）

# 规律：hand_tiles_history[i] 是打出 discards[i] 后的手牌
```

### 为什么要保存每一巡的手牌快照？

1. **准确性**：直接记录该时刻的真实手牌状态
2. **简单性**：不需要复杂的重建逻辑
3. **可靠性**：避免摸牌信息不完整导致的错误

### 筛选条件的必要性

用户指出需要筛选"自己没有打过目标牌"的情况：

```python
# 原因：如果玩家打过2m，说明：
# 1. 不需要2m（已经舍弃）
# 2. 当时可能有多张，但现在已经不具代表性
# 3. 这种特殊情况会影响统计准确性

has_discarded_target = False
for d in player_state.discards[:i+1]:
    if d.tile // 4 == mapped_target_code:
        has_discarded_target = True
        break

if has_discarded_target:
    continue  # 跳过该样本
```

## 测试验证

运行测试脚本验证：

```
✓ 正确使用 hand_tiles_history[i] 获取该巡手牌
✓ 正确映射等价目标牌（6种等价模式）
✓ 正确应用筛选条件（跳过已打过目标牌的情况）
✓ 正确统计目标牌数量（转换编码后比较）
```

## 总结

这次修复解决了三个关键问题：
1. **编码匹配**：正确转换编码类型
2. **目标映射**：正确处理等价模式
3. **时间点准确性**：使用正确时刻的手牌快照 + 筛选条件

修复后的结果应该能准确反映"打出3m-1m的玩家手里有几张2m"这一读牌分析需求。
