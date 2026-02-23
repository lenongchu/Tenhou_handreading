# Tenhou6 格式迁移说明

## 当前状态（2026-02 已完成）

项目已全面采用 tenhou6 格式解析牌谱。

### 1. 解析流程

- **入口**：`parse_log_to_game_states()`（`src/live_analyzer.py`）
- **格式**：优先 tenhou6 JSON，若无则对 XML 调用 tenhou-paifu-to-json 转为 JSON 后解析
- **数据源**：`live_analyzer` 优先读取 `logs.log_json`（tenhou6 JSON），若为空则用 `logs.log`（XML）

### 2. 批量转换

使用 `convert_xml_to_tenhou6.py` 将 `logs` 表中的 XML 批量转为 tenhou6 JSON 并写入 `log_json`：

```bash
# 转换全部，8 进程并行（默认）
py convert_xml_to_tenhou6.py --skip-existing

# 指定并行进程数（建议 4-8，100 万牌谱约可提速 4-8 倍）
py convert_xml_to_tenhou6.py --skip-existing --workers 8

# 仅转换前 1000 局
py convert_xml_to_tenhou6.py --limit 1000 --workers 4

# 指定每批大小（默认 200）
py convert_xml_to_tenhou6.py --db data/tenhou.db --batch 500 --workers 8
```

### 3. 前置依赖

- **tenhou-paifu-to-json**（或 tenhou-paifu-to-json-main）需置于项目目录：

  ```bash
  git clone https://github.com/Riichi-Mahjong-Statistics-Seminar/tenhou-paifu-to-json tenhou-paifu-to-json-main
  ```

### 4. 数据库体积

- **原因**：`logs` 表同时存 `log`（gzip XML）和 `log_json`（tenhou6 JSON），每局两份数据，体积约翻倍；若 `log_json` 存未压缩 JSON，会比 gzip XML 更大。
- **现状**：新转换的 `log_json` 已改为 **gzip 压缩** 存储，体积约为未压缩的 1/3～1/5。读取时 `parse_log_to_game_states` 会自动解压（兼容旧未压缩数据）。
- **已存在的未压缩 log_json**：不会自动重压；若需缩小体积，可清空 `log_json` 后重新跑转换（会按 gzip 写入）。

### 5. 推荐流程

1. 下载牌谱后，运行 `convert_xml_to_tenhou6.py` 批量转换为 tenhou6 JSON
2. 分析时 `live_analyzer` 会自动优先使用 `log_json`，减少解析开销
