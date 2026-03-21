# -*- coding: utf-8 -*-
"""
py-shortqt v1.2.0 启动器
"""

import json
import sys
import subprocess
from pathlib import Path

def main():
    project_root = Path(__file__).parent
    config_file = project_root / "config" / "accounts.json"
    
    print()
    print("=" * 40)
    print("py-shortqt v1.2.0 启动器")
    print("=" * 40)
    print()
    
    # 检查配置文件
    if not config_file.exists():
        print("X 未找到 config/accounts.json")
        print("请先配置 API Key")
        input("\n按回车键退出...")
        return
    
    # 加载账户配置
    with open(config_file, encoding='utf-8') as f:
        accounts = json.load(f)['accounts']
    
    # 1. 选择模式
    print("请选择交易模式:")
    print()
    print("1. 实盘交易")
    print("2. 模拟交易")
    print()
    
    mode = input("请输入选项 (1-2, 默认 1): ").strip()
    if mode == "" or mode == "1":
        mode = "1"
        main_file = "src/main_live.py"
        print("\n已选择：实盘交易")
    elif mode == "2":
        main_file = "src/main_sim.py"
        print("\n已选择：模拟交易")
    else:
        mode = "1"
        main_file = "src/main_live.py"
        print("\n无效选项，使用默认：实盘交易")
    
    # 2. 选择账户
    print()
    print("选择账户:")
    print()
    
    for i, acc in enumerate(accounts):
        acc_type = "测试网" if acc.get('testnet') else "实盘"
        print(f"{i+1}. {acc['name']} ({acc_type})")
    
    print()
    account_choice = input("请输入账户编号 (1-N, 默认 1): ").strip()
    if account_choice == "":
        account_choice = 1
    else:
        account_choice = int(account_choice)
    
    # 获取账户名称
    account_name = accounts[account_choice - 1]['name']
    
    print()
    print(f"启动账户：{account_name}")
    print(f"启动文件：{main_file}")
    print()
    
    # 启动程序
    subprocess.run([sys.executable, main_file, "--account", account_name])
    
    print()
    input("按回车键退出...")

if __name__ == "__main__":
    main()
