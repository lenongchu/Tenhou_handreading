# 下载 2020-2024 年历史数据说明

根据 houou-logs 的工作方式，下载历史年份数据需要：

## 问题分析

`fetch --archive` 只获取最近的存档数据（2025-2026），不包括更早的年份。

## 解决方案

### 方案 1：从天凤官网下载年度存档 ZIP 文件

天凤提供历史年份的 ZIP 存档文件：

**下载地址格式：**
```
https://tenhou.net/sc/raw/dat/2020.zip
https://tenhou.net/sc/raw/dat/2021.zip
https://tenhou.net/sc/raw/dat/2022.zip
https://tenhou.net/sc/raw/dat/2023.zip
https://tenhou.net/sc/raw/dat/2024.zip
```

**导入流程：**
1. 手动下载 ZIP 文件
2. 使用 houou-logs 导入：
   ```bash
   py -3.12 -m houou_logs import data/tenhou.db 2020.zip
   ```

### 方案 2：使用自动下载脚本

我可以创建一个脚本自动下载并导入历史年份数据。

## 注意事项

1. **文件很大**：每年的 ZIP 文件可能有几百 MB 到数 GB
2. **网络连接**：需要稳定的网络连接
3. **存储空间**：确保有足够的磁盘空间（约 10-20 GB）
4. **下载时间**：可能需要数小时完成

## 建议

- 如果只是测试应用，现有的 2025-2026 年数据已经足够（34万局）
- 如果需要完整历史数据分析，可以逐年下载导入
