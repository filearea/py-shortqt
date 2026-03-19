# -*- coding: utf-8 -*-
"""
Maker Scalper Backtest - 主程序入口
挂单剥头皮模拟交易系统
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

# 导入项目模块
from config.settings import (
    BINANCE_WS_URL, SYMBOL, LEVERAGE, INITIAL_BALANCE,
    TAKE_PROFIT_POINTS, STOP_LOSS_POINTS, LOG_BASE_DIR, TUI_REFRESH_RATE
)
from src.logger import TradeLogger
from src.trader import TradeState
from src.websocket import BinanceListener
from src.ui import TradingUI

try:
    import websockets
    from rich.console import Console
    from rich.live import Live
except ImportError as e:
    print(f"缺少依赖库：{e}")
    print("请运行：pip install -r requirements.txt")
    sys.exit(1)


class TradingBot:
    """交易机器人"""
    
    def __init__(self):
        # 初始化日志
        log_dir = Path(__file__).parent / LOG_BASE_DIR
        self.logger = TradeLogger(log_dir)
        
        # 初始化交易状态
        self.state = TradeState(
            self.logger, LEVERAGE,
            Decimal(str(INITIAL_BALANCE)),
            Decimal(str(TAKE_PROFIT_POINTS)),
            Decimal(str(STOP_LOSS_POINTS))
        )
        
        # 初始化界面
        self.ui = TradingUI(
            self.state, LEVERAGE,
            Decimal(str(TAKE_PROFIT_POINTS)),
            Decimal(str(STOP_LOSS_POINTS))
        )
        
        # 初始化 WebSocket
        self.listener = BinanceListener(SYMBOL, BINANCE_WS_URL)
        self.listener.add_callback(self.on_market_data)
        
        self.running = True
    
    async def on_market_data(self, event_type: str, data: dict):
        """市场数据回调"""
        import time
        now = time.time()
        
        try:
            if event_type == 'ticker':
                price = data['price']
                if self.state.last_price and price != self.state.last_price:
                    self.state.last_price_change = price - self.state.last_price
                self.state.last_price = price
                
                # 更新频率
                self.state.update_count = getattr(self.state, 'update_count', 0) + 1
                if hasattr(self.state, 'last_update_time') and self.state.last_update_time:
                    elapsed = now - self.state.last_update_time
                    if elapsed > 0:
                        self.state.updates_per_second = 1.0 / elapsed
                self.state.last_update_time = now
                
                # 记录价格
                self.logger.record_price(price)
                
                # 检查挂单成交
                if self.state.pending_order:
                    filled = self.state.check_pending_order_filled(price)
                    if filled:
                        print(f"[DEBUG] ✓ 成交！")
                
                # 检查止盈止损
                if self.state.position:
                    result = self.state.check_tp_sl(price)
                    if result:
                        print(f"[DEBUG] ✓ {result['type']}!")
                
                # 记录市场快照
                self.logger.record_snapshot(price, self.state.orderbook)
            
            elif event_type == 'depth':
                self.state.orderbook = data
        except Exception as e:
            print(f"\n[DEBUG] on_market_data 错误：{e}")
            import traceback
            traceback.print_exc()
    
    async def place_order(self, side: str):
        """下 Maker 挂单"""
        try:
            print(f"\n[DEBUG] ========== 开始下单 {side} ==========")
            
            if self.state.position is not None:
                print(f"[DEBUG] 拒绝：已有持仓")
                return
            print(f"[DEBUG] 检查持仓：无 ✓")
            
            if self.state.pending_order is not None:
                print(f"[DEBUG] 拒绝：已有挂单")
                return
            print(f"[DEBUG] 检查挂单：无 ✓")
            
            if self.state.last_price is None:
                print(f"[DEBUG] 拒绝：等待行情")
                return
            print(f"[DEBUG] 最新价：{self.state.last_price} ✓")
            
            # Maker 挂单：基于买一价/卖一价
            bid1 = self.state.orderbook['bids'][0][0] if self.state.orderbook.get('bids') else None
            ask1 = self.state.orderbook['asks'][0][0] if self.state.orderbook.get('asks') else None
            
            print(f"[DEBUG] 买一={bid1} | 卖一={ask1}")
            
            if side == 'LONG':
                if bid1 is None:
                    print(f"[DEBUG] 买一价为空，无法开多")
                    return
                order_price = bid1
                print(f"[DEBUG] 做多挂单价：{order_price}")
            else:
                if ask1 is None:
                    print(f"[DEBUG] 卖一价为空，无法开空")
                    return
                order_price = ask1
                print(f"[DEBUG] 做空挂单价：{order_price}")
            
            position, size = self.state.can_open_position(side, order_price)
            print(f"[DEBUG] can_open_position 返回：position={position is not None}, size={size}")
            
            if position is None:
                print(f"[DEBUG] 无法开仓")
                return
            
            print(f"[DEBUG] 调用 place_pending_order...")
            self.state.place_pending_order(position)
            print(f"[DEBUG] 挂单成功！pending_order={self.state.pending_order}")
            print(f"[DEBUG] =========={side} 下单完成==========")
        except Exception as e:
            print(f"\n[DEBUG] place_order 错误：{e}")
            import traceback
            traceback.print_exc()
    
    async def cancel_order(self):
        """撤单"""
        print(f"\n[DEBUG] 尝试撤单...")
        result = self.state.cancel_pending_order()
        if result:
            print(f"[DEBUG] 撤单成功")
        else:
            print(f"[DEBUG] 没有可撤销的挂单")
    
    async def close_position_early(self):
        """提前平仓"""
        try:
            print(f"\n[DEBUG] ========== 提前平仓 ==========")
            
            if self.state.position is None:
                print(f"[DEBUG] 没有持仓，无法平仓")
                return
            
            pos = self.state.position
            print(f"[DEBUG] 当前持仓：{pos['side']} @ {pos['entry_price']}")
            
            bid1 = self.state.orderbook['bids'][0][0] if self.state.orderbook.get('bids') else None
            ask1 = self.state.orderbook['asks'][0][0] if self.state.orderbook.get('asks') else None
            
            print(f"[DEBUG] 买一={bid1} | 卖一={ask1}")
            
            # 多单持仓 → 挂卖单 @ 卖一价（Maker）
            # 空单持仓 → 挂买单 @ 买一价（Maker）
            if pos['side'] == 'LONG':
                if ask1 is None:
                    print(f"[DEBUG] 卖一价为空")
                    return
                close_price = ask1
                print(f"[DEBUG] 多单平仓：挂卖单 @ {close_price}")
            else:
                if bid1 is None:
                    print(f"[DEBUG] 买一价为空")
                    return
                close_price = bid1
                print(f"[DEBUG] 空单平仓：挂买单 @ {close_price}")
            
            self.state.close_position_early(pos['side'], close_price)
            print(f"[DEBUG] 提前平仓挂单成功")
            print(f"[DEBUG] ==========")
        except Exception as e:
            print(f"[DEBUG] close_position_early 错误：{e}")
            import traceback
            traceback.print_exc()
    
    async def run(self):
        """运行主循环"""
        ws_task = asyncio.create_task(self.listener.connect())
        
        print("=" * 70)
        print("Maker Scalper Backtest - 挂单剥头皮模拟测试")
        print("=" * 70)
        print(f"初始保证金：{INITIAL_BALANCE} USDT | 杠杆：{LEVERAGE}x")
        print(f"止盈：+{TAKE_PROFIT_POINTS} 点 | 止损：-{STOP_LOSS_POINTS} 点")
        print("=" * 70)
        print("操作：↑/W 做多  |  ↓/S 做空  |  ←撤单  |  →提前平仓  |  Q 退出")
        print("=" * 70)
        print("连接中...")
        
        await asyncio.sleep(2)
        
        if not self.listener.connected:
            print("等待连接...")
            for _ in range(10):
                if self.listener.connected:
                    break
                await asyncio.sleep(1)
        
        if not self.listener.connected:
            print("✗ 连接失败，退出")
            return
        
        if sys.platform == 'win32':
            import msvcrt
            try:
                with Live(self.ui.render(), refresh_per_second=TUI_REFRESH_RATE, screen=False) as live:
                    while self.running:
                        try:
                            live.update(self.ui.render())
                        except Exception as e:
                            print(f"界面刷新错误：{e}")
                            break
                        
                        if msvcrt.kbhit():
                            try:
                                key = msvcrt.getch()
                                if key == b'\xe0' or key == b'\x00':  # 方向键前缀
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
                                print(f"按键处理错误：{e}")
                        
                        await asyncio.sleep(0.05)
            except KeyboardInterrupt:
                print("\n用户中断")
            except Exception as e:
                print(f"运行错误：{e}")
        
        self.running = False
        self.listener.running = False
        await ws_task
        self.logger.close()
        
        print("\n" + "=" * 70)
        print("交易结束")
        print(f"最终余额：{self.state.balance:.2f} USDT")
        print(f"总盈亏：{float(self.state.balance) - INITIAL_BALANCE:+.2f} USDT")
        print(f"完成交易：{len([t for t in self.state.trades if t['type'] in ['TP', 'SL', 'EARLY']])} 笔")
        print("=" * 70)


async def main():
    bot = TradingBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已中断")
