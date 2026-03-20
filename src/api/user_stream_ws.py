# -*- coding: utf-8 -*-
"""
币安用户数据流 WebSocket
监听订单状态更新和账户变更
"""

import asyncio
import websockets
from typing import Callable, Optional


class UserStreamWebSocket:
    """用户数据流 WebSocket 客户端"""
    
    def __init__(self, listen_key: str, base_url: str = "wss://fstream.binance.com"):
        self.listen_key = listen_key
        self.ws_url = f"{base_url}/ws/{listen_key}"
        self.running = False
        self.connected = False
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        
        # 回调函数
        self.order_callbacks: list[Callable] = []
        self.account_callbacks: list[Callable] = []
    
    def add_order_callback(self, callback: Callable):
        """添加订单更新回调"""
        self.order_callbacks.append(callback)
    
    def add_account_callback(self, callback: Callable):
        """添加账户更新回调"""
        self.account_callbacks.append(callback)
    
    async def connect(self):
        """连接 WebSocket"""
        self.running = True
        
        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    self.connected = True
                    print(f"✓ 用户数据流已连接")
                    
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                            await self.process_message(message)
                        except asyncio.TimeoutError:
                            continue
            
            except websockets.exceptions.ConnectionClosed:
                self.connected = False
                print("用户数据流断开，重连中...")
                await asyncio.sleep(3)
            
            except Exception as e:
                self.connected = False
                print(f"用户数据流错误：{e}")
                await asyncio.sleep(3)
    
    async def process_message(self, message: str):
        """处理 WebSocket 消息"""
        import json
        import asyncio
        
        data = json.loads(message)
        event_type = data.get('e')
        
        if event_type == 'ORDER_TRADE_UPDATE':
            order_data = data.get('o', {})
            for callback in self.order_callbacks:
                try:
                    result = callback(order_data)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    print(f"[订单回调错误] {e}")
        
        elif event_type == 'ACCOUNT_UPDATE':
            account_data = data.get('a', {})
            for callback in self.account_callbacks:
                try:
                    result = callback(account_data)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    print(f"[账户回调错误] {e}")
    
    async def close(self):
        """关闭连接"""
        self.running = False
        if self.ws:
            await self.ws.close()
