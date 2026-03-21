@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0

echo.
echo ========================================
echo py-shortqt v1.2.0 启动器
echo ========================================
echo.

:: 检查配置文件
if not exist "config\accounts.json" (
    echo X 未找到 config/accounts.json
    echo 请先配置 API Key
    echo.
    pause
    exit /b 1
)

:: 1. 选择模式
echo 请选择交易模式:
echo.
echo 1. 实盘交易
echo 2. 模拟交易
echo.
set /p mode=请输入选项 (1-2, 默认 1): 

if "%mode%"=="" set mode=1

if "%mode%"=="1" (
    echo.
    echo 已选择：实盘交易
    set main_file=src/main_live.py
) else if "%mode%"=="2" (
    echo.
    echo 已选择：模拟交易
    set main_file=src/main_sim.py
) else (
    echo.
    echo 无效选项，使用默认：实盘交易
    set mode=1
    set main_file=src/main_live.py
)

:: 2. 选择账户
echo.
echo 选择账户:
echo.

:: 使用 Python 显示账户列表
python -c "import json; accounts=json.load(open('config/accounts.json', encoding='utf-8'))['accounts']; [print(f'{i+1}. {a[\"name\"]} ({\"测试网\" if a[\"testnet\"] else \"实盘\"})') for i,a in enumerate(accounts)]"

echo.
set /p account_choice=请输入账户编号 (1-N, 默认 1): 

if "%account_choice%"=="" set account_choice=1

:: 获取账户名称
for /f "delims=" %%a in ('python -c "import json; accounts=json.load(open('config/accounts.json', encoding='utf-8'))['accounts']; print(accounts[%account_choice%-1]['name'])"') do set account_name=%%a

echo.
echo 启动账户：%account_name%
echo 启动文件：%main_file%
echo.

python %main_file% --account "%account_name%"

echo.
pause
