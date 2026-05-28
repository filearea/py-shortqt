# -*- coding: utf-8 -*-
"""
币安用户数据流 WebSocket
监听订单状态更新和账户变更
"""

import asyncio
import json
import websockets
from typing import Callable, Optional


class UserStreamWebSocket:
    """用户数据流 WebSocket 客户端"""

    LISTEN_KEY_EXPIRE_SECONDS = 3600  # listen key 有效期 60 分钟
    KEEP_ALIVE_INTERVAL = 1800  # 保活间隔 30 分钟

    def __init__(self, listen_key: str, api_client=None, testnet: bool = False, log_func=None):
        self.listen_key = listen_key
        self.api_client = api_client  # 用于 keep-alive 调用
        self._log = log_func or print  # 日志函数，默认 print

        if testnet:
            base_url = "wss://stream.binancefuture.com"
        else:
            base_url = "wss://fstream.binance.com"

        self.ws_url = f"{base_url}/ws/{listen_key}"
        self.running = False
        self.connected = False
        self.ws: Optional[websockets.WebSocketClientProtocol] = None

        # 代理配置
        import os
        self.proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')

        # 回调函数
        self.order_callbacks: list[Callable] = []
        self.account_callbacks: list[Callable] = []

        # 保活任务
        self._keep_alive_task: Optional[asyncio.Task] = None

    def add_order_callback(self, callback: Callable):
        """添加订单更新回调"""
        self.order_callbacks.append(callback)

    def add_account_callback(self, callback: Callable):
        """添加账户更新回调"""
        self.account_callbacks.append(callback)

    async def _keep_alive_loop(self):
        """定时保活 listen key，防止过期"""
        while self.running:
            await asyncio.sleep(self.KEEP_ALIVE_INTERVAL)
            if not self.running:
                break
            try:
                if self.api_client:
                    self.api_client.keep_alive_listen_key(self.listen_key)
                    self._log(f"[UserStream] listenKey 保活成功")
                else:
                    self._log(f"[UserStream] 无 api_client，无法保活 listenKey")
            except Exception as e:
                self._log(f"[UserStream] listenKey 保活失败：{e}")

    async def connect(self):
        """连接 WebSocket"""
        self.running = True

        # 启动保活任务
        self._keep_alive_task = asyncio.create_task(self._keep_alive_loop())

        reconnect_delay = 3  # 初始重连延迟（秒）
        max_reconnect_delay = 30  # 最大重连延迟

        while self.running:
            try:
                connect_kwargs = {'ping_timeout': 10, 'ping_interval': 20}
                self._log(f"[UserStream] 尝试连接：{self.ws_url} (proxy={self.proxy})")
                async with websockets.connect(self.ws_url, **connect_kwargs) as ws:
                    self.ws = ws
                    self.connected = True
                    self._log("✓ 用户数据流已连接")
                    reconnect_delay = 3  # 连接成功后重置延迟

                    _msg_count = 0
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                            _msg_count += 1
                            if _msg_count <= 3:
                                self._log(f"[UserStream] 收到第{_msg_count}条消息：{message[:200]}")
                            await self.process_message(message)
                        except asyncio.TimeoutError:
                            self._log(f"[UserStream] 30秒无消息（已收{_msg_count}条）")
                            continue
                        except websockets.exceptions.ConnectionClosed:
                            self._log("用户数据流连接关闭")
                            break
                        except OSError as e:
                            # Windows 网络错误（如 WinError 64）
                            self._log(f"用户数据流网络错误：{e}")
                            break

            except OSError as e:
                # Windows 网络错误（如 WinError 64 指定的网络名不再可用）
                self.connected = False
                self._log(f"用户数据流连接失败：{e}")
                self._log(f"  {reconnect_delay}秒后重试...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

            except websockets.exceptions.InvalidStatusCode:
                self.connected = False
                self._log("用户数据流连接失败：无效的 Listen Key，可能需要更新")
                await asyncio.sleep(10)

            except Exception as e:
                self.connected = False
                self._log(f"用户数据流错误：{e}")
                self._log(f"  {reconnect_delay}秒后重试...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

    _event_count = {}

    async def process_message(self, message: str):
        """处理 WebSocket 消息"""
        import json
        import asyncio

        data = json.loads(message)
        event_type = data.get('e')

        # 统计各类型事件
        if event_type:
            self._event_count[event_type] = self._event_count.get(event_type, 0) + 1
            if self._event_count[event_type] <= 3:
                self._log(f"[UserStream] 事件类型={event_type} (第{self._event_count[event_type]}次)")

        if event_type == 'ORDER_TRADE_UPDATE':
            order_data = data.get('o', {})
            for callback in self.order_callbacks:
                try:
                    result = callback(order_data)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    self._log(f"[订单回调错误] {e}")

        elif event_type == 'ACCOUNT_UPDATE':
            account_data = data.get('a', {})
            for callback in self.account_callbacks:
                try:
                    result = callback(account_data)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    self._log(f"[账户回调错误] {e}")

    async def close(self):
        """关闭连接"""
        self.running = False

        # 取消保活任务
        if self._keep_alive_task and not self._keep_alive_task.done():
            self._keep_alive_task.cancel()
            try:
                await self._keep_alive_task
            except asyncio.CancelledError:
                pass

        if self.ws:
            await self.ws.close()
