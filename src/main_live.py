# -*- coding: utf-8 -*-
"""
实盘交易主入口
"""

import asyncio
import sys
import os
from pathlib import Path
from decimal import Decimal
from datetime import datetime

# 设置 UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    os.environ['PYTHONUTF8'] = '1'

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import SYMBOL, LEVERAGE_LIMIT, ACTUAL_LEVERAGE, TESTNET
from src.api.binance_client import BinanceAPIError
from src.trading.live import LiveTrader
from src.websocket import BinanceListener
from src.ui.live_ui import LiveTradingUI
from src.logger import TradeLogger
from src.system_logger import get_system_logger

try:
    from rich.live import Live
    import msvcrt
except ImportError as e:
    print(f"缺少依赖库：{e}")
    print("请运行：pip install -r requirements.txt")
    sys.exit(1)


class LiveTradingBot:
    """实盘交易机器人"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.symbol = SYMBOL
        self.running = True
        
        # 获取系统日志
        self.sys_logger = get_system_logger()
        self.sys_logger.info("=== py-shortqt v1.1.1 启动 ===")
        
        # 初始化交易日志（项目根目录/logs/）
        project_root = Path(__file__).parent.parent
        self.logger = TradeLogger(project_root / "logs")
        
        # 初始化实盘交易器
        self.trader = LiveTrader(
            api_key=api_key,
            api_secret=api_secret,
            symbol=self.symbol,
            leverage_limit=LEVERAGE_LIMIT,
            actual_leverage=ACTUAL_LEVERAGE,
            testnet=TESTNET,
            logger=self.logger
        )
        
        # 初始化行情 WebSocket
        self.listener = BinanceListener(self.symbol.lower(), "wss://fstream.binance.com/ws")
        self.listener.add_callback(self.on_market_data)
        
        # UI
        self.ui = LiveTradingUI(self.trader, LEVERAGE_LIMIT, Decimal('1'), Decimal('3'))
        
        self.sys_logger.info(f"交易对：{self.symbol}, 杠杆：{LEVERAGE_LIMIT}x (实际{ACTUAL_LEVERAGE}x)")
    
    async def on_market_data(self, event_type: str, data: dict):
        """市场数据回调"""
        try:
            if event_type == 'ticker':
                price = data['price']
                self.trader.update_price(price)
                
                # 记录价格
                self.logger.record_price(price)
            
            elif event_type == 'depth':
                bids = data.get('bids', [])
                asks = data.get('asks', [])
                self.trader.update_orderbook(bids, asks)
        except Exception:
            pass
    
    async def place_order(self, side: str):
        """开仓"""
        await self.trader.open_position(side)
    
    async def cancel_order(self):
        """撤单"""
        # 如果有提前平仓单，撤销并恢复止盈止损
        if self.trader.early_close_order:
            self.trader.cancel_early_close()
        # 否则撤销开仓挂单
        elif self.trader.pending_order:
            self.trader.cancel_open_order()
    
    async def close_position_early(self):
        """提前平仓"""
        await self.trader.close_position_early()
    
    async def run(self):
        """运行主循环"""
        print("=" * 70)
        print("py-shortqt v1.1.1 - 实盘交易模式")
        print("=" * 70)
        
        # 1. 初始化实盘连接
        if not await self.trader.initialize():
            print("\n✗ 实盘初始化失败，退出")
            return
        
        # 2. 连接行情 WebSocket
        print("连接行情 WebSocket...")
        ws_task = asyncio.create_task(self.listener.connect())
        
        # 等待行情连接（最多 10 秒）
        print("等待连接...")
        for i in range(20):  # 10 秒 = 20 * 0.5 秒
            if self.listener.connected:
                print("✓ 行情已连接")
                break
            await asyncio.sleep(0.5)
        else:
            print("✗ 行情连接超时，退出")
            self.listener.running = False
            return
        
        print("=" * 70)
        print("操作：↑/W 做多  |  ↓/S 做空  |  ←撤单  |  →提前平仓  |  Q 退出")
        print("=" * 70)
        
        # 3. 主循环
        print("\n进入主循环...")
        try:
            # 使用自适应高度（不强制全屏）
            with Live(
                self.ui.render(),
                refresh_per_second=10,
                screen=False  # 不使用全屏，自适应终端高度
            ) as live:
                while self.running:
                    try:
                        live.update(self.ui.render())
                    except Exception as e:
                        print(f"\n[UI 更新错误] {e}")
                        continue
                    
                    # 键盘输入
                    if msvcrt.kbhit():
                        try:
                            key = msvcrt.getch()
                            if key == b'\xe0' or key == b'\x00':
                                key = msvcrt.getch()
                                if key == b'H':  # ↑
                                    await self.place_order('LONG')
                                elif key == b'P':  # ↓
                                    await self.place_order('SHORT')
                                elif key == b'M':  # →
                                    await self.close_position_early()
                                elif key == b'K':  # ←
                                    await self.cancel_order()
                            elif key.upper() == b'W':
                                await self.place_order('LONG')
                            elif key.upper() == b'S':
                                await self.place_order('SHORT')
                            elif key.lower() == b'q':
                                await self.cancel_order()
                            elif key.upper() == b'Q':
                                self.running = False
                        except Exception as e:
                            print(f"\n[键盘输入错误] {e}")
                    
                    # 同步账户信息
                    self.trader.sync_account()
                    
                    await asyncio.sleep(0.05)
        
        except KeyboardInterrupt:
            print("\n用户中断")
        except Exception as e:
            print(f"\n[主循环异常] {e}")
            import traceback
            traceback.print_exc()
        
        # 4. 清理
        print("\n清理资源...")
        self.running = False
        self.listener.running = False
        await self.trader.cleanup()
        
        # 等待 WebSocket 任务结束（最多 2 秒）
        try:
            await asyncio.wait_for(ws_task, timeout=2.0)
        except asyncio.TimeoutError:
            print("WebSocket 任务超时，强制结束")
        
        self.logger.close()
        
        print("\n" + "=" * 70)
        print("交易结束")
        print(f"最终余额：{self.trader.available_balance:.4f} USDC")
        print("=" * 70)
        
        # 防止窗口立即关闭
        print("\n按回车键退出...")
        try:
            input()
        except:
            pass
        print(f"最终余额：{self.trader.available_balance:.4f} USDC")
        print("=" * 70)


def load_account():
    """加载账号配置"""
    import json
    config_file = Path(__file__).parent.parent / "config" / "accounts.json"
    
    if not config_file.exists():
        print("✗ 未找到 config/accounts.json")
        print("请先配置 API Key")
        return None, None
    
    with open(config_file, encoding='utf-8') as f:
        config = json.load(f)
    
    accounts = config.get('accounts', [])
    if not accounts:
        print("✗ 未配置账号")
        return None, None
    
    # 使用默认账号或第一个账号
    default_name = config.get('settings', {}).get('default_account')
    for acc in accounts:
        if acc['name'] == default_name or default_name is None:
            return acc['api_key'], acc['api_secret']
    
    return accounts[0]['api_key'], accounts[0]['api_secret']


async def main():
    # 加载账号
    api_key, api_secret = load_account()
    if not api_key:
        return
    
    print(f"\n使用账号：主账号")
    print(f"交易对：{SYMBOL}")
    print(f"杠杆：{LEVERAGE_LIMIT}x (实际仓位 {ACTUAL_LEVERAGE}x)")
    print()
    
    # 创建并运行机器人
    bot = LiveTradingBot(api_key, api_secret)
    await bot.run()


if __name__ == "__main__":
    try:
        # 添加崩溃日志
        import traceback
        import sys
        
        def log_exception(exc_type, exc_value, exc_traceback):
            with open('crash.log', 'w', encoding='utf-8') as f:
                f.write(f"崩溃时间：{datetime.now()}\n")
                f.write(f"异常类型：{exc_type.__name__}\n")
                f.write(f"异常消息：{exc_value}\n")
                f.write("\n堆栈跟踪:\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
        
        sys.excepthook = log_exception
        
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已中断")
