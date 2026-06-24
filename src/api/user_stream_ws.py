# -*- coding: utf-8 -*-
"""
币安用户数据流 WebSocket
监听订单状态更新和账户变更

v1.9.0 修复：
- 应用层心跳检测：60s 无消息强制重连
- Listen Key 自动轮换：重连时获取新 key + 清理旧 key
- 消息时间戳追踪
"""

import asyncio
import json
import time
import websockets
from typing import Callable, Optional


class UserStreamWebSocket:
    """用户数据流 WebSocket 客户端"""

    LISTEN_KEY_EXPIRE_SECONDS = 3600  # listen key 有效期 60 分钟
    KEEP_ALIVE_INTERVAL = 1800  # 保活间隔 30 分钟
    ZOMBIE_TIMEOUT = 60  # v1.9.0：60 秒无消息视为僵尸连接

    def __init__(self, listen_key: str, api_client=None, testnet: bool = False, log_func=None):
        self.listen_key = listen_key
        self.api_client = api_client  # 用于 keep-alive 和 listen key 管理
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

        # v1.9.0：健康监控
        self._last_msg_ts: float = 0.0
        self._msg_count: int = 0
        self._restart_count: int = 0
        self._health_check_task: Optional[asyncio.Task] = None

    @property
    def last_msg_ts(self) -> float:
        """上次收到消息的时间戳"""
        return self._last_msg_ts

    @property
    def msg_count(self) -> int:
        """累计消息数"""
        return self._msg_count

    @property
    def restart_count(self) -> int:
        """重连次数"""
        return self._restart_count

    @property
    def seconds_since_last_msg(self) -> float:
        """距上次消息的秒数"""
        if self._last_msg_ts == 0:
            return float('inf')
        return time.time() - self._last_msg_ts

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
        """连接 WebSocket（v1.9.0：带健康检测 + listen key 轮换）"""
        self.running = True

        # 启动保活任务
        if self._keep_alive_task is None or self._keep_alive_task.done():
            self._keep_alive_task = asyncio.create_task(self._keep_alive_loop())

        # 启动健康检测任务
        if self._health_check_task is None or self._health_check_task.done():
            self._health_check_task = asyncio.create_task(self._health_check_loop())

        reconnect_delay = 3  # 初始重连延迟（秒）
        max_reconnect_delay = 30  # 最大重连延迟

        while self.running:
            try:
                # 重连时刷新 listen key（v1.9.0）
                if self._restart_count > 0 or self._last_msg_ts > 0:
                    await self._refresh_listen_key()

                self.ws_url = self._build_ws_url()
                connect_kwargs = {
                    'close_timeout': 5,
                    'open_timeout': 10,
                }
                if self.proxy:
                    connect_kwargs['proxy'] = self.proxy
                    self._log(f"[UserStream] 通过代理连接：{self.ws_url}")
                async with websockets.connect(self.ws_url, **connect_kwargs) as ws:
                    self.ws = ws
                    self.connected = True
                    self._last_msg_ts = time.time()
                    self._log("✓ 用户数据流已连接")
                    reconnect_delay = 3  # 连接成功后重置延迟

                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                            self._last_msg_ts = time.time()
                            self._msg_count += 1
                            if self._msg_count <= 3:
                                self._log(f"[UserStream] 收到第{self._msg_count}条消息：{message[:200]}")
                            await self.process_message(message)
                        except asyncio.TimeoutError:
                            age = time.time() - self._last_msg_ts
                            self._log(f"[UserStream] 30秒无消息（距上次 {age:.0f}s，已收{self._msg_count}条）")
                            if age >= self.ZOMBIE_TIMEOUT:
                                self._log("[UserStream] 疑似僵尸连接，强制断开重连")
                                break
                            continue
                        except websockets.exceptions.ConnectionClosed:
                            self._log("用户数据流连接关闭")
                            break
                        except OSError as e:
                            self._log(f"用户数据流网络错误：{e}")
                            break

            except OSError as e:
                self.connected = False
                self._log(f"用户数据流连接失败：{e}")
                self._log(f"  {reconnect_delay}秒后重试...")
                self._restart_count += 1
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

            except websockets.exceptions.InvalidStatusCode:
                self.connected = False
                self._log("用户数据流连接失败：无效的 Listen Key，将刷新后重连")
                self._restart_count += 1
                await self._refresh_listen_key()
                await asyncio.sleep(3)

            except Exception as e:
                self.connected = False
                self._log(f"用户数据流错误：{e}")
                self._log(f"  {reconnect_delay}秒后重试...")
                self._restart_count += 1
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

    def _build_ws_url(self) -> str:
        """构建 WebSocket URL"""
        if self.api_client and getattr(self.api_client, 'testnet', False):
            base = "wss://stream.binancefuture.com"
        else:
            base = "wss://fstream.binance.com"
        return f"{base}/ws/{self.listen_key}"

    async def _refresh_listen_key(self):
        """刷新 listen key：获取新 key + 关闭旧 key"""
        if not self.api_client:
            self._log("[UserStream] 无 api_client，无法刷新 listenKey")
            return
        try:
            old_key = self.listen_key
            new_key = await asyncio.to_thread(self.api_client.get_listen_key)
            self.listen_key = new_key
            self._log(f"[UserStream] listenKey 已刷新")
            # 异步关闭旧 key（不阻塞）
            if old_key:
                try:
                    await asyncio.to_thread(self.api_client.close_listen_key, old_key)
                except Exception:
                    pass
        except Exception as e:
            self._log(f"[UserStream] listenKey 刷新失败：{e}")

    async def _health_check_loop(self):
        """健康检测循环：每 15 秒检查一次"""
        while self.running:
            await asyncio.sleep(15)
            if not self.running:
                break
            if self.connected and self._last_msg_ts > 0:
                age = time.time() - self._last_msg_ts
                if age > self.ZOMBIE_TIMEOUT:
                    self._log(f"[UserStream] 健康检测失败：{age:.0f}s 无消息，触发重连")
                    if self.ws:
                        try:
                            await self.ws.close()
                        except Exception:
                            pass
                    self.connected = False

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

        # 取消健康检测任务
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # 清理 listen key
        if self.api_client and self.listen_key:
            try:
                await asyncio.to_thread(self.api_client.close_listen_key, self.listen_key)
                self._log("[UserStream] listenKey 已清理")
            except Exception:
                pass

        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
