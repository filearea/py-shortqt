# -*- coding: utf-8 -*-
"""
币安 WebSocket 连接 - 实时行情数据
"""

import asyncio
import json
import os
from decimal import Decimal
import websockets


class BinanceListener:
    """币安 WebSocket 监听器"""

    def __init__(self, symbol: str, ws_url: str):
        self.symbol = symbol.lower()
        # v1.5.5 修复：用 bookTicker 替代 aggTrade，获取最新价格更稳定
        self.ws_url = f"{ws_url}/{self.symbol}@bookTicker/{self.symbol}@depth20@100ms/{self.symbol}@kline_1m"
        self.last_price = None
        self.orderbook = {'bids': [], 'asks': []}
        self.current_kline = None  # v1.4.0 新增：当前 K 线
        self.callbacks = []
        self.running = False
        self.connected = False
        
        # 代理配置
        self.proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        if self.proxy:
            print(f"[WebSocket] 使用代理：{self.proxy}")
    
    def add_callback(self, callback):
        """添加数据回调"""
        self.callbacks.append(callback)
    
    async def connect(self):
        """连接 WebSocket"""
        self.running = True
        print(f"[WebSocket] 尝试连接：{self.ws_url}")
        
        reconnect_delay = 3  # 初始重连延迟（秒）
        max_reconnect_delay = 30  # 最大重连延迟
        
        while self.running:
            try:
                connect_kwargs = {
                    'close_timeout': 5,
                    'open_timeout': 10
                }
                if self.proxy:
                    connect_kwargs['proxy'] = self.proxy

                async with websockets.connect(self.ws_url, **connect_kwargs) as ws:
                    self.connected = True
                    print("[OK] WebSocket 已连接")
                    reconnect_delay = 3  # 连接成功后重置延迟

                    while self.running:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(message)
                        await self.process_message(data)
            except websockets.exceptions.ConnectionClosed:
                self.connected = False
                if self.running:  # 只有在非正常退出时才显示重连
                    print(f"WebSocket 断开，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.connected = False
                print(f"WebSocket 错误：{e}，{reconnect_delay}秒后重连...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
    
    async def process_message(self, data: dict):
        """处理 WebSocket 消息 - v1.4.0 新增 K 线处理"""
        try:
            event_type = data.get('e', '')

            # bookTicker 事件（获取最新价格，比aggTrade更稳定）
            # 格式: {"e":"bookTicker", "u":123, "s":"ETHUSDC", "b":"2113.02", "B":"10.5", "a":"2113.03", "A":"5.2"}
            if event_type == 'bookTicker':
                # 用买一价作为最新价格
                price = Decimal(data['b'])
                self.last_price = price
                for callback in self.callbacks:
                    await callback('ticker', {'price': price})
                return

            if event_type == 'aggTrade':
                # 实时成交（保留作兼容）
                price = Decimal(data['p'])
                self.last_price = price
                for callback in self.callbacks:
                    await callback('ticker', {'price': price})

            elif event_type == 'depthUpdate':
                # 深度更新
                bids = [[Decimal(p), Decimal(q)] for p, q in data.get('b', [])]
                asks = [[Decimal(p), Decimal(q)] for p, q in data.get('a', [])]
                if bids:
                    self.orderbook['bids'] = bids[:10]
                if asks:
                    self.orderbook['asks'] = asks[:10]
                for callback in self.callbacks:
                    await callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']})

            elif event_type == 'kline':
                # v1.4.0 新增：K 线数据
                kline_data = data.get('k', {})
                kline = {
                    'timestamp': kline_data.get('t', 0),
                    'open': Decimal(kline_data.get('o', '0')),
                    'high': Decimal(kline_data.get('h', '0')),
                    'low': Decimal(kline_data.get('l', '0')),
                    'close': Decimal(kline_data.get('c', '0')),
                    'volume': Decimal(kline_data.get('v', '0')),
                    'is_closed': kline_data.get('x', False)  # K 线是否完成
                }

                # 保存当前 K 线（用于指标计算）
                self.current_kline = kline

                # 触发 K 线更新回调
                for callback in self.callbacks:
                    await callback('kline', kline)

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
        except Exception as e:
            # 只打印错误信息，不打完整堆栈避免刷屏
            pass
