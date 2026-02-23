# 问题总结和解决方案

## 🔴 当前问题

你尝试查询 "1s-2s" 时报错：`no such table: game_states`

## 根本原因

1. **数据库缺少 `game_states` 表** - 已解决 ✅
   - 已创建表结构

2. **日志数据未解析** - 发现问题 ⚠️
   - 数据库中的 XML 是 GZIP 压缩的
   - 已添加解压缩代码
   - 但解析器返回的 GameState 对象**没有舍牌数据**

3. **解析器问题** - 需要修复 🔧
   - `mjlog_parser.py` 解析 XML 后，返回的 GameState 对象中 `discards` 列表为空
   - 需要检查解析逻辑，确保正确提取舍牌信息

## 等价性匹配 (你的担心)

关于 "1s-2s" = "1m-2m" = "9p-8p" 的等价性：

**✅ 代码已经实现了！**

查看 `src/tile_normalizer.py`:
- `normalize_sequence()` 方法会将所有等价的序列标准化
- 数字等价：1-2-3 = 2-3-4 = 7-8-9 (都标准化为 1-2-3)
- 花色等价：1s-2s = 1m-2m = 1p-2p (都标准化为同一种花色)

问题不在匹配算法，而在于**没有数据可以匹配**！

## 🎯 立即可行的解决方案

### 方案 A: 修复解析器 (推荐但需要时间)

需要调试 `src/mjlog_parser.py` 找出为什么 `discards` 列表为空。

### 方案 B: 使用现有工具验证数据 (快速测试)

```bash
# 验证 XML 格式
py -3.12 test_parser_output.py
```

### 方案 C: 创建测试数据 (临时方案)

手动插入一些测试数据到 `game_states` 表，验证查询和等价性匹配是否工作。

## 📝 下一步行动

1. **调试解析器**
   - 检查 `mjlog_parser.py` 中的 `parse()` 方法
   - 查看 XML 标签解析逻辑 (可能是 `<D>`, `<E>`, `<F>`, `<G>` 标签)
   - 确保正确提取舍牌信息

2. **重新处理日志**
   - 修复解析器后运行 `process_logs_sample.py`
   - 填充 `game_states` 表

3. **测试查询**
   - 测试 "1s-2s" 能否找到匹配
   - 验证 "1m-2m" 返回相同结果 (等价性)

## 技术细节

### 数据库结构 ✅
```sql
CREATE TABLE game_states (
    id INTEGER PRIMARY KEY,
    log_id TEXT,
    player_id INTEGER,
    turn INTEGER,
    tile INTEGER,
    is_tsumogiri INTEGER,
    normalized_pattern TEXT,  -- 这里存储标准化后的模式！
    ...
)
```

### 等价性实现 ✅
`TileNormalizer.normalize_sequence([18, 19])` → "1s-2s"
`TileNormalizer.normalize_sequence([0, 1])` → "1s-2s"  (同样的结果！)

### 当前缺失 ❌
- 解析的对局数据中没有舍牌信息
- `game_states` 表是空的

## 联系方式

如需继续调试，请：
1. 检查 `src/mjlog_parser.py` 的实现
2. 查看天凤 XML 格式文档
3. 或提供一个实际的 XML 样本进行分析
