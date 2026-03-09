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
| **数据与配置** | 数据库路径由 `config.ini` 的 `[Database] path` 指定（默认可为项目外，如 `E:\Cursor\Tenhou data\data\tenhou.db`）。配置需从 `config.example.ini` 复制并填写。 |
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
 ▼ ▼ ▼
 ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
 │DataDownloader│ │ LiveAnalyzer │ │tile_illustration│
 │ 牌谱下载 │ │ 实时分析查询 │ │ 舍牌示意图 │
 └──────────────┘ └──────────────┘ └──────────────┘
```

### 1.2 牌谱下载与入库流程

```
 DataDownloader
 │ 调用 houou-logs（外部子进程）
 ▼
 ┌─────────────┐ fetch：获取 log ID ┌─────────────┐
 │ houou-logs │ ──────────────────────────►│ logs 表 │
 │ (外部工具) │ download：下载 mjlog │ (log GZIP) │
 └─────────────┘ ◄─────────────────────────┘ └──────┬──────┘
 │ 调用 convert_xml_to_tenhou6 │ 读 log 列
 ▼ ▼
 tenhou6_adapter.xml_to_tenhou6_json() tenhou-paifu-to-json
 │ 写入 log_json 列 (XML→JSON)
 ▼
 logs 表（含 log_json） ◄── 牌谱 tenhou6 JSON 就绪
```

简记：houou-logs(fetch→download) → logs.log(GZIP) → convert_xml_to_tenhou6 → tenhou6_adapter.xml_to_tenhou6_json → logs.log_json

### 1.3 实时分析与查询流程（LiveAnalyzer）

```
 LiveAnalyzer.analyze(舍牌模式, 目标牌)
 │
 ├── 1. equivalent_variants.generate_equivalent_variants() 生成等价变体
 ├── 2. 从 database 读 logs 表 log_json
 ├── 3. tenhou6_adapter.parse_tenhou6_json() → GameState 列表
 ├── 4. 逐巡：equivalent_variants.match_discard_to_variant() 匹配
 ├── 5. tenpai_utils.is_tenpai() 听牌判断
 ├── 6. instant_deal_in.RoundInstantDealInAnalyzer()（analysis_target="deal_in_instant" 时）
 └── 7. 写入 game_states / visible_tile_stats，返回概率
 │
 ▼
 database (tenhou.db): logs | game_states | visible_tile_stats
```

### 1.4 舍牌示意图（tile_illustration）

```
 tile_illustration.render_illustration_to_qimage()
 └── equivalent_variants.split_discard_pattern() 拆分模式
```

### 1.5 底层模块依赖

```
 mjlog_parser（GameState, Discard, CallInfo, TileUtils）
 ▲
 │ 被以下模块使用
 ┌─────┴─────┬──────────────┬──────────────┐
 │ tenhou6_ │ equivalent_ │ database │
 │ adapter │ variants │ │
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
| | `r` | 立直宣言牌 | `3mr` (手切), `3mtr` (摸切) |
| **立直细分** | `r` | **手切**立直宣言 (Tedashi Riichi) | `3mr` |
| | `tr` 或 `rt` | **摸切**立直宣言 (Tsumogiri Riichi) | `3mtr` |
| | `fr` | **手/摸皆可**立直宣言 | `3mfr` |
| **序列与逻辑** | `-` 或 `AND` | 顺序分隔 | `3s-1s`, `3sAND1s` |
| | `*` | 任意数量摸切 | `3s-*-1s` |
| | `$` | 任意一张手切 | `c0p6p-$` |
| | `[xy]suit` | **数字范围**：x 到 y 连续（含两端），共 (y−x+1) 张；与单张相同，**无后缀默认手切**，可加 `t`（摸切）或 `f`（手摸切皆可），如 `[29]mf` | `[39]p`=3p~9p 共 7 张（仅手切）；`[17]z`=1z~7z；`[29]mf`=2m~9m 手摸切皆可 |
| | `OR` | 逻辑或 | `3mOR5m`, `[25]m`（[25]m 即 2m~5m 其一） |
| | `NOT` | 逻辑非 | `NOTm`, `zNOT1z`, `NOT[45]m`（排除 4m,5m） |
| **占位符** | `z`/`zt` | 任意字牌/摸切字牌 | `z-zt` |
| | `zf`/`kf`/`yp` | 自风/客风/役牌（自风+场风+三元） | `zf`, `kf`, `yp` |
| | `ap` | 安牌（满足其一即可，不含本张舍牌：场上可见1-3枚该字牌；或非场风、非三元字牌可见0张） | `ap` |
| | `apr` | 立直宣言的安牌（该舍牌为立直宣言且满足安牌条件） | `apr` |
| | `z1`-`z3`/`kf1`-`kf3` | 互不相同字牌/客风 | `z1-z2-z3` |
| **副露** | `c<tiles>`/`p<tiles>` | 吃/碰 | `4mc3m5m`, `p1z1z` |
| | `pkfkf`/`pzfzf`/`pypyp` | 客风/自风/役牌碰 | `pkfkf`, `pzfzf`, `pypyp` |
| **拆搭** | `cd1`/`cd2` | 任意拆搭/拆搭花色≠下一张 | `cd1`, `cd2-3m` |
| | `cdm`/`cdp`/`cds` | 拆指定花色搭子 | `cdm` |

