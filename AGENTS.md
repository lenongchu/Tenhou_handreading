# 立直麻将读牌应用 - 项目手册 (AGENTS.md)

> **同步说明**：本文档与 `.cursor/rules/tenhou-handreading-project.mdc` 保持同步。修改任一处时，请同步更新另一文件。

本文档为项目核心手册，供 AI 与开发者有效维护。原 `docs/项目文件夹结构说明.md` 已并入，以本文件为唯一维护入口。

---

## 〇、云 VM / Jules 环境须知

**Jules 运行于云虚拟机 (cloud VM)**，与本地开发环境有重要差异：

| 差异点 | 说明 |
|--------|------|
| **无图形界面** | 无法启动 PyQt5 GUI（`run.py` / `gui_app`）。优先使用 CLI 脚本、批处理脚本，或 headless 模式。 |
| **路径风格** | 云 VM 通常为 Linux：使用 `/`，工作目录可能为 `/workspace` 或项目根。避免依赖 `C:` 等 Windows 路径。 |
| **数据与配置** | `data/tenhou.db`、`config.ini` 需确保存在且可写。数据可来自挂载或克隆，配置需从 `config.example.ini` 复制并填写。 |
| **外部工具** | `houou-logs` 需在 PATH 或项目内可执行；`tenhou-paifu-to-json` 需可用。云 VM 上需事先安装或拉取。 |
| **适合任务** | 批量处理（`process_all_logs*.py`）、转换（`convert_xml_to_tenhou6.py`）、数据库操作、分析逻辑开发、单元测试。 |
| **不适合任务** | 直接运行 `run.py` 启动 GUI、依赖本地显示/桌面的操作。 |

---

## 一、调用关系图

### 1.1 顶层入口与三大功能

```
                         run.py / gui_app（入口）
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          ▼                         ▼                         ▼
   ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
   │DataDownloader│          │ LiveAnalyzer │          │tile_illustration│
   │ 牌谱下载     │          │ 实时分析查询  │          │ 舍牌示意图    │
   └──────────────┘          └──────────────┘          └──────────────┘
```

### 1.2 牌谱下载与入库流程

```
   DataDownloader
         │ 调用 houou-logs（外部子进程）
         ▼
   ┌─────────────┐     fetch：获取 log ID     ┌─────────────┐
   │ houou-logs  │ ──────────────────────────►│  logs 表    │
   │ (外部工具)   │     download：下载 mjlog   │  (log GZIP)  │
   └─────────────┘ ◄─────────────────────────┘ └──────┬──────┘
         │ 调用 convert_xml_to_tenhou6                  │ 读 log 列
         ▼                                              ▼
   tenhou6_adapter.xml_to_tenhou6_json()      tenhou-paifu-to-json
          │ 写入 log_json 列                        (XML→JSON)
          ▼
   logs 表（含 log_json）  ◄── 牌谱 tenhou6 JSON 就绪
```

简记：houou-logs(fetch→download) → logs.log(GZIP) → convert_xml_to_tenhou6 → tenhou6_adapter.xml_to_tenhou6_json → logs.log_json

### 1.3 实时分析与查询流程（LiveAnalyzer）

```
   LiveAnalyzer.analyze(舍牌模式, 目标牌)
         │
         ├── 1. equivalent_variants.generate_equivalent_variants()  生成等价变体
         ├── 2. 从 database 读 logs 表 log_json
         ├── 3. tenhou6_adapter.parse_tenhou6_json()  → GameState 列表
         ├── 4. 逐巡：equivalent_variants.match_discard_to_variant() 匹配
         ├── 5. tenpai_utils.is_tenpai()  听牌判断
         ├── 6. instant_deal_in.RoundInstantDealInAnalyzer()（analysis_target="deal_in_instant" 时）
         └── 7. 写入 game_states / visible_tile_stats，返回概率
         │
         ▼
   database (tenhou.db): logs | game_states | visible_tile_stats
```

