# -*- coding: utf-8 -*-
"""
py-shortqt v1.1.1 - 统一启动入口
支持模拟模式和实盘模式
"""

import asyncio
import sys
import os
from pathlib import Path
from decimal import Decimal

# 设置 UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    os.environ['PYTHONUTF8'] = '1'

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 导入配置
from config.settings import USE_LIVE_TRADING, TESTNET


def print_banner():
    """打印欢迎横幅"""
    print("=" * 70)
    print("py-shortqt v1.1.1 - Maker Scalper 剥头皮交易系统")
    print("=" * 70)
    print()
    print("作者：魔力塔互动娱乐")
    print("版本：v1.1.1 实盘版")
    print()


def choose_mode():
    """选择交易模式"""
    print("请选择交易模式：")
    print()
    print("  1. 实盘模式（使用真实资金）")
    print("  2. 模拟模式（使用虚拟资金）")
    print()
    
    # 如果有默认配置，提示但不自动选择
    if USE_LIVE_TRADING:
        print(f"提示：配置文件默认使用实盘模式 (TESTNET={TESTNET})")
        print()
    
    while True:
        choice = input("请输入选择 (1=实盘，2=模拟) [默认=2]: ").strip()
        if choice == '1':
            return 'live'
        elif choice == '' or choice == '2':
            return 'sim'
        else:
            print("无效输入，请重新输入")


async def run_live_mode():
    """运行实盘模式"""
    from src.main_live import main as live_main
    
    print()
    print("=" * 70)
    print("启动实盘模式...")
    print("=" * 70)
    print()
    
    if TESTNET:
        print("⚠️  警告：当前使用测试网环境")
        print()
    
    await live_main()


async def run_sim_mode():
    """运行模拟模式"""
    from src.main_sim import main as sim_main
    
    print()
    print("=" * 70)
    print("启动模拟模式...")
    print("=" * 70)
    print()
    
    await sim_main()


async def main():
    """主函数"""
    print_banner()
    
    # 选择模式
    mode = choose_mode()
    
    print()
    print("正在初始化...")
    print()
    
    try:
        if mode == 'live':
            await run_live_mode()
        else:
            await run_sim_mode()
    except KeyboardInterrupt:
        print("\n\n用户中断，退出程序...")
    except Exception as e:
        print(f"\n\n程序异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print()
    print("程序已退出")
    print()


if __name__ == "__main__":
    asyncio.run(main())