> `r` 与 `c/p` 不能同现（立直不可副露）；`*` 仅匹配连续摸切；`cd2` 自动检查下一张花色。数字范围 `[xy]suit` 无后缀时仅匹配手切，需手摸切皆可请写 `[xy]suitf`（如 `[29]mf`）。

---

## 三、等价变换与全局映射

### 3.1 核心概念

**等价变换**：同一舍牌模式的多种花色表示等价（`1s-3s` ≡ `1m-3m` ≡ `1p-3p`），用于扩大统计样本。

**全局映射**：分析开始时按舍牌模式的花色集合一次性生成映射集，同时作用于舍牌模式、目标牌、可见约束、前段禁打、前段有打、宝牌约束。

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
| 前段禁打 | `_apply_suit_mapping_to_string` | NOTs → NOTm；`[39]p` → `[39]s` 等，与主模式同套映射 |
| 前段有打 | `_apply_suit_mapping_to_string` | `[37]mOR[37]s` → `[37]pOR[37]m` 等，与主模式同套映射 |

**前段约束**：前段禁打、前段有打均参与等价映射，与主舍牌模式使用同一套花色映射；判定时用**当前命中变体的映射后串**对「主模式匹配点之前的实际舍牌」做检查。语法与舍牌模式一致，含数字范围 `[xy]`（如 `[39]p` = 3p～9p 共 7 张）。

### 3.4 占位符 ap/yp 与等价映射

- **ap / apr / apf / apt**（安牌、立直宣言安牌等）与 **yp / ypr / ypf / ypt**（役牌等）中的字母 **p** 是占位符名的一部分，**不是**花色「饼子」。等价映射时这些整词**不参与**花色替换（否则会误写成 am/ym 等）。
- 带后缀形式（如 `apf` 手摸切皆可、`apt` 摸切）同样按整词保留。实现见 `equivalent_variants._is_ap_or_yp_token` 与 `_apply_suit_mapping_to_string`。

### 3.5 纯字牌模式 + 数牌目标

- 舍牌模式**仅含字牌占位符**（如 `apr`、`z-ap`）而**目标牌为数牌或赤五**（如 `3p`）时，仍按目标牌花色生成 **3 个等价变体**（3m / 3p / 3s），以扩大样本。实现见 `generate_equivalent_variants` 中「纯字牌模式」分支对 `target_suits` 的处理。

### 3.6 关联牌判断与等价变换

