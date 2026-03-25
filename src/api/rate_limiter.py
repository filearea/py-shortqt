# -*- coding: utf-8 -*-
"""
API 请求速率限制器

管理币安 Futures API 的请求频率，避免触发频率限制。
币安限制：1200 权重/分钟
"""

import time
import asyncio
from collections import deque
from typing import Optional


class RateLimiter:
    """API 请求速率限制器"""
    
    def __init__(self, weight_limit: int = 1200, window_seconds: int = 60):
        """
        初始化速率限制器
        
        Args:
            weight_limit: 权重限制（币安：1200/分钟）
            window_seconds: 时间窗口（秒）
        """
        self.weight_limit = weight_limit
        self.window_seconds = window_seconds
        
        # 请求队列：(timestamp, weight)
        self.requests = deque()
        
        # 当前权重
        self.current_weight = 0
        
        # 锁（用于多线程安全）
        self._lock = asyncio.Lock()
    
    async def acquire(self, weight: int = 1, timeout: Optional[float] = None) -> bool:
        """
        获取请求权限
        
        Args:
            weight: 本次请求的权重
            timeout: 超时时间（秒），None 表示无限等待
        
        Returns:
            True: 成功获取权限
            False: 超时
        """
        async with self._lock:
            start_time = time.time()
            
            while True:
                # 清理过期请求
                self._cleanup_expired()
                
                # 检查是否有足够权重
                if self.current_weight + weight <= self.weight_limit:
                    # 记录请求
                    self.requests.append((time.time(), weight))
                    self.current_weight += weight
                    return True
                
                # 计算需要等待的时间
                if self.requests:
                    oldest_time = self.requests[0][0]
                    wait_time = oldest_time + self.window_seconds - time.time()
                    
                    if wait_time > 0:
                        # 需要等待
                        if timeout is not None:
                            elapsed = time.time() - start_time
                            remaining_timeout = timeout - elapsed
                            if remaining_timeout <= 0:
                                return False
                            wait_time = min(wait_time, remaining_timeout)
                        
                        # 释放锁，等待
                        self._lock.release()
                        try:
                            await asyncio.sleep(wait_time)
                        finally:
                            await self._lock.acquire()
                    else:
                        # 已经过期，继续循环
                        continue
                else:
                    # 队列为空，可以直接通过
                    self.requests.append((time.time(), weight))
                    self.current_weight += weight
                    return True
    
    def _cleanup_expired(self):
        """清理过期的请求记录"""
        current_time = time.time()
        cutoff_time = current_time - self.window_seconds
        
        while self.requests and self.requests[0][0] < cutoff_time:
            _, weight = self.requests.popleft()
            self.current_weight -= weight
        
        # 确保不会出现负数
        if self.current_weight < 0:
            self.current_weight = 0
    
    def get_current_weight(self) -> int:
        """获取当前已使用的权重"""
        self._cleanup_expired()
        return self.current_weight
    
    def get_available_weight(self) -> int:
        """获取剩余可用权重"""
        return self.weight_limit - self.get_current_weight()
    
    def get_wait_time(self, weight: int = 1) -> float:
        """
        获取需要等待的时间
        
        Args:
            weight: 请求权重
        
        Returns:
            需要等待的秒数（0 表示无需等待）
        """
        self._cleanup_expired()
        
        if self.current_weight + weight <= self.weight_limit:
            return 0.0
        
        if self.requests:
            oldest_time = self.requests[0][0]
            wait_time = oldest_time + self.window_seconds - time.time()
            return max(0.0, wait_time)
        
        return 0.0
    
    def reset(self):
        """重置限制器（测试用）"""
        self.requests.clear()
        self.current_weight = 0


# 币安 Futures API 权重配置
BINANCE_WEIGHTS = {
    # 公开接口
    'GET /fapi/v1/klines': 1,
    'GET /fapi/v1/ticker/price': 2,
    'GET /fapi/v1/depth': 5,
    'GET /fapi/v1/time': 1,
    
    # 账户接口
    'GET /fapi/v2/account': 5,
    'GET /fapi/v2/positionRisk': 5,
    'GET /fapi/v1/openOrders': 5,
    'GET /fapi/v1/allOrders': 5,
    
    # 交易接口
    'POST /fapi/v1/order': 1,
    'DELETE /fapi/v1/order': 1,
    'DELETE /fapi/v1/allOpenOrders': 1,
    
    # 批量接口
    'POST /fapi/v1/batchOrders': 5,
    'DELETE /fapi/v1/batchOrders': 5,
    
    # Algo Order
    'POST /fapi/v1/algoOrder': 1,
    'DELETE /fapi/v1/algoOrder': 1,
    'GET /fapi/v1/algoOrder/openOrders': 5,
    
    # 用户数据流
    'POST /fapi/v1/listenKey': 1,
    'PUT /fapi/v1/listenKey': 1,
    'DELETE /fapi/v1/listenKey': 1,
}
