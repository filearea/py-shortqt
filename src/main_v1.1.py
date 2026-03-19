# -*- coding: utf-8 -*-
"""
py-shortqt v1.1.0 - 主程序入口
量化交易框架 - 开发分支
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

from config.settings import SYMBOL, LOG_BASE_DIR
from src.ui.menu import ModeSelector
from src.trading.simulation import SimulationTrader
from src.trading.live import LiveTrader
from src.quant.manager import QuantStrategyManager


def print_banner():
    """打印欢迎横幅"""
    print("=" * 70)
    print("py-shortqt v1.1.0 - 量化交易框架")
    print("=" * 70)
    print()


async def run_simulation_mode(sub_mode: str):
    """运行模拟模式"""
    print("\n" + "=" * 70)
    print("模拟模式")
    print("=" * 70)
    
    if sub_mode == "manual":
        print("启动：手动快捷键模式")
        trader = SimulationTrader(mode="manual")
        await trader.run()
    elif sub_mode == "quant":
        print("启动：量化信号模式")
        trader = SimulationTrader(mode="quant")
        await trader.run()


async def run_live_mode():
    """运行实盘模式"""
    print("\n" + "=" * 70)
    print("实盘模式")
    print("=" * 70)
    
    trader = LiveTrader()
    await trader.run()


async def run_quant_strategy_manager():
    """运行量化策略管理"""
    print("\n" + "=" * 70)
    print("量化策略管理")
    print("=" * 70)
    
    manager = QuantStrategyManager()
    await manager.run()


async def main_menu():
    """主菜单"""
    print_banner()
    
    selector = ModeSelector()
    
    while True:
        try:
            # 显示主菜单
            print("\n请选择交易模式：")
            print("  1. 模拟模式")
            print("  2. 实盘模式")
            print("  3. 量化策略管理")
            print("  0. 退出")
            print()
            
            choice = input("请输入选项 (0-3): ").strip()
            
            if choice == "1":
                # 模拟模式
                print("\n模拟模式子选项：")
                print("  1. 手动快捷键模式")
                print("  2. 量化信号模式")
                print("  0. 返回主菜单")
                print()
                
                sub_choice = input("请输入选项 (0-2): ").strip()
                
                if sub_choice == "1":
                    await run_simulation_mode("manual")
                elif sub_choice == "2":
                    await run_simulation_mode("quant")
                elif sub_choice == "0":
                    continue
                else:
                    print("无效选项")
            
            elif choice == "2":
                # 实盘模式
                await run_live_mode()
            
            elif choice == "3":
                # 量化策略管理
                await run_quant_strategy_manager()
            
            elif choice == "0":
                print("\n再见！")
                break
            
            else:
                print("无效选项，请重新输入")
        
        except KeyboardInterrupt:
            print("\n\n用户中断")
            break
        except Exception as e:
            print(f"\n错误：{e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main_menu())
    except KeyboardInterrupt:
        print("\n已中断")