### 1.4 舍牌示意图（tile_illustration）

```
   tile_illustration.render_illustration_to_qimage()
         └── equivalent_variants.split_discard_pattern()  拆分模式
```

### 1.5 底层模块依赖

```
   mjlog_parser（GameState, Discard, CallInfo, TileUtils）
         ▲
         │ 被以下模块使用
   ┌─────┴─────┬──────────────┬──────────────┐
   │ tenhou6_   │ equivalent_  │ database     │
   │ adapter    │ variants     │              │
   └────────────┴──────────────┴──────────────┘
```

### 1.6 模块依赖表（import 方向）

| 被依赖模块 | 依赖方 |
|-----------|--------|
| `mjlog_parser` | database, tenhou6_adapter, pattern_matcher, live_analyzer, equivalent_variants |
| `equivalent_variants` | live_analyzer, tile_illustration |
| `tenpai_utils` | live_analyzer |
| `instant_deal_in` | live_analyzer（即时铳率/完整振听/理论 ron 点） |
| `tenhou6_adapter` | live_analyzer（解析 tenhou6 JSON → GameState） |
| `database` | live_analyzer, gui_app 间接 |
| `pattern_matcher` | 旧路径，live_analyzer 现用 equivalent_variants |
| `tile_illustration` | gui_app |
| `data_downloader` | gui_app |

---

## 二、专有名词（Terminology）

### 立直麻将 / 天凤

| 术语 | 英文 | 含义 |
|------|------|------|
| 立直 | Riichi | 宣告听牌，之后不能换牌 |
| 舍牌 | Discard | 玩家打出的牌 |
| 手切 | Tedashi | 从手牌打出（非摸切） |
| 摸切 | Tsumogiri | 摸牌后立即打出该牌 |
| 副露 | Call/Meld | 吃(chii)/碰(pon)/杠(kan) |
| 听牌 | Tenpai | 差一张即可和牌 |
| 向听 | Shanten | 距听牌差几手，0=听牌 |
| 宝牌 | Dora | 和牌时每张额外番数 |
| 赤五 | Red Five | 0m/0p/0s，替代 5m/5p/5s |
| 巡目 | Turn | 某玩家第几次出牌 |
| 本场 | Honba | 连庄次数 |
| 亲家/庄家 | Oya | 东家，player_id=0 时通常为 oya |
| 东1局/南2局 | Round | round_num: 0=东1, 4=南1 |
| 手顺 | Tejun | 对手打出牌的顺序，舍牌模式 |

### 牌符编码

| 概念 | 说明 |
|------|------|
| base | 0-36：1m=0..9m=8, 1p=9..9p=17, 1s=18..9s=26, 字牌 27-33, 赤五 34/35/36 |
| tile | 0-147：base*4（同种牌 4 张共用同一 base，区分实例用 tile%4） |
| 牌符字符串 | 1m-9m, 1p-9p, 1s-9s；0m/0p/0s=赤五；1z-7z 或 东/南/西/北/白/发/中 |

### 舍牌模式语法 (Discard Pattern Syntax)

