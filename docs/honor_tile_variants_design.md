# 字牌等价变体设计文档

## 1. 字牌编码约定

| 符号 | 牌面 | base_tile | 说明 |
|------|------|-----------|------|
| 1z | 东 | 27 | 风牌 |
| 2z | 南 | 28 | 风牌 |
| 3z | 西 | 29 | 风牌 |
| 4z | 北 | 30 | 风牌 |
| 5z | 白 | 31 | 三元牌 |
| 6z | 发 | 32 | 三元牌 |
| 7z | 中 | 33 | 三元牌 |

自风/客风（与 `MjlogParser.get_player_wind` / `get_jikaze` 一致）：仅由 `player_id` 与亲 `oya` 决定座位序 East→…，**勿把 `round_num` 或场风进度叠加到座风**。
场风（round wind）以谱面 `GameState.bakaze` + `resolve_bakaze` 为准，勿用 `round_num // 4` 单轨代替。
客风 = 四风之中除自风外的 3 个风位。

---

## 2. 新符号与等价变体数量

| 符号 | 含义 | 变体数（与数牌 6 个搭配时） | 匹配时 |
|------|------|---------------------------|--------|
| z | 任意字牌 | 6×1=6 | 接受任意 1z-7z |
| zt | 任意字牌摸切 | 6×1=6 | 接受任意 1z-7z 且 is_tsumogiri |
| zf | 自风 | 6×1=6 | 需 player 上下文，匹配 自风 |
| kf | 客风 | 6×3=18 | 需 player 上下文，匹配 3 个客风之一 |
| kfx | 客风不含场风 | 6×2=12（非连风为 2 门；连风时同 kf） | 需 `kyokuze_list` + `bakaze`（`resolve_bakaze`）；`pkfxkfx` 对称 `pkfkf` |
| z1 | 任意字牌（第 1 个） | - | 与 z2/z3 组合时约束“不同” |
| z2 | 与 z1 不同的任意字牌 | - | 匹配时检查 ≠ z1 |
| z3 | 与 z1、z2 不同的任意字牌 | - | 匹配时检查 ≠ z1,z2 |
| zf1,zf2,zf3 | 自风中的第 1/2/3 张 | - | 自风仅 1 种，zf2/zf3 需多局或扩展语义 |

**纯字牌组合示例：**
- "zt-zt"：连续两张摸切字牌 → 7×7=49 种
- "z1-z2"：两张不同的字牌 → 7×6=42 种
- "z1-z2-z3"：三张不同字牌 → 7×6×5=210 种

---

## 3. 实现要点

### 3.1 解析扩展（parse_discard_element）
- 识别 z, zt, zf, kf, kfx, z1, z2, z3, zf1, zf2, zf3；`pkfxkfx` → `@p:kfx`
- 1z-7z 与 东南西北白发中 的互转

### 3.2 等价变体生成（generate_equivalent_variants）
- 数牌部分：沿用现有 6 变体（3 花色 × 2 镜像）
- 字牌占位符：
  - z/zt/zf/kf/kfx：不展开，保留占位符
  - zt-zt：展开为 49 个 (h1,h2) 对
  - z1-z2：展开为 42 个 (h1,h2) 对，h1≠h2
  - z1-z2-z3：展开为 210 个三元组

### 3.3 匹配逻辑（_match_pattern_at_end）
- 新增参数：`context: Optional[Dict] = None`，含 player_id, oya, round_num
- 对 z：接受任意字牌
- 对 zf/kf/kfx：用 context 算出 自风/客风/客风去场风，再检查
- 对 z1,z2,z3：匹配时维护已匹配字牌集合，保证“不同”

### 3.4 调用链修改
- `match_discard_to_variant` 增加 `context` 参数
- `live_analyzer` 在匹配时传入 `player_id, oya, round_num`

### 3.5 副露碰 `p<tiles>`（数牌对碰，与字牌表正交）
- **语法**：`p` + 两枚**相同**牌，如 `p1z1z`、`p1m1m`、`p9m9m`、`p0m0m`（赤对碰）；解析为内部 `@p:…`，与谱面 `pon` 的 `consumed` 一致。
- **等价**：含数牌的碰参与全局 `m/p/s` 映射（`p1m1m` → `p1p1p` / `p1s1s`）；`_apply_suit_mapping_to_string` 将行首 **`p` 视为碰前缀**而非花色「饼」，避免与 `yp`/`ap` 占位符中的字母 `p` 混淆（后者整词不参与花色置换）。
- **`OR` 限制**：`p1m1mORp9m9m` 等副露组合 **不支持**；请用主界面**多行舍牌模式**逐行 OR。

---

## 4. 待确认问题

1. **zf2, zf3**：自风每局只有一种，zf2/zf3 的语义是否指“同一自风的第二/三张”，或仅在 三元牌 等场景使用？
2. **string_to_tile**：是否在 mjlog_parser 中增加 "1z"-"7z" 的解析？
3. **get_acceptable_last_tiles**：对 z/zf/kf，末尾牌应为“任意字牌”，需调整快速排除逻辑。
