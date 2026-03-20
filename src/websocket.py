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
        self.ws_url = f"{ws_url}/{self.symbol}@aggTrade/{self.symbol}@depth20@100ms"
        self.last_price = None
        self.orderbook = {'bids': [], 'asks': []}
        self.callbacks = []
        self.running = False
        self.connected = False
    
    def add_callback(self, callback):
        """添加数据回调"""
        self.callbacks.append(callback)
    
    async def connect(self):
        """连接 WebSocket"""
        self.running = True
        print(f"[WebSocket] 尝试连接：{self.ws_url}")
        
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.connected = True
                    print(f"✓ 已连接：{self.ws_url}")
                    while self.running:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(message)
                        await self.process_message(data)
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
        if 'e' in data:
            if data['e'] == 'aggTrade':
                # 实时成交
                price = Decimal(data['p'])
                self.last_price = price
                for callback in self.callbacks:
                    await callback('ticker', {'price': price})
            
            elif data['e'] == 'depthUpdate':
                # 深度快照
                bids = [[Decimal(p), Decimal(q)] for p, q in data.get('b', [])]
                asks = [[Decimal(p), Decimal(q)] for p, q in data.get('a', [])]
                if bids:
                    self.orderbook['bids'] = bids[:10]
                if asks:
                    self.orderbook['asks'] = asks[:10]
                for callback in self.callbacks:
                    await callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']})
