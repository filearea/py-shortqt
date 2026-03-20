# -*- coding: utf-8 -*-
"""
完整功能测试脚本
"""

import asyncio
import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import SYMBOL, LEVERAGE_LIMIT, ACTUAL_LEVERAGE
import json

# 加载账号
cfg = json.load(open('config/accounts.json', encoding='utf-8'))
acc = cfg['accounts'][0]

from src.trading.live import LiveTrader
from src.websocket import BinanceListener

async def test_full():
    print("=" * 70)
    print("py-shortqt v1.1.1 - 完整功能测试")
    print("=" * 70)
    
    trader = LiveTrader(
        api_key=acc['api_key'],
        api_secret=acc['api_secret'],
        symbol=SYMBOL,
        leverage_limit=LEVERAGE_LIMIT,
        actual_leverage=ACTUAL_LEVERAGE,
        testnet=False
    )
    
    # 1. 初始化
    print("\n[1/6] 初始化实盘连接...")
    if not await trader.initialize():
        print("✗ 初始化失败")
        return
    print("✓ 初始化成功")
    
    # 2. 连接行情 WebSocket
    print("\n[2/6] 连接行情 WebSocket...")
    listener = BinanceListener(SYMBOL.lower(), "wss://fstream.binance.com/ws")
    
    async def on_market_data(event_type: str, data: dict):
        if event_type == 'ticker' and 'price' in data:
            trader.update_price(data['price'])
        elif event_type == 'depth':
            trader.update_orderbook(data.get('bids', []), data.get('asks', []))
    
    listener.add_callback(on_market_data)
    
    ws_task = asyncio.create_task(listener.connect())
    await asyncio.sleep(2)
    
    if not listener.connected:
        print("✗ 行情连接失败")
        return
    print("✓ 行情已连接")
    
    # 3. 等待价格更新
    print("\n[3/6] 等待价格数据...")
    for i in range(10):
        if trader.last_price:
            print(f"✓ 当前价格：{trader.last_price}")
            break
        await asyncio.sleep(0.5)
    else:
        print("✗ 未收到价格数据")
        listener.running = False
        await ws_task
        return
    
    # 4. 测试开仓（1U 保证金，25 倍杠杆）
    print("\n[4/6] 测试开仓（1U × 25x）...")
    
    # 同步最新账户信息
    trader.sync_account()
    print(f"  可用余额：{trader.available_balance}")
    
    # 等待价格稳定
    await asyncio.sleep(1)
    
    # 尝试开多单
    if trader.last_price and trader.orderbook.get('bids'):
        print(f"  当前价格：{trader.last_price}")
        
        # 计算仓位：1U * 25 倍 / 价格
        test_size = (trader.available_balance * Decimal('25') / trader.last_price).quantize(Decimal('0.001'))
        print(f"  计划开仓：{test_size} ETH")
        
        # 检查最小下单量
        if test_size * trader.last_price < Decimal('5'):
            print("⚠️  名义价值低于 5U，可能无法开仓")
            print("  跳过实际下单测试")
        else:
            print("  下达 Maker 挂单...")
            success = await trader.open_position('LONG')
            
            if success:
                print(f"✓ 挂单成功")
                print(f"  挂单价格：{trader.pending_order['price']}")
                print(f"  挂单数量：{trader.pending_order['size']}")
                
                # 等待 3 秒看是否成交
                print("\n  等待 3 秒观察成交...")
                await asyncio.sleep(3)
                
                if trader.position:
                    print(f"✓ 已成交！持仓：{trader.position['size']} @ {trader.position['entry_price']}")
                    
                    # 测试撤单/平仓
                    print("\n[5/6] 测试提前平仓...")
                    await trader.close_position_early()
                    await asyncio.sleep(2)
                    
                    if trader.early_close_order:
                        print(f"✓ 提前平仓挂单成功 @ {trader.early_close_order['price']}")
                        
                        # 测试撤单恢复
                        print("\n[6/6] 测试撤单恢复止盈止损...")
                        trader.cancel_early_close()
                        await asyncio.sleep(1)
                        
                        if trader.tp_order and trader.sl_order:
                            print("✓ 止盈止损单已恢复")
                        else:
                            print("⚠️  止盈止损单未恢复")
                    else:
                        print("✗ 提前平仓挂单失败")
                else:
                    print("⚠️  挂单未成交（正常，可能价格未触及）")
            else:
                print("✗ 开仓失败")
    
    # 清理
    print("\n清理资源...")
    listener.running = False
    await ws_task
    await trader.cleanup()
    
    print("\n" + "=" * 70)
    print("测试完成")
    print("=" * 70)

if __name__ == "__main__":
    try:
        asyncio.run(test_full())
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n测试异常：{e}")
        import traceback
        traceback.print_exc()
