# -*- coding: utf-8 -*-
"""
py-shortqt 启动器
直接选择账户启动实盘交易
"""

import json
import sys
import subprocess
from pathlib import Path

def main():
    project_root = Path(__file__).parent
    config_file = project_root / "config" / "accounts.json"

    # 读取版本号
    version_file = project_root / "VERSION"
    if version_file.exists():
        try:
            version = version_file.read_text(encoding='utf-8').strip()
        except UnicodeDecodeError:
            version = version_file.read_text(encoding='utf-16').strip()
    else:
        version = "unknown"

    print()
    print("=" * 40)
    print(f"py-shortqt v{version} 启动器")
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

    account_name = accounts[account_choice - 1]['name']
    main_file = "src/main_live.py"

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
