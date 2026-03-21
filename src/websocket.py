# -*- coding: utf-8 -*-
"""
币安 WebSocket 连接 - 实时行情数据
"""

import asyncio
import json
from decimal import Decimal
import websockets


class BinanceListener:
    """币安 WebSocket 监听器"""
    
    def __init__(self, symbol: str, ws_url: str):
        self.symbol = symbol.lower()
        # 订阅：实时成交 + 5 档深度（更新更频繁）
        self.ws_url = f"{ws_url}/{self.symbol}@aggTrade/{self.symbol}@depth5@100ms"
        self.last_price = None
        self.orderbook = {'bids': [], 'asks': []}
        self.callbacks = []
        self.running = False
        self.connected = False
        self.msg_count = 0
        self.last_log_time = 0
    
    def add_callback(self, callback):
        """添加数据回调"""
        self.callbacks.append(callback)
    
    async def connect(self):
        """连接 WebSocket"""
        self.running = True
        import time
        print(f"[WebSocket] 尝试连接：{self.ws_url}")
        
        self.msg_count = 0
        self.last_log_time = time.time()
        
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.connected = True
                    print(f"✓ 已连接：{self.ws_url}")
                    while self.running:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(message)
                        await self.process_message(data)
                        
                        # 每秒打印一次统计
                        now = time.time()
                        if now - self.last_log_time >= 1.0:
                            print(f"[WebSocket] {self.msg_count} 条/秒 | 最新价：{self.last_price}")
                            self.msg_count = 0
                            self.last_log_time = now
                        self.msg_count += 1
            except websockets.exceptions.ConnectionClosed:
                self.connected = False
                print("WebSocket 断开，重连中...")
                await asyncio.sleep(3)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.connected = False
                print(f"WebSocket 错误：{e}")
                await asyncio.sleep(3)
    
    async def process_message(self, data: dict):
        """处理 WebSocket 消息"""
        try:
            if 'e' in data:
                if data['e'] == 'aggTrade':
                    # 实时成交
                    price = Decimal(data['p'])
                    self.last_price = price
                    for callback in self.callbacks:
                        await callback('ticker', {'price': price})
                
                elif data['e'] == 'depthUpdate':
                    # 深度更新
                    bids = [[Decimal(p), Decimal(q)] for p, q in data.get('b', [])]
                    asks = [[Decimal(p), Decimal(q)] for p, q in data.get('a', [])]
                    if bids:
                        self.orderbook['bids'] = bids[:10]
                    if asks:
                        self.orderbook['asks'] = asks[:10]
                    for callback in self.callbacks:
                        await callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']})
                
                elif data['e'] == 'bookTicker':
                    # 最优买卖价（更新频率最高，约 100ms 一次）
                    bid_price = Decimal(data['b'])
                    bid_qty = Decimal(data['B'])
                    ask_price = Decimal(data['a'])
                    ask_qty = Decimal(data['A'])
                    self.orderbook['bids'] = [[bid_price, bid_qty]]
                    self.orderbook['asks'] = [[ask_price, ask_qty]]
                    for callback in self.callbacks:
                        await callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']})
            else:
                # 可能是深度快照（没有 'e' 字段）
                if 'lastUpdateId' in data and 'bids' in data and 'asks' in data:
                    # 初始深度快照
                    bids = [[Decimal(p), Decimal(q)] for p, q in data.get('bids', [])]
                    asks = [[Decimal(p), Decimal(q)] for p, q in data.get('asks', [])]
                    self.orderbook['bids'] = bids[:10]
                    self.orderbook['asks'] = asks[:10]
                    for callback in self.callbacks:
                        await callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']})
                
                # 处理 bookTicker（也没有 'e' 字段，但有 'b' 和 'a'）
                elif 'b' in data and 'a' in data and 'B' in data and 'A' in data:
                    # bookTicker 数据
                    bid_price = Decimal(data['b'])
                    bid_qty = Decimal(data['B'])
                    ask_price = Decimal(data['a'])
                    ask_qty = Decimal(data['A'])
                    self.orderbook['bids'] = [[bid_price, bid_qty]]
                    self.orderbook['asks'] = [[ask_price, ask_qty]]
                    for callback in self.callbacks:
                        await callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']})
        except Exception as e:
            print(f"[WebSocket] 处理消息错误：{e}")
            import traceback
            traceback.print_exc()
