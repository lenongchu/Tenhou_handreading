# 📥 如何下载 2020-2024 年历史数据

## ✅ 已完成准备

我已经为你创建了以下工具：

### 1️⃣ 自动下载脚本
- **文件**：`download_historical_data.py`
- **功能**：自动下载并导入 2020-2024 年数据
- **特点**：
  - ✅ 自动下载 ZIP 文件
  - ✅ 自动导入到数据库
  - ✅ 显示下载进度
  - ✅ 错误处理
  - ✅ 支持断点续传

### 2️⃣ 快速启动脚本
- **文件**：`下载历史数据_2020-2024.bat`
- **用法**：双击运行
- **优点**：一键启动，简单方便

### 3️⃣ 详细指南
- **文件**：`DOWNLOAD_2020_2024_GUIDE.md`
- **内容**：完整的下载和导入说明

## 🚀 现在就开始下载

### 方法 1：双击批处理文件（最简单）
```
双击：下载历史数据_2020-2024.bat
```

### 方法 2：命令行运行
```bash
py -3.12 download_historical_data.py
```

### 方法 3：手动下载（如果自动下载失败）

1. 浏览器访问以下链接下载 ZIP 文件：
   ```
   https://tenhou.net/sc/raw/dat/2020.zip
   https://tenhou.net/sc/raw/dat/2021.zip
   https://tenhou.net/sc/raw/dat/2022.zip
   https://tenhou.net/sc/raw/dat/2023.zip
   https://tenhou.net/sc/raw/dat/2024.zip
   ```

2. 保存到：`data/archives/` 目录

3. 逐个导入：
   ```bash
   py -3.12 -m houou_logs import data/tenhou.db data/archives/2020.zip
   py -3.12 -m houou_logs import data/tenhou.db data/archives/2021.zip
   py -3.12 -m houou_logs import data/tenhou.db data/archives/2022.zip
   py -3.12 -m houou_logs import data/tenhou.db data/archives/2023.zip
   py -3.12 -m houou_logs import data/tenhou.db data/archives/2024.zip
   ```

## 📊 预期结果

下载完成后，你将拥有：

| 年份 | 预计局数 |
|------|---------|
| 2020年 | ~300,000 局 |
| 2021年 | ~300,000 局 |
| 2022年 | ~300,000 局 |
| 2023年 | ~300,000 局 |
| 2024年 | ~300,000 局 |
| 2025年 | 310,329 局（已有）|
| 2026年 | 34,307 局（已有）|
| **总计** | **~1,900,000 局** |

**最终数据库大小**：约 15-20 GB

## ⏱️ 时间估算

- **下载时间**：2-5 小时（取决于网络速度）
- **导入时间**：1-2 小时
- **总计**：3-7 小时

## 💾 空间要求

- ZIP 文件：约 5-10 GB
- 数据库（导入后）：约 10-15 GB
- **建议预留**：至少 30 GB 空闲空间

## 🎯 建议

### 如果你是第一次使用：
1. **先用现有数据测试**
   - 你已经有 344,636 局（2025-2026年）
   - 足够测试和开发应用功能
   
2. **确认应用正常工作后再下载完整数据**
   - 避免浪费时间和带宽
   - 确保一切就绪

### 如果你准备好了：
1. **确保网络稳定**
2. **确保有足够磁盘空间**
3. **运行**：`下载历史数据_2020-2024.bat`
4. **耐心等待**：可能需要数小时
5. **运行完成后验证**：`py -3.12 check_latest_log.py`

## 📞 需要帮助？

查看详细指南：`DOWNLOAD_2020_2024_GUIDE.md`

---

**准备好了吗？双击 `下载历史数据_2020-2024.bat` 开始！** 🚀
