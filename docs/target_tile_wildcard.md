# 目标牌通配：五与赤五（`.`）

## 文档位置

| 说明类型 | 位置 |
|----------|------|
| **项目手册（权威）** | 根目录 `AGENTS.md` → 章节「目标牌输入（Target tile，统计用）」；与 `.cursor/rules/tenhou-handreading-project.mdc` 同步 |
| **GUI 帮助（用户）** | 主界面「目标牌」旁 **?** 按钮 → `src/ui/styles.py` 内 `TARGET_HELP_HTML` |
| **实现** | `equivalent_variants.parse_target_tiles`、`_transform_tile_with_mapping`；`mjlog_parser.TileUtils.get_bases_for_target_tile_str`；`live_analyzer` 中目标统计与即时铳率对 `.` 的拒绝 |

## 摘要

- **`5.p` / `5.m` / `5.s`**：该目标位同时接受普通五与赤五（`5x` 与 `0x`）。
- **简写 `45.p`**：等价 `4p` + `5.p`。
- **不写 `.` 时**：`5p` 与 `0p` 在统计上仍**不合并**（与默认目标牌语义一致）。
- **即时铳率**：目标串中**不允许** `.`；须写 `5p` 或 `0p`。