| 类别 | 符号 | 含义 | 示例 |
|---|---|---|---|
| **基本牌型** | `1m`-`9m`, `1p`-`9p`, `1s`-`9s` | 数牌 | `1m`, `5p`, `9s` |
| | `1z`-`7z` 或 `东`...`中` | 字牌 | `1z`, `东`, `中` |
| | `0m`, `0p`, `0s` | 赤五 | `0m` (赤5m) |
| | `m`, `p`, `s` | 花色通配符 | `m` (任意万字) |
| **修饰符** | (无) | 默认手切 | `3m` |
| | `t` | 必须摸切 | `3mt` |
| | `f` | 手切或摸切皆可 | `3mf` |
| | `r` | 立直宣言牌（必为摸切） | `3mr` |
| **序列与逻辑** | `-` 或 `AND` | 顺序分隔 | `3s-1s`, `3sAND1s` |
| | `*` | 任意数量摸切 | `3s-*-1s` |
| | `$` | 任意一张手切 | `c0p6p-$` |
| | `OR` | 逻辑或 | `3mOR5m`, `[25]m` |
| | `NOT` | 逻辑非 | `NOTm`, `zNOT1z` |
| **占位符** | `z`/`zt` | 任意字牌/摸切字牌 | `z-zt` |
| | `zf`/`kf`/`yp` | 自风/客风/役牌（自风+场风+三元） | `zf`, `kf`, `yp` |
| | `z1`-`z3`/`kf1`-`kf3` | 互不相同字牌/客风 | `z1-z2-z3` |
| **副露** | `c<tiles>`/`p<tiles>` | 吃/碰 | `4mc3m5m`, `p1z1z` |
| | `pkfkf`/`pzfzf`/`pypyp` | 客风/自风/役牌碰 | `pkfkf`, `pzfzf`, `pypyp` |
| **拆搭** | `cd1`/`cd2` | 任意拆搭/拆搭花色≠下一张 | `cd1`, `cd2-3m` |
| | `cdm`/`cdp`/`cds` | 拆指定花色搭子 | `cdm` |

> `r` 与 `c/p` 不能同现（立直不可副露）；`*` 仅匹配连续摸切；`cd2` 自动检查下一张花色。

---

## 三、等价变换与全局映射

### 3.1 核心概念

**等价变换**：同一舍牌模式的多种花色表示等价（`1s-3s` ≡ `1m-3m` ≡ `1p-3p`），用于扩大统计样本。

**全局映射**：分析开始时按舍牌模式的花色集合一次性生成映射集，同时作用于舍牌模式、目标牌、可见约束、前段禁打、宝牌约束。

### 3.2 映射生成

| 模式花色数 | 映射数量 | 示例 |
|-----------|----------|------|
| 0 种（纯字牌） | 1 | 恒等映射 |
| 1 种（如仅 s） | 3 | s→s, s→m, s→p |
| 2 或 3 种 | 6 | m/p/s 全排列 |

实现：`_get_suits_in_pattern()` → `_get_suit_mappings_for_variants()` → `List[Dict[str, str]]`

### 3.3 映射作用范围

| 内容 | 变换函数 | 示例 |
|------|----------|------|
| 舍牌模式 | `_apply_suit_mapping_to_string` | 1s-2s → 1m-2m |
| 目标牌 | `_transform_tile_with_mapping` | 4s → 4m |
| 可见枚数约束 | `_transform_visible_constraints_with_mapping` | {"4s":(1,1)} → {"4m":(1,1)} |
| 副露区域约束 | `_apply_suit_mapping_to_string` | 4sc3s5s → 4mc3m5m |
| 前段禁打 | `_apply_suit_mapping_to_string` | NOTs → NOTm |

### 3.4 副露与字牌

- **含副露也应用全局映射**：`4mc3m5m` → `4pc3p5p`、`4sc3s5s` 等
- **字牌不参与映射**：1z-7z 无花色概念，不参与 m/p/s 映射；不影响变体数量

### 3.5 宝牌约束

不传入 `generate_equivalent_variants`，在 `live_analyzer` 用 `_dora_matches_constraint` 实现：数牌同数字等价；赤五 0m/0p/0s 等价；字牌需完全匹配。详见 `docs/dora_constraint_equivalence.md`。

---

## 四、核心模块

