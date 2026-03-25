@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0

rem 设置控制台窗口尺寸（宽度 120 列，高度 40 行）
mode con: cols=120 lines=40

python launcher.py
pause
