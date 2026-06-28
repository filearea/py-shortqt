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

    def __init__(self, symbol: str, ws_url: str, log_func=None):
        self.symbol = symbol.lower()
        self._log = log_func or print
        # 组合流（bookTicker + depth）+ 独立 @trade 流（替代被代理阻断的 @aggTrade）
        ws_base = ws_url.replace('/ws', '')  # 去掉 /ws 后缀
        self.ws_url = f"{ws_base}/stream?streams={self.symbol}@bookTicker/{self.symbol}@depth20@100ms"
        self.ws_trade_url = f"{ws_base}/ws/{self.symbol}@trade"
        self.last_price = None
        self.orderbook = {'bids': [], 'asks': []}
        self.current_kline = None
        self.callbacks = []
        self.running = False
        self.connected = False

        # bookTicker 节流：最多每 200ms 回调一次（避免事件循环拥塞）
        self._last_book_ticker_time = 0

        # v1.10.0: aggTrade 诊断计数器
        self._agg_trade_count = 0
        self._agg_trade_first_logged = False
        self._seen_event_types = set()

        # v1.10.0: @trade 合成实时 K 线（替代被代理阻断的 @kline_1m）
        self._kl_minute = 0      # 当前分钟起始毫秒
        self._kl_open = None
        self._kl_high = None
        self._kl_low = None
        self._kl_close = None
        self._kl_volume = Decimal('0')
        self._kl_first_trade_ts = 0

        # 代理配置
        self.proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
        if self.proxy:
            self._log(f"[WebSocket] 使用代理：{self.proxy}")
    
    def add_callback(self, callback):
        """添加数据回调"""
        self.callbacks.append(callback)
    
    async def connect(self):
        """连接 WebSocket（主组合流 + 逐笔成交流）"""
        self.running = True
        self._log(f"[WebSocket] 主组合流：{self.ws_url}")
        self._log(f"[WebSocket] 逐笔成交流：{self.ws_trade_url}")

        await asyncio.gather(
            self._ws_loop(self.ws_url, '主组合流'),
            self._ws_loop(self.ws_trade_url, '逐笔成交'),
        )

    async def _ws_loop(self, url: str, name: str):
        """单个 WebSocket 连接循环（带自动重连）"""
        reconnect_delay = 3
        max_reconnect_delay = 30

        while self.running:
            try:
                connect_kwargs = {
                    'close_timeout': 5,
                    'open_timeout': 10
                }
                if self.proxy:
                    connect_kwargs['proxy'] = self.proxy

                ws = await asyncio.wait_for(
                    websockets.connect(url, **connect_kwargs),
                    timeout=15
                )
                self.connected = True
                self._log(f"[OK] {name} 已连接")
                reconnect_delay = 3

                try:
                    while self.running:
                        message = await asyncio.wait_for(ws.recv(), timeout=30)
                        data = json.loads(message)
                        await self.process_message(data)
                finally:
                    self.connected = False
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    raise RuntimeError(f"{name} 连接已断开")

            except websockets.exceptions.ConnectionClosed:
                self.connected = False
                if self.running:
                    self._log(f"⚠ {name} 断开，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            except (RuntimeError, asyncio.CancelledError):
                if self.running:
                    self._log(f"⚠ {name} 断开，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            except asyncio.TimeoutError:
                self.connected = False
                if self.running:
                    self._log(f"⚠ {name} 连接超时，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
            except Exception as e:
                self.connected = False
                if self.running:
                    self._log(f"⚠ {name} 连接错误：{e}，{reconnect_delay}秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)
    
    async def process_message(self, data: dict):
        """处理 WebSocket 消息"""
        try:
            event_type = data.get('e', '')
            stream_name = data.get('stream', '')

            # 组合流消息格式：{"stream":"ethusdc@kline_1m", "data":{...}}
            if stream_name and 'data' in data:
                data = data['data']
                event_type = data.get('e', '')

            # 诊断：记录首次出现的事件类型（组合流和独立流都会被记录）
            if event_type and event_type not in self._seen_event_types:
                self._seen_event_types.add(event_type)
                self._log(f'[WebSocket] 事件类型: "{event_type}" stream={stream_name or "独立流"}')

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

            elif event_type in ('aggTrade', 'trade'):
                # v1.10.0：成交数据（@trade 逐笔 / @aggTrade 聚合，格式兼容）
                self._agg_trade_count += 1
                if not self._agg_trade_first_logged:
                    self._agg_trade_first_logged = True
                    self._log(f'[WebSocket] 首个 {event_type} 事件: p={data.get("p")} q={data.get("q")} m={data.get("m")} T={data.get("T")}')

                raw_ts = data.get('T') or data.get('E') or int(time.time() * 1000)
                try:
                    ts = int(raw_ts)
                except (ValueError, TypeError):
                    ts = int(time.time() * 1000)
                trade = {
                    'price': Decimal(str(data.get('p') or '0')),
                    'qty': Decimal(str(data.get('q') or '0')),
                    'm': data.get('m', True),  # true=买方是maker(卖方taker), false=买方是taker
                    'ts': ts
                }
                for callback in self.callbacks:
                    asyncio.create_task(callback('aggTrade', trade))

                # 实时 K 线合成（从 @trade 逐笔聚合 OHLCV）
                price = trade['price']
                qty = trade['qty']

                # 异常价保护：偏离参考价 >10% 的成交不参与合成，防止极端值污染 K 线
                if self.last_price is not None:
                    dev = abs(price - self.last_price) / self.last_price
                    if dev > Decimal('0.10'):
                        return

                minute_start = ts // 60000 * 60000

                if self._kl_minute == 0:
                    # 首个成交，初始化当前 K 线
                    self._kl_minute = minute_start
                    self._kl_open = price
                    self._kl_high = price
                    self._kl_low = price
                    self._kl_close = price
                    self._kl_volume = qty
                    self._kl_first_trade_ts = ts
                elif minute_start > self._kl_minute:
                    # 进入新分钟 → 闭合上一根 K 线，推送回调
                    closed_kline = {
                        'timestamp': self._kl_minute,
                        'open': self._kl_open,
                        'high': self._kl_high,
                        'low': self._kl_low,
                        'close': self._kl_close,
                        'volume': self._kl_volume,
                        'is_closed': True
                    }
                    self.current_kline = closed_kline
                    for callback in self.callbacks:
                        asyncio.create_task(callback('kline', closed_kline))
                    # 开始新 K 线
                    self._kl_minute = minute_start
                    self._kl_open = price
                    self._kl_high = price
                    self._kl_low = price
                    self._kl_close = price
                    self._kl_volume = qty
                    self._kl_first_trade_ts = ts
                else:
                    # 同一分钟，更新 OHLCV
                    if price > self._kl_high:
                        self._kl_high = price
                    if price < self._kl_low:
                        self._kl_low = price
                    self._kl_close = price
                    self._kl_volume += qty
                    # 实时更新 current_kline（未闭合）
                    self.current_kline = {
                        'timestamp': self._kl_minute,
                        'open': self._kl_open,
                        'high': self._kl_high,
                        'low': self._kl_low,
                        'close': self._kl_close,
                        'volume': self._kl_volume,
                        'is_closed': False
                    }

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
            self._log(f'[WebSocket] 消息处理异常: {type(e).__name__}: {e}')
