# -*- coding: utf-8 -*-
"""调试脚本"""
import sys
import traceback

print("开始加载模块...")

try:
    print("1. 导入 asyncio...")
    import asyncio
    print("   ✓ asyncio OK")
    
    print("2. 导入 pathlib...")
    from pathlib import Path
    print("   ✓ pathlib OK")
    
    print("3. 导入 config.settings...")
    sys.path.insert(0, str(Path(__file__).parent))
    from config.settings import BINANCE_WS_URL, SYMBOL
    print(f"   ✓ config OK: {SYMBOL}")
    
    print("4. 导入 src.logger...")
    from src.logger import TradeLogger
    print("   ✓ logger OK")
    
    print("5. 导入 src.trader...")
    from src.trader import TradeState
    print("   ✓ trader OK")
    
    print("6. 导入 src.websocket...")
    from src.websocket import BinanceListener
    print("   ✓ websocket OK")
    
    print("7. 导入 src.ui...")
    from src.ui import TradingUI
    print("   ✓ ui OK")
    
    print("8. 导入 rich...")
    from rich.live import Live
    print("   ✓ rich OK")
    
    print("9. 导入 websockets...")
    import websockets
    print("   ✓ websockets OK")
    
    print("\n✅ 所有模块加载成功！")
    print("\n开始测试运行...")
    
    from src.main import TradingBot
    
    async def test():
        print("10. 创建 TradingBot...")
        bot = TradingBot()
        print("    ✓ TradingBot 创建成功")
        print("\n按 Ctrl+C 停止")
        await bot.run()
    
    import asyncio
    asyncio.run(test())
    
except Exception as e:
    print(f"\n❌ 错误：{e}")
    print("\n详细错误信息：")
    traceback.print_exc()
    input("\n按回车退出...")
