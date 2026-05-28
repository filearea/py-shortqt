# -*- coding: utf-8 -*-
"""
币安 WebSocket 连接 - 实时行情数据
"""

import asyncio
import json
import os
import time
from decimal import Decimal
import websockets


class BinanceListener:
    """币安 WebSocket 监听器"""

    def __init__(self, symbol: str, ws_url: str):
        self.symbol = symbol.lower()
        # 币安组合流格式：/stream?streams=symbol@stream1/symbol@stream2/...
        ws_base = ws_url.replace('/ws', '')  # 去掉 /ws 后缀
        self.ws_url = f"{ws_base}/stream?streams={self.symbol}@bookTicker/{self.symbol}@depth20@100ms/{self.symbol}@kline_1m"
        self.last_price = None
        self.orderbook = {'bids': [], 'asks': []}
        self.current_kline = None
        self.callbacks = []
        self.running = False
        self.connected = False

        # bookTicker 节流：最多每 200ms 回调一次（避免事件循环拥塞）
        self._last_book_ticker_time = 0

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

                ws = await asyncio.wait_for(
                    websockets.connect(self.ws_url, **connect_kwargs),
                    timeout=15
                )
                self.connected = True
                print("[OK] WebSocket 已连接")
                reconnect_delay = 3  # 连接成功后重置延迟

                try:
                    while self.running:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(message)
                        await self.process_message(data)
                finally:
                    self.connected = False
                    # 安全关闭连接（抑制 websockets 内部 AttributeError）
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    raise RuntimeError("WebSocket 连接已断开")

            except websockets.exceptions.ConnectionClosed:
                self.connected = False
                if self.running:
                    print(f"⚠ 行情连接断开，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            except (RuntimeError, asyncio.CancelledError):
                if self.running:
                    print(f"⚠ 行情连接断开，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            except asyncio.TimeoutError:
                self.connected = False
                if self.running:
                    print(f"⚠ 行情连接超时，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            except Exception as e:
                self.connected = False
                if self.running:
                    print(f"⚠ 行情连接错误：{e}，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
    
    async def process_message(self, data: dict):
        """处理 WebSocket 消息"""
        try:
            event_type = data.get('e', '')
            stream_name = data.get('stream', '')

            # 组合流消息格式：{"stream":"ethusdc@kline_1m", "data":{...}}
            # 需要解包 data 字段
            if stream_name and 'data' in data:
                data = data['data']
                event_type = data.get('e', '')

            # bookTicker 事件（获取最新价格）
            if event_type == 'bookTicker':
                price = Decimal(data['b'])
                self.last_price = price
                # 节流回调：最多每 200ms 触发一次，避免事件循环拥塞
                now = time.monotonic()
                if now - self._last_book_ticker_time >= 0.2:
                    self._last_book_ticker_time = now
                    for callback in self.callbacks:
                        asyncio.create_task(callback('ticker', {'price': price}))
                return

            elif event_type == 'depthUpdate':
                # 深度更新（更新内存 + 非阻塞回调，不阻塞 ws.recv()）
                bids = [[Decimal(p), Decimal(q)] for p, q in data.get('b', [])]
                asks = [[Decimal(p), Decimal(q)] for p, q in data.get('a', [])]
                if bids:
                    self.orderbook['bids'] = bids[:10]
                if asks:
                    self.orderbook['asks'] = asks[:10]
                # 触发回调（UI 显示订单簿依赖此回调）
                for callback in self.callbacks:
                    asyncio.create_task(callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']}))

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
                    # 初始深度快照（更新内存 + 回调，只触发一次）
                    bids = [[Decimal(p), Decimal(q)] for p, q in data.get('bids', [])]
                    asks = [[Decimal(p), Decimal(q)] for p, q in data.get('asks', [])]
                    self.orderbook['bids'] = bids[:10]
                    self.orderbook['asks'] = asks[:10]
                    for callback in self.callbacks:
                        asyncio.create_task(callback('depth', {'bids': self.orderbook['bids'], 'asks': self.orderbook['asks']}))
        except Exception as e:
            # 只打印错误信息，不打完整堆栈避免刷屏
            pass