- **使用等价变换**：关联牌分析（analysis_target=`related_tile`）与目标牌存量、听牌等模式相同，舍牌模式参与等价变换。如 `1p-2p` 生成 3 个变体（1m-2m、1p-2p、1s-2s），扩大样本池。
- **目标牌占位**：关联牌无需目标牌，传入 `5z` 占位；`generate_equivalent_variants` 仅对舍牌模式做花色映射，变体 `target` 为 `5z` 不变。
- **判定逻辑**：匹配时取**实际舍牌**的最后一张（`discard.tile`），按该牌的花色与点数从 `hand_at_turn` 提取该花色 1-9 枚数，调用 `related_tile_utils.is_related_discard(num, counts)` 判断是否关联。
- **字牌不参与**：舍牌为字牌时视为非关联（target_count=0）。
- **实现**：`related_tile_utils` 模块；`live_analyzer` 主分析、并行 worker、网格分析均支持。

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
| `related_tile_utils` | 关联牌判断 | is_related_discard, hand_to_suit_counts |
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

---

## 六、数据流

1. **牌谱来源**：houou-logs 下载 XML → `data/` 或已转 tenhou6 JSON
2. **入库**：convert_xml_to_tenhou6 / process_all_logs → `logs` 表
3. **分析（常规）**：LiveAnalyzer 从 logs 读牌谱 → tenhou6_adapter 解析 → 逐巡生成 GameState → 匹配模式 → 写入 game_states
4. **分析（即时铳率）**：analysis_target=`deal_in_instant` 时，按 tenhou6 事件流重放 → instant_deal_in 判定当巡可荣和/振听/理论点
5. **查询**：用户输入舍牌模式 → equivalent_variants 解析 → live_analyzer 查库 → 返回概率分布或即时铳率指标

---

## 六.1 即时铳率分析 (Instant Deal-in Rate)

### 概念与口径

**即时铳率**：在“命中样本所在的当巡时点”，假想目标牌（如 3p）若被对手打出，我方是否可荣和、是否振听、理论 ron 点多少。与“结局放铳率”（对局结束后是否放铳）不同，口径为**当巡时点**。

### 核心模块与流程

| 组件 | 职责 |
|------|------|
| `instant_deal_in.RoundInstantDealInAnalyzer` | 按 tenhou6 事件流重放，判定当巡可荣和/振听/理论点 |
| `instant_deal_in.extract_tenhou6_rounds` | 从 tenhou6 JSON 提取小局 (game_data, game_events) |
| `live_analyzer` | analysis_target=`deal_in_instant` 时调用引擎，汇总铳率/铳点/铳度 |

流程：舍牌模式匹配 → 取 matched_variant["target"]（等价映射后目标牌）→ `round_instant_analyzer.evaluate(player_id, turn, mapped_target)` → 返回 `deal_in_hit`、`deal_in_point`、`furiten_state` 等。

### 假想目标牌与等价变体

- **假想目标牌参与等价变体**：目标牌与舍牌模式一起做花色映射（`_transform_tile_with_mapping`），如 2s → 2m/2p。
- **样本生成**：匹配任一等价变体即生成样本，`actual_pattern` 为实际舍牌，`mapped_target` 为映射后目标牌；即时铳率评估使用 `mapped_target`。

### 多目标即时铳率

- **目标格式**：逗号分隔，如 `3p,4p`，表示同时分析 3p、4p 两张假想牌。
- **变体 target**：多目标时 `variant_target="3p,4p"` 传入 `generate_equivalent_variants`，变体 `target` 为列表 `["3m","4m"]` 等；单目标时等价变体里存为**字符串**（如 `"3m"`）。
- **变体 target 与 mt_item 统一逻辑**：一次分析可配置多条（舍牌模式, 目标牌），「是否多目标」按第一条判定（`multi_target = len(item_multi_targets[0]) > 1`），实际匹配可能命中其他条（单目标）。`live_analyzer` 取到 `matched_variant["target"]` 后**统一规范为列表**：若为字符串则 `m_targets = [m_targets]`，再以 `len(m_targets) == len(mt_item)` 与当前条目标列表 `mt_item = item_multi_targets[matched_idx]` 对齐，逐目标 evaluate 与统计。**长度不一致视为逻辑错误**，直接抛出 `ValueError`，不静默回退。
- **逐目标统计**：`instant_deal_in_dist[tk]` 按目标牌分别累计 hits/points；`multi_instant_stats` 输出各目标铳率、平均铳点、铳度。
- **并行 worker**：worker 需实现多目标逻辑并返回 `instant_deal_in_dist`，主进程合并。

