@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0

echo.
echo ╔════════════════════════════════════════╗
echo ║     py-shortqt v1.2.0 启动器          ║
echo ╚════════════════════════════════════════╝
echo.

:: 检查配置文件是否存在
if not exist "config\accounts.json" (
    echo ✗ 未找到 config/accounts.json
    echo 请先配置 API Key
    echo.
    pause
    exit /b 1
)

:: 显示账户列表
echo 选择账户:
echo.
python -c "import json; accounts=json.load(open('config/accounts.json', encoding='utf-8'))['accounts']; [print(f'{i+1}. {a[\"name\"]} ({\"测试网\" if a[\"testnet\"] else \"实盘\"})') for i,a in enumerate(accounts)]"

echo.
set /p choice="请输入选项 (1-N, 直接回车使用默认账户): "

if "%choice%"=="" (
    :: 使用默认账户
    echo.
    echo 启动账户：默认账户
    echo.
    python src/main_live.py
) else (
    :: 根据选择启动
    for /f "delims=" %%a in ('python -c "import json; accounts=json.load(open('config/accounts.json', encoding='utf-8'))['accounts']; print(accounts[%choice%-1]['name'])"') do set account=%%a
    
    echo.
    echo 启动账户：%account%
    echo.
    python src/main_live.py --account "%account%"
)

echo.
pause
