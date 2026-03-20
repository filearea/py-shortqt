@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0
echo.
echo ========================================
echo py-shortqt v1.1.1 启动器
echo ========================================
echo.
python src/main.py
pause