### 样本池与可铳样本

- **可铳优先**：池满时，若新样本为可铳（`deal_in_hit` 或 `instant_eval_multi[tk]["deal_in_hit"]`），替换池中首个非可铳样本。
- **多目标筛选**：生成样本时 `target_tile_filter`（如 "3p"）按 `instant_eval_multi[target_tile_filter]["deal_in_hit"]` 过滤。
- **每局样本上限**：`PARALLEL_MAX_SAMPLE_POOL_PER_LOG` 控制单局最多保留样本数，避免单局多匹配时截断过多可铳样本。

### 假想振听牌 (Hypothetical Furiten Tiles)

- **参数**：`hypothetical_furiten_tiles`，如 `"6p"` 或 `"6p,7p"`，在铳率分析页输入框「假想振听牌」。
- **逻辑**：若目标牌可铳且假想振听牌（**且非目标牌本身**）在该时点也会放铳，则该匹配**不计入主铳率**，而是计入 `excluded_due_to_hypothetical_furiten`。
- **目标牌排除**：假想振听牌若与目标牌相同（映射后），不参与排除判定，避免误排除全部可铳样本。
- **等价变换**：假想振听牌与目标牌使用同一套等价：数牌/赤五按**当前变体的 pattern suit**（从 `matched_variant["discard"]` 取首个数牌花色）变换，即在该变体下检查「同数字、同变体花色」的牌是否也会放铳；字牌仍用 `mapping`。实现见 `_get_variant_pattern_suit` 与 `_get_hypothetical_furiten_deal_in_tiles`。
- **结果展示**：当 `excluded_due_to_hypothetical_furiten > 0` 时，显示「因假想振听牌被排除的案列数」；若输入了多张假想振听牌（如 1p,2p），则同时按牌展示每张导致的排除数 `excluded_due_to_hypothetical_furiten_by_tile`（如「1p: 20 例、2p: 29 例」）。

### 平均铳点口径选项

- **不考虑里宝（平均铳点仅按理论点）**  
  - 铳率分析页复选框，默认勾选。  
  - 勾选时：平均铳点 = 可铳样本的**理论点**（仅表宝牌，不含里宝）求平均。  
  - 不勾选时：预留「考虑里宝」逻辑（若已实现里宝随机模拟则用其求平均）；当前实现仍按理论点。  
  - 参数：`instant_use_theory_point_only`；结果中为 True 时界面显示「平均铳点（仅理论点）」。

- **亲家和牌以自家计算**  
  - 铳率分析页复选框，默认不勾选。  
  - 勾选时：亲家荣和时的铳点按**子家点**换算（库返回的放铳者支付额折半），统一统计口径，减少亲家样本带来的点数偏高偏差。  
  - 实现：`RoundInstantDealInAnalyzer(normalize_oya_ron_to_ko=True)`；`_calculate_ron_point` 中若和牌者为亲家（`snapshot.player_id == self.oya`）则 `point = (point + 1) // 2`。  
  - 参数：`instant_normalize_oya_ron_to_ko`；结果中为 True 时界面在平均铳点后追加「（亲家已按子家换算）」提示。

### 约束与限制

- 支持主分析页全部约束：宝牌、立直、副露、南三南四、副露区域、场上可见枚数。
- 不支持 combo 目标（如 4s-5s 搭子），请用逗号分隔多目标（如 4s,5s）。
- 振听判定：含同巡振听、立直振听、舍张振听，由 `RoundInstantDealInAnalyzer` 实现。

---

## 七、维护约定

