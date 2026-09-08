# 立直麻将读牌应用

## 项目简介

这是一个基于天凤凤凰桌牌谱数据的立直麻将统计工具。通过分析历史对局数据，以真实的数据作为基础，提升麻将理解。
作者天凤八段(儲），现淡出麻将

## 主要功能

- **数据采集**：自动下载天凤凤凰桌（2020年至今）的历史牌谱数据
- **数据质量控制**：自动过滤低质量对局（掉线未重连），确保分析准确性
- **舍牌分析**：识别手切/摸切，追踪玩家的舍牌序列
- **模式匹配**：支持通配符查询（如 `7s-*-5s`），灵活匹配舍牌模式
- **场上可见枚数**：支持指定牌在场上可见枚数范围；另支持宝牌约束、立直约束，精确模拟实战环境
- **概率计算**：统计目标牌在手牌中的概率分布（0/1/2/3张）
- **图形界面**：提供直观的桌面GUI，方便查询和分析
- **麻将示意图**：根据舍牌/副露符号生成示意图图片（如 `4mc3m5m`），牌面素材来自 `assets/tile-assets`

## 技术栈

- **编程语言**：Python 3.10+
- **数据下载**：[houou-logs](https://github.com/Apricot-S/houou-logs)
- **数据存储**：SQLite
- **数据处理**：NumPy, Pandas
- **GUI框架**：PyQt5
- **牌谱解析**：tenhou6 格式（[tenhou-paifu-to-json](https://github.com/Riichi-Mahjong-Statistics-Seminar/tenhou-paifu-to-json)）

## 安装指南

### 1. 环境要求

- Python 3.10 或更高版本
- pip 包管理器

### 2. 克隆项目

```bash
git clone <repository-url>
cd Tenhou_handreading
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 初始化数据

首次运行时，需要下载历史牌谱数据（这可能需要较长时间）：

```bash
python -m src.gui_app
```

在GUI中点击"下载历史数据"按钮。

**重要**：下载完成后，建议依次执行：

1. 清理低质量对局：`py clean_new_logs.py`
2. 批量转换为 tenhou6 格式（可选，可加速分析）：`py convert_xml_to_tenhou6.py --skip-existing`

详见 [数据质量管理文档](docs/data_quality.md)、[tenhou6 迁移说明](docs/tenhou6_migration.md)。

### 5. 麻将示意图（可选）

使用「麻将示意图」功能前，需将牌面素材放入 `assets/tile-assets/Regular/`（SVG 格式）。项目已预置来自 `E:\Cursor\SVGtoasset\assets` 的正放牌素材。

**立体效果**：安装 `cairosvg` 可高质量渲染 Inkscape SVG；未安装时使用 Qt 渲染。

## 使用说明

### 启动应用

```bash
python -m src.gui_app
```

### 查询示例

**场景**：对手第2-10巡内先手切7s，后手切9s，宝牌为6s，场上8s可见2-3枚，想知道他有多大概率还有6s？

**输入**：
- 舍牌模式：`7s-9s`
- 目标牌：`6s`
- 巡目范围：2 - 10
- 宝牌约束：宝牌为 `6s`
- 场上可见枚数：`8s` 可见 `2-3` 枚

**输出**（示例）：
```
目标牌: 6s
有0张的概率: 40.5%
有1张的概率: 45.2%
有2张的概率: 12.8%
有3张的概率: 1.5%
样本数量: 8,432 局
```

### 查询语法

#### 舍牌模式
- `3s-1s`：第1巡手切3s，第2巡手切1s
- `3s-*-1s`：手切3s后任意次摸切，再手切1s
- `3s-*2-1s`：手切3s，恰好2次摸切，再手切1s（可选功能）

#### 目标牌（分析目标）
- 单张：`6s`、`2m`、字牌等；统计该牌在手牌中 0/1/2/3 张的概率
- **五与赤五通配**：`5.p` / `5.m` / `5.s` 表示该位同时接受普通五与赤五（`5x` 与 `0x`）；简写 `45.p` 等价 `4p`+`5.p`（详见 GUI「目标牌」旁 **?** 按钮说明或 `AGENTS.md`「目标牌输入」）
- 不写 `.` 时，`5p` 与 `0p`（赤五）在统计上分别计数；即时铳率模式不支持 `.` 通配，须写 `5p` 或 `0p`

#### 宝牌约束
- **宝牌无关**：仅匹配宝牌指示物与舍牌序列花色不同的对局
- **宝牌为X**：匹配宝牌为指定值的对局（如 `6s`）

#### 场上可见枚数
- 指定某张牌在场上可见枚数范围（其他3家舍牌河 + 副露 + 宝牌指示物）
- 支持多个约束同时生效

## 项目结构

```
tenhou_handreading/
├── README.md                    # 项目说明文档
├── requirements.txt             # Python依赖列表
├── setup.py                     # 安装配置
├── src/                         # 源代码目录
│   ├── __init__.py
│   ├── data_downloader.py       # 数据下载模块
│   ├── mjlog_parser.py          # 牌谱解析模块
│   ├── tile_normalizer.py       # 牌型标准化模块
│   ├── pattern_matcher.py       # 模式匹配引擎
│   ├── probability_calculator.py # 概率计算引擎
│   ├── database.py              # 数据库操作
│   └── gui_app.py               # GUI主程序
├── tests/                       # 测试代码
│   ├── test_parser.py
│   ├── test_normalizer.py
│   └── test_matcher.py
└── data/                        # 数据存储目录
    └── (数据库文件)
```

## 核心算法

### 1. 牌型标准化

为了提高查询效率和匹配准确性，系统会将舍牌序列标准化：

- **数字等价性**：`7s-9s` ≡ `1m-3m`（相对位置关系相同）
- **花色等价性**：当宝牌无关时，不同花色的相同模式被视为等价
- **宝牌一致性**：宝牌约束和舍牌序列同步标准化

### 2. 模式匹配

支持灵活的通配符查询，允许：
- 精确匹配连续手切
- 通配符匹配任意摸切
- 巡目范围限制
- 可见枚数约束
- 宝牌约束

### 3. 概率计算

在所有匹配的历史对局中：
1. 定位到舍牌序列完成的时刻
2. 统计该时刻手牌中目标牌的数量
3. 计算0/1/2/3张的概率分布

## 数据来源

本项目使用 [houou-logs](https://github.com/Apricot-S/houou-logs) 工具下载天凤凤凰桌（四人麻，2020年至今）的公开牌谱数据。


## 参考资源

- [houou-logs 项目](https://github.com/Apricot-S/houou-logs)
- [Tenhou log 解析](https://github.com/NegativeMjark/tenhou-log)
- [Riichi Wiki - Tedashi/Tsumogiri](https://riichi.wiki/Tedashi_and_tsumogiri)

## 许可证

MIT License

## 贡献指南

欢迎提交 Issue 和 Pull Request！

## 联系方式

如有问题或建议，请通过 GitHub Issues 联系。

---

**注意**：本工具仅用于学习和研究目的，请勿用于任何商业用途或违反天凤服务条款的行为。
