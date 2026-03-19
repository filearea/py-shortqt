# -*- coding: utf-8 -*-
"""
模拟交易模块
"""

import asyncio
from decimal import Decimal


class SimulationTrader:
    """模拟交易器"""
    
    def __init__(self, mode: str = "manual"):
        self.mode = mode
        self.running = False
    
    async def run(self):
        """运行模拟交易"""
        print(f"\n模拟交易启动 - 模式：{self.mode}")
        print("=" * 70)
        
        if self.mode == "manual":
            await self._run_manual()
        elif self.mode == "quant":
            await self._run_quant()
    
    async def _run_manual(self):
        """手动快捷键模式"""
        print("\n手动快捷键模式")
        print("=" * 70)
        
        # 使用 v1.0.1 的交易逻辑
        from src.ui.legacy import TradingUI
        from src.main import TradingBot
        
        bot = TradingBot()
        await bot.run()
    
    async def _run_quant(self):
        """量化信号模式"""
        print("\n量化信号模式")
        print()
        
        # TODO: 实现量化信号交易逻辑
        print("[提示] 此功能正在开发中...")
        print("  后续将支持：")
        print("  - 自动根据量化信号开仓")
        print("  - 自动止盈止损")
        print("  - 自动仓位管理")
        print()
        
        input("按回车返回菜单...")
