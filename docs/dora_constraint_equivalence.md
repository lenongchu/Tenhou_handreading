# 宝牌约束与等价变体映射说明

本文档说明分析开始时如何根据舍牌模式生成花色映射，以及该映射如何作用于舍牌模式、目标牌、可见约束、前段禁打与**宝牌约束**，以便日后查阅和核对。

---

## 一、映射的生成（全局、分析开始时）

映射关系在 `src/equivalent_variants.py` 的 `generate_equivalent_variants()` 中生成，依据是**舍牌模式中出现的花色数量**：

| 模式花色数 | 映射数量 | 说明 |
|-----------|----------|------|
| 0 种（纯字牌） | 1 | 恒等映射 |
| 1 种（如仅 s） | 3 | 该花色分别映到 m/p/s |
| 2 或 3 种 | 6 | m/p/s 的全排列 |

实现：`_get_suits_in_pattern(parsed_pattern)` → `_get_suit_mappings_for_variants(suits_in_pattern)` → 返回 `List[Dict[str, str]]`，每个 mapping 形如 `{"m":"p", "p":"s", "s":"m"}`。

**重要**：这组映射在分析开始时、按每个查询项生成一次，是**该模式的全局映射集**，后续所有相关数据都按同一映射关系进行等价变换。

---

## 二、映射作用于哪些内容

对每个 mapping，`generate_equivalent_variants` 会生成一个变体，并对以下内容应用同一 mapping：

| 内容 | 应用方式 | 示例 |
|------|----------|------|
| 舍牌模式 | `_apply_suit_mapping_to_string` | 1s-2s → 1m-2m |
| 目标牌 | `_transform_tile_with_mapping` | 4s → 4m |
| 可见枚数约束 | `_transform_visible_constraints_with_mapping` | {"4s":(1,1)} → {"4m":(1,1)} |
| 前段禁打 | `_apply_suit_mapping_to_string` | NOTs → NOTm |

每个变体是一个 `Dict`，包含 `discard`、`target`、`visible_constraints`、`prior_discard_exclusion` 等，它们都按同一 mapping 变换。

---

## 三、宝牌约束的等价处理

### 3.1 设计原则

宝牌约束应与舍牌模式的等价变体一致：当舍牌模式从 1s-2s 变为 1m-2m 时，对应的「宝牌为 4s」应视为「宝牌为 4m」。

因此，**宝牌约束应受到同一组映射的等价约束**：

- 用户输入「宝牌为 4s」
- 等价于接受：4s、4m、4p（同一数字、不同花色）

### 3.2 实现方式

宝牌约束**不**作为参数传入 `generate_equivalent_variants`，而是在 `src/live_analyzer.py` 中对每一局进行 round 级检查。

为实现与 mapping 相同的语义，使用 `_dora_matches_constraint(dora_str, dora_constraint)`：

- **数牌**：同数字不同花色视为等价（如 4s ≡ 4m ≡ 4p）
- **赤五**：0m / 0p / 0s 等价
- **字牌**：无花色等价，需完全匹配

这样，对「宝牌为 4s」而言，实际宝牌为 4s、4m、4p 的局都会被接受，与「舍牌 1s-2s 的三个等价变体 1s-2s / 1m-2m / 1p-2p」在逻辑上一致。

---

## 四、流程与对应关系

```
分析开始
    │
    ├─ 舍牌模式：1s-2s
    ├─ 目标牌：4s
    ├─ 宝牌约束：宝牌为 4s
    │
    ▼
generate_equivalent_variants()
    │
    ├─ suits_in_pattern = {s}  → 1 种花色
    ├─ mappings = [ s→s, s→m, s→p ]  （3 个 mapping）
    │
    ▼
对每个 mapping 生成一个变体：
    │
    ├─ mapping s→s: discard=1s-2s, target=4s, visible=..., prior=...
    ├─ mapping s→m: discard=1m-2m, target=4m, visible=..., prior=...
    └─ mapping s→p: discard=1p-2p, target=4p, visible=..., prior=...
    │
    ▼
遍历对局，对每一局：
    │
    ├─ Round 级检查：_dora_matches_constraint(dora_str, "4s")
    │       → 接受 dora 为 4s / 4m / 4p 的局
    │
    └─ 舍牌匹配：match_discard_to_variant()
            → 例如实际为 1m-2m 时匹配到变体 target=4m
```

---

## 五、要点总结

1. **映射来源**：由舍牌模式的花色集合决定，在 `generate_equivalent_variants` 中一次性生成，是该模式的全局映射集。
2. **映射范围**：舍牌模式、目标牌、可见枚数约束、前段禁打，均按同一 mapping 做等价变换。
3. **宝牌约束**：未传入 `generate_equivalent_variants`，但在 round 级通过 `_dora_matches_constraint` 实现与 mapping 等同的等价语义。
4. **等价关系**：数牌同数字不同花色等价；赤五 0m/0p/0s 等价；字牌无花色等价。

---

## 六、相关代码位置

| 功能 | 文件 | 函数/位置 |
|------|------|-----------|
| 映射生成 | equivalent_variants.py | `_get_suit_mappings_for_variants` |
| 变体生成 | equivalent_variants.py | `generate_equivalent_variants` |
| 目标牌变换 | equivalent_variants.py | `_transform_tile_with_mapping` |
| 可见约束变换 | equivalent_variants.py | `_transform_visible_constraints_with_mapping` |
| 宝牌等价检查 | live_analyzer.py | `_dora_matches_constraint` |
| 宝牌 round 过滤 | live_analyzer.py | 多处 `if dora_constraint != "dora_unrelated" and not _dora_matches_constraint(...)` |
| 宝牌无关（pattern_suit） | live_analyzer.py | 从 `matched_variant["discard"]` 提取花色后比较 |
