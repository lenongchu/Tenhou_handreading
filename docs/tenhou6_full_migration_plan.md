# Tenhou6 全面迁移计划

## 状态：已完成（2026-02）

## 已完成工作

1. **解析层**
   - 移除 mjlog_parser 的 XML 解析逻辑，仅保留 GameState、Discard 及牌符工具
   - `live_analyzer` 统一使用 tenhou6 格式解析（`tenhou6_adapter`）
   - `parse_log_to_game_states()` 支持 tenhou6 JSON，XML 通过 tenhou-paifu-to-json 转换

2. **存储层**
   - `logs` 表新增 `log_json` 列，存放 tenhou6 JSON
   - `convert_xml_to_tenhou6.py` 批量转换 XML → tenhou6 JSON
   - `live_analyzer` 优先读取 `log_json`，若无则使用 `log`（XML）

3. **脚本与测试**
   - 删除 `verify_mjlog_vs_tenhou6.py`、`verify_single_game_diff.py`
   - 更新 `process_logs_sample.py`、`verify_target_count1.py` 使用 tenhou6 解析

## 使用说明

### 批量转换（推荐）

首次或下载新牌谱后，运行批量转换：

```bash
py convert_xml_to_tenhou6.py --skip-existing
```

支持断点续传，已写入 `log_json` 的记录会被跳过。

### 分析流程

- `live_analyzer` 自动优先使用 `log_json`
- 未转换的对局仍可分析，会实时将 XML 转为 tenhou6 再解析
