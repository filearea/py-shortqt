@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0

echo.
echo ========================================
echo py-shortqt v1.2.0 启动器
echo ========================================
echo.

:: Check if config file exists
if not exist "config\accounts.json" (
    echo X 未找到 config/accounts.json
    echo 请先配置 API Key
    echo.
    pause
    exit /b 1
)

:: Show account list using Python
echo 选择账户:
echo.
python -c "import json; accounts=json.load(open('config/accounts.json', encoding='utf-8'))['accounts']; [print(f'{i+1}. {a[\"name\"]} ({\"testnet\" if a[\"testnet\"] else \"live\"})') for i,a in enumerate(accounts)]"

echo.
set /p choice=Enter account number (1-N, or press Enter for default): 

if "%choice%"=="" (
    echo.
    echo Starting with default account...
    echo.
    python src/main_live.py
) else (
    for /f "delims=" %%a in ('python -c "import json; accounts=json.load(open('config/accounts.json', encoding='utf-8'))['accounts']; print(accounts[%choice%-1]['name'])"') do set account=%%a
    
    echo.
    echo Starting account: %account%
    echo.
    python src/main_live.py --account "%account%"
)

echo.
pause
