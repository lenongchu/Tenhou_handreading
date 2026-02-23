@echo off
cd /d "%~dp0"
REM 下载 2020-2024 年历史数据
echo ============================================================
echo 天凤历史数据下载工具
echo ============================================================
echo.
echo 此脚本将下载 2020-2024 年的凤凰桌对局数据
echo 预计需要：
echo   - 下载时间：2-5 小时
echo   - 磁盘空间：15-25 GB
echo   - 网络流量：5-10 GB
echo.
echo 请确保：
echo   1. 网络连接稳定
echo   2. 有足够的磁盘空间
echo   3. 不要中断下载过程
echo.
pause

py -3.12 download_historical_data.py

pause
