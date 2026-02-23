@echo off
chcp 65001 >nul
echo ========================================
echo 天凤数据后台下载工具
echo ========================================
echo.
echo 正在下载4人麻将对局数据...
echo 此窗口可以最小化，不影响其他工作
echo 按 Ctrl+C 可以随时停止下载
echo.
echo ========================================
echo.

cd /d "%~dp0"
py -3.12 -m houou_logs download data/tenhou.db --players 4

echo.
echo ========================================
echo 下载已完成或中断
echo ========================================
pause
