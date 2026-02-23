# 下载 2020-2024 年历史数据指南

## 🎯 目标
下载天凤凤凰桌 2020-2024 年的历史对局数据

## 📦 当前数据情况
- ✅ 已有：2025-2026年数据（344,636局，1.85 GB）
- ⏳ 需要：2020-2024年数据

## 🚀 下载方法

### 方法 1：使用自动下载脚本（推荐）

我已经创建了 `download_historical_data.py` 脚本，可以自动下载和导入历史数据。

**使用步骤：**

```bash
# 运行脚本
py -3.12 download_historical_data.py
```

**功能：**
- 自动从天凤官网下载 2020-2024 年 ZIP 存档
- 自动导入到数据库
- 显示下载进度
- 错误处理和重试选项

**预计时间：**
- 下载：2-5 小时（取决于网络速度）
- 导入：1-2 小时
- 总计：3-7 小时

**所需空间：**
- ZIP 文件：约 5-10 GB
- 导入后数据库：约 10-15 GB
- 总计：约 15-25 GB

### 方法 2：手动下载（如果自动脚本失败）

**步骤：**

1. **手动下载 ZIP 文件**

从以下 URL 下载各年份数据：
```
https://tenhou.net/sc/raw/dat/2020.zip
https://tenhou.net/sc/raw/dat/2021.zip
https://tenhou.net/sc/raw/dat/2022.zip
https://tenhou.net/sc/raw/dat/2023.zip
https://tenhou.net/sc/raw/dat/2024.zip
```

可以使用浏览器或下载工具（如 IDM、迅雷）下载。

2. **创建存档目录**
```bash
mkdir data\archives
```

3. **将 ZIP 文件移动到存档目录**
```
data/archives/2020.zip
data/archives/2021.zip
data/archives/2022.zip
data/archives/2023.zip
data/archives/2024.zip
```

4. **逐个导入到数据库**
```bash
py -3.12 -m houou_logs import data/tenhou.db data/archives/2020.zip
py -3.12 -m houou_logs import data/tenhou.db data/archives/2021.zip
py -3.12 -m houou_logs import data/tenhou.db data/archives/2022.zip
py -3.12 -m houou_logs import data/tenhou.db data/archives/2023.zip
py -3.12 -m houou_logs import data/tenhou.db data/archives/2024.zip
```

### 方法 3：分批下载（推荐新手）

如果不想一次下载所有年份，可以先下载最近的年份测试：

**只下载 2024 年：**
```bash
# 1. 手动下载
# 浏览器访问: https://tenhou.net/sc/raw/dat/2024.zip

# 2. 导入
py -3.12 -m houou_logs import data/tenhou.db data/archives/2024.zip
```

测试成功后再下载其他年份。

## ⚠️ 注意事项

1. **网络稳定性**
   - 每个文件可能有数百 MB 到数 GB
   - 如果网络不稳定，建议使用下载工具（支持断点续传）

2. **磁盘空间**
   - 确保有足够的磁盘空间（至少 30 GB 空闲）
   - 数据库会持续增长

3. **下载时间**
   - 取决于网络速度
   - 建议在网络空闲时段进行
   - 可以后台运行，不影响其他操作

4. **数据完整性**
   - 下载完成后检查文件大小
   - 如果导入失败，可能需要重新下载

## 🔍 验证下载结果

下载并导入完成后，运行检查脚本：

```bash
py -3.12 check_latest_log.py
```

应该看到类似输出：
```
总日志数: 1,500,000+ 局
数据库大小: 15+ GB

日期范围:
  最早: 2020-XX-XX
  最新: 2026-02-05

按年份统计:
  2020年: XXX,XXX 局
  2021年: XXX,XXX 局
  2022年: XXX,XXX 局
  2023年: XXX,XXX 局
  2024年: XXX,XXX 局
  2025年: 310,329 局
  2026年: 34,307 局
```

## 💡 建议

**对于开发和测试：**
- 现有的 2025-2026 年数据（344,636局）已经足够
- 可以先用现有数据开发和测试应用功能

**对于生产使用：**
- 下载完整历史数据（2020-2026）
- 获得更大的样本量，提高统计准确性

## 📞 遇到问题？

如果遇到以下问题：
- **下载失败**：检查网络连接，尝试使用浏览器或下载工具
- **导入错误**：检查 ZIP 文件是否完整，尝试重新下载
- **磁盘空间不足**：清理不需要的文件，或使用外部硬盘

## 🎯 快速开始

**最简单的方式（推荐）：**

```bash
# 直接运行自动下载脚本
py -3.12 download_historical_data.py
```

脚本会引导你完成整个过程！

---

创建日期：2026-02-13