| 模块 | 职责 | 关键类/函数 |
|------|------|-------------|
| `mjlog_parser` | 牌谱数据结构、牌编码工具 | GameState, Discard, CallInfo, TileUtils |
| `tenhou6_adapter` | tenhou6 JSON → GameState | _parse_round_from_tenhou6 |
| `equivalent_variants` | 舍牌模式解析、等价变体、约束匹配 | generate_equivalent_variants, parse_target_tiles, match_discard_to_variant |
| `live_analyzer` | 实时分析：模式匹配 + 概率计算 | LiveAnalyzer.analyze, get_database_stats |
| `instant_deal_in` | 即时铳率引擎：事件重放、完整振听、理论点 | RoundInstantDealInAnalyzer, extract_tenhou6_rounds |
| `database` | SQLite：logs, game_states, visible_tile_stats | Database.create_tables, insert_game_state |
| `tenpai_utils` | 听牌判断 | is_tenpai (mahjong 库) |
| `gui_app` | PyQt5 桌面界面 | 入口 |
| `data_downloader` | 调用 houou-logs 下载牌谱 | DataDownloader |
| `tile_illustration` | 舍牌示意图渲染 | render_illustration_to_qimage |

---

## 五、文件夹结构

### 根目录

| 文件/文件夹 | 说明 |
|------------|------|
| `run.py` | 主入口，启动 GUI |
| `AGENTS.md` | 本手册副本（供 Jules 等云 VM 使用），与 `.cursor/rules/tenhou-handreading-project.mdc` 同步 |
| `setup.py`, `requirements.txt`, `config.example.ini` | 安装与配置 |
| `README.md`, `QUICKSTART.md`, `CHANGELOG.md`, `PROJECT_SUMMARY.md` | 文档 |
| `Riichi-rules-2016-EN.pdf` | EMA 立直规则参考 |

### 根目录脚本

| 文件 | 说明 |
|------|------|
| `download_historical_data.py` | 下载天凤凤凰桌历史牌谱 |
| `download_tiles.py` | 下载牌面素材（SVG/PNG） |
| `convert_xml_to_tenhou6.py` | XML → tenhou6 JSON |
| `process_all_logs.py` / `process_all_logs_optimized.py` | 批量处理牌谱 |
| `clean_new_logs.py` | 清理新导入低质量对局 |
| `clean_bye_logs.py` | 清理掉线未重连对局 |
| `compact_database.py`, `clear_game_states.py`, `rebuild_game_states.py` | 数据库操作 |
| `query_logs.py`, `decode_player_names.py`, `migrate_add_unique_constraint.py` | 查询与迁移 |
| `verify_single_vs_multi.py`, `cleanup_db_lock.py` | 校验与维护 |
| `启动应用.bat`, `启动应用_Python3.12.bat`, `后台下载数据.bat` | 批处理（本地 Windows 用） |
| `tenhou_handreading.log` | 应用日志 |

### `.cursor`

| 子目录/文件 | 说明 |
|------------|------|
| `rules/tenhou-handreading-project.mdc` | 本项目手册（.cursor 规则文件），与根目录 `AGENTS.md` 保持同步 |
| `plans/` | 开发计划 |
| `skills/riichi-mahjong-rules/` | 立直规则技能 |

### `assets`

| 路径 | 说明 |
|------|------|
| `assets/tile-assets/Regular/` | SVG 牌面素材 |
| `assets/tile-assets/Export/Regular/` | PNG 备选 |
| `assets/3d-tile/` | 3D 渲染用 PNG |

### `data`

| 文件 | 说明 |
|------|------|
| `tenhou.db` | 主数据库（SQLite） |
| `tenhou.db.stats_cache` | 统计缓存 |
| `query_archive.json` | 查询归档 |
| `archives/` | 归档目录 |

### `docs`

| 文件 | 说明 |
|------|------|
| `data_quality.md` | 数据质量管理 |
| `dora_constraint_equivalence.md` | 宝牌约束等价性 |
| `honor_tile_variants_design.md` | 字牌变体设计 |
| `tenhou6_migration.md`, `tenhou6_to_gamestate_example.md` | tenhou6 迁移 |
| `tile_glyph_assets.md` | 牌面符号说明 |
| `项目文件夹结构说明.md` | 已并入本手册，现为轻量索引 |

### `scripts`