- **修改牌编码逻辑**：改 `mjlog_parser.TileUtils` 与 `tenhou6_adapter.TENHOU6_TO_BASE`
- **修改舍牌模式语法**：改 `equivalent_variants`，与 `docs/dora_constraint_equivalence.md`、`docs/honor_tile_variants_design.md` 一致
- **修改等价/映射逻辑**：确保 `generate_equivalent_variants` 与 `_dora_matches_constraint` 语义一致
- **添加新约束**：在 equivalent_variants 中扩展占位符或 `match_discard_to_variant`
- **修改即时铳率/振听逻辑**：改 `instant_deal_in` 与 `live_analyzer` 的 `analysis_target="deal_in_instant"` 分支，保持“当巡时点”口径
- **修改铳率分析页（GUI）**：改 `gui_app` 中“铳率分析”分页；约束项需与主分析页保持同能力（宝牌/立直/副露/南三南四/副露区域/场上可见枚数）。铳率分析页支持**多条舍牌模式**（可添加多行“模式 + 目标牌”），满足任一即计入，与主分析页一致。
- **规则参考**：`.cursor/skills/riichi-mahjong-rules/reference.md`、`Riichi-rules-2016-EN.pdf`
- **本手册维护**：新增重要目录/脚本/模块时，同步更新本文件；`docs/项目文件夹结构说明.md` 保持为轻量索引并指向本文件
- **双文件同步**：`AGENTS.md` 与 `.cursor/rules/tenhou-handreading-project.mdc` 内容一致，修改任一处需同步另一处
- **编码与乱码**：编辑含中文的源文件时，**禁止**经终端输出/管道写回；务必使用编辑器级写入并保证 UTF-8。详见 `docs/mojibake_root_cause.md`
- **乱码根因（已确认）**：Windows 下经 PowerShell 终端链路（管道/重定向/不安全替换）写回 UTF-8 文件，会触发双重编码并可能破坏引号、注入 private use area 字符；禁止使用该链路编辑源码。

---

## 九、大文件编辑保护协议 (Large File Protection)

由于 `live_analyzer.py` 与 `gui_app.py` 均超过 2000-3000 行，编辑器/AI 级写入极易产生截断、乱码或语法破坏。所有 Agent 必须遵守：

1.  **修改后必校验**：每次 substantive edit（实质性修改）后，必须立即运行语法检查：
    `python -m py_compile src/live_analyzer.py`
2.  **分阶段提交 (Multi-stage Commits)**：避免一次性进行超大规模（100+行）的重构。采取“小步快跑”策略，确保每次提交都是语法正确且可运行的。
3.  **禁止混合缩进**：由于历史原因，本项目严格使用 **4个空格** 缩进。Agent 在写入时必须确保不引入 Tab 字符。
4.  **编码锁定**：Agent 在执行 `Read` 后发现非 UTF-8 字符（如 `瀹炴椂` 等乱码）时，必须停止修改并先修复编码。

---

## 八、样本生成与验证逻辑

### 8.1 样本收集流 (Sample Collection)

1.  **主分析打标 (Pre-tagging)**：在 `LiveAnalyzer.analyze_discard_pattern` 扫描期间，实时匹配成功的样本会附带业务标记：
    *   `outcome_won` / `outcome_deal_in`：和牌/放铳标记。
    *   `deal_in_hit` / `deal_in_point`：即时铳率判定及其理论点。
    *   `target_count` / `target_counts`：目标牌在手牌中的实际枚数。
2.  **样本池缓存 (sample_pool)**：前 N 条（由 `sample_pool_cap` 控制）符合条件的完整数据会被存入 `sample_pool` 字典列表。

### 8.2 生成验证样本 (Sample Extraction)

*   **极速提取**：当用户点击「生成样本」且存在 `sample_pool` 时，直接按**标记位**（如 `deal_in_hit == True`）进行过滤。
*   **零冗余校验**：从 `sample_pool` 提取样本时**不再调用** `verify_sample_consistency`。因为该样本在存入时已通过主分析器的严耕校验，避免因字典上下文丢失导致的二次判错。
*   **离线扫描**：若无 `sample_pool`（如二次打开历史存档或内存不足），则启动 `collect_verification_samples` 遍历数据库。此时会调用 `verify_sample_consistency` 确保离线还原的逻辑一致性。

### 8.3 格式化规范 (Display Format)

*   **手切立直**：在 `actual_pattern` 中显示为 `r` 后缀（如 `3pr`）。
*   **摸切立直**：在 `actual_pattern` 中显示为 `tr` 后缀（如 `3ptr`）。
*   **摸切**：普通摸切显示为 `t`（如 `3pt`）。
*   **手切**：普通手切无后缀。
