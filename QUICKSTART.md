# 立直麻将读牌应用 - 快速入门

## 已完成的工作

### ✅ 步骤1：项目结构搭建
- 创建完整的项目目录结构
- 配置 `requirements.txt` 依赖文件
- 编写详细的 `README.md` 文档
- 配置 `setup.py` 安装脚本
- 创建 `.gitignore` 文件

### ✅ 步骤2：核心模块实现

1. **数据下载模块** (`src/data_downloader.py`)
   - 封装 houou-logs 工具
   - 支持年度数据下载
   - 支持增量更新

2. **牌谱解析模块** (`src/mjlog_parser.py`)
   - 解析天凤 mjlog XML 格式
   - 提取舍牌序列（手切/摸切）
   - 追踪手牌状态和可见牌
   - 处理宝牌指示牌

3. **牌型标准化模块** (`src/tile_normalizer.py`)
   - 实现牌型等价转换
   - 处理数字和花色标准化
   - 支持宝牌约束标准化

4. **数据库模块** (`src/database.py`)
   - 设计 SQLite 数据库架构
   - 实现游戏状态存储
   - 支持快速查询和过滤

5. **模式匹配引擎** (`src/pattern_matcher.py`)
   - 支持通配符查询
   - 实现巡目范围过滤
   - 处理可见枚数约束

6. **概率计算引擎** (`src/probability_calculator.py`)
   - 统计目标牌分布
   - 计算0/1/2/3张概率
   - 支持样本限制

7. **GUI界面** (`src/gui_app.py`)
   - 使用 PyQt5 构建
   - 数据管理功能
   - 查询输入界面
   - 结果显示面板

### ✅ 步骤3：测试套件
- `tests/test_parser.py` - 牌谱解析测试
- `tests/test_normalizer.py` - 标准化测试
- `tests/test_matcher.py` - 模式匹配测试

## 项目结构

```
Tenhou_handreading/
├── README.md              # 项目说明
├── QUICKSTART.md          # 快速入门（本文件）
├── requirements.txt       # Python依赖
├── setup.py               # 安装配置
├── .gitignore            # Git忽略文件
├── src/                  # 源代码
│   ├── __init__.py
│   ├── data_downloader.py
│   ├── mjlog_parser.py
│   ├── tile_normalizer.py
│   ├── database.py
│   ├── pattern_matcher.py
│   ├── probability_calculator.py
│   └── gui_app.py
├── tests/                # 测试代码
│   ├── __init__.py
│   ├── test_parser.py
│   ├── test_normalizer.py
│   └── test_matcher.py
└── data/                 # 数据存储目录（空）
```

## 下一步工作

虽然所有模块的基本框架已经完成，但还需要进一步的工作才能使应用完全运行：

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 验证 houou-logs 工具
确保 houou-logs 已正确安装并可在命令行中使用：
```bash
houou-logs --help
```

如果未安装，请参考：https://github.com/Apricot-S/houou-logs

### 3. 完善 mjlog 解析器
- 实现副露（N标签）的完整解码逻辑
- 验证手切/摸切判定的准确性
- 测试实际的 mjlog XML 文件

### 4. 集成各模块
- 连接数据下载与解析流程
- 实现完整的数据处理管道
- 在解析时调用标准化模块

### 5. 测试与调试
```bash
# 运行单元测试
pytest tests/ -v

# 运行特定测试
pytest tests/test_parser.py -v
```

### 6. 启动 GUI 应用
```bash
python -m src.gui_app
```

## 核心功能说明

### 查询示例
**场景**：对手第2-10巡内先手切7s，后手切9s，宝牌为6s，场上8s可见2-3枚，想知道他有多大概率还有6s？

**输入**：
- 舍牌模式：`7s-9s`
- 目标牌：`6s`
- 巡目范围：2 - 10
- 宝牌约束：宝牌为 `6s`
- 场况约束：`8s` 可见 `2-3` 枚

**输出**（示例）：
```
目标牌: 6s
有0张的概率: 40.5%
有1张的概率: 45.2%
有2张的概率: 12.8%
有3张的概率: 1.5%
样本数量: 8,432 局
```

## 技术特点

1. **牌型标准化**：自动处理等价模式（如 7s-9s ≡ 1m-3m）
2. **灵活查询**：支持通配符、巡目范围、可见约束
3. **高效存储**：SQLite数据库，优化索引
4. **直观界面**：PyQt5 GUI，易于使用

## 注意事项

1. **数据量**：天凤凤凰桌数据量巨大，初次下载需要较长时间和大量存储空间
2. **解析性能**：解析和标准化所有牌谱需要大量计算资源
3. **测试数据**：建议先用少量数据测试各模块功能
4. **模块完善**：当前代码为框架实现，部分功能需要根据实际 mjlog 格式调整

## 参考资源

- [houou-logs 项目](https://github.com/Apricot-S/houou-logs)
- [Tenhou log 解析](https://github.com/NegativeMjark/tenhou-log)
- [Riichi Wiki - Tedashi/Tsumogiri](https://riichi.wiki/Tedashi_and_tsumogiri)

## 许可证

MIT License

---

**开发进度**：✅ 所有基础模块已完成，可以开始集成和测试！