| 文件 | 说明 |
|------|------|
| `debug_sample_match.py` | 样本匹配调试 |

### `src`

| 文件 | 说明 |
|------|------|
| `gui_app.py` | PyQt5 GUI 主程序 |
| `live_analyzer.py` | 实时舍牌分析 |
| `database.py` | 数据库操作 |
| `data_downloader.py` | 牌谱下载 |
| `mjlog_parser.py` | 牌谱解析（数据结构） |
| `tenhou6_adapter.py` | tenhou6 JSON 适配 |
| `equivalent_variants.py` | 等价变体、约束匹配 |
| `instant_deal_in.py` | 即时铳率（当巡可荣和/振听/理论点） |
| `pattern_matcher.py` | 旧模式匹配（现用 equivalent_variants） |
| `tenpai_utils.py` | 听牌判断 |
| `log_quality.py` | 牌谱质量检查 |
| `tile_illustration.py` | 舍牌示意图渲染 |

### `tests`

| 文件 | 说明 |
|------|------|
| `test_parser.py` | 牌谱解析测试 |

### 外部/子模块

| 目录 | 说明 |
|------|------|
| `houou-logs` | 天凤数据下载工具（可为空） |
| `tenhou-paifu-to-json-main` | tenhou-paifu-to-json 拷贝，XML→JSON 转换 |

---

## 六、数据流

1. **牌谱来源**：houou-logs 下载 XML → `data/` 或已转 tenhou6 JSON
2. **入库**：convert_xml_to_tenhou6 / process_all_logs → `logs` 表
3. **分析（常规）**：LiveAnalyzer 从 logs 读牌谱 → tenhou6_adapter 解析 → 逐巡生成 GameState → 匹配模式 → 写入 game_states
4. **分析（即时铳率）**：analysis_target=`deal_in_instant` 时，按 tenhou6 事件流重放 → instant_deal_in 判定当巡可荣和/振听/理论点
5. **查询**：用户输入舍牌模式 → equivalent_variants 解析 → live_analyzer 查库 → 返回概率分布或即时铳率指标

---

## 七、维护约定

- **修改牌编码逻辑**：改 `mjlog_parser.TileUtils` 与 `tenhou6_adapter.TENHOU6_TO_BASE`
- **修改舍牌模式语法**：改 `equivalent_variants`，与 `docs/dora_constraint_equivalence.md`、`docs/honor_tile_variants_design.md` 一致
- **修改等价/映射逻辑**：确保 `generate_equivalent_variants` 与 `_dora_matches_constraint` 语义一致
- **添加新约束**：在 equivalent_variants 中扩展占位符或 `match_discard_to_variant`
- **修改即时铳率/振听逻辑**：改 `instant_deal_in` 与 `live_analyzer` 的 `analysis_target="deal_in_instant"` 分支，保持“当巡时点”口径
- **修改铳率分析页（GUI）**：改 `gui_app` 中“铳率分析”分页；约束项需与主分析页保持同能力（宝牌/立直/副露/南三南四/副露区域/场上可见枚数）
- **规则参考**：`.cursor/skills/riichi-mahjong-rules/reference.md`、`Riichi-rules-2016-EN.pdf`
- **本手册维护**：新增重要目录/脚本/模块时，同步更新本文件；`docs/项目文件夹结构说明.md` 保持为轻量索引并指向本文件
- **双文件同步**：`AGENTS.md` 与 `.cursor/rules/tenhou-handreading-project.mdc` 内容一致，修改任一处需同步另一处
- **编码与乱码**：编辑含中文的源文件时，**禁止**经终端输出/管道写回；务必使用编辑器级写入并保证 UTF-8。详见 `docs/mojibake_root_cause.md`
- **乱码根因（已确认）**：Windows 下经 PowerShell 终端链路（管道/重定向/不安全替换）写回 UTF-8 文件，会触发双重编码并可能破坏引号、注入私有区字符；禁止使用该链路编辑源码
