# -*- coding: utf-8 -*-
"""
Tick 级价格追踪器

追踪最近 30 秒 bookTicker 价格流，计算：
- 方向反转次数（tick 级来回拉锯频率）
- 最大 tick 振幅（30 秒内最高-最低）
"""

from collections import deque
from typing import Optional


class TickTracker:
    def __init__(self, window_seconds: float = 30.0):
        self.window_seconds = window_seconds
        # [(timestamp: float, price: float), ...]
        self.ticks: deque = deque()
        self._last_price: Optional[float] = None
        self._last_direction: int = 0  # 1=up, -1=down, 0=none
        self._reversal_count: int = 0

    def add_tick(self, timestamp: float, price: float):
        """添加一笔 bookTicker 价格"""
        # 计算方向反转
        if self._last_price is not None and price != self._last_price:
            direction = 1 if price > self._last_price else -1
            if self._last_direction != 0 and direction != self._last_direction:
                self._reversal_count += 1
            self._last_direction = direction
        self._last_price = price

        self.ticks.append((timestamp, price))
        self._cleanup(timestamp)

    def _cleanup(self, now: float):
        """清理过期 tick"""
        cutoff = now - self.window_seconds
        while self.ticks and self.ticks[0][0] < cutoff:
            self.ticks.popleft()

    def get_reversal_count(self) -> int:
        """当前窗口内的方向反转次数"""
        return self._reversal_count

    def get_max_amplitude(self) -> float:
        """当前窗口内的最大 tick 振幅（百分比）"""
        if not self.ticks:
            return 0.0
        prices = [p for _, p in self.ticks]
        highest = max(prices)
        lowest = min(prices)
        if lowest == 0:
            return 0.0
        return (highest - lowest) / lowest * 100

    def get_tick_momentum(self) -> str:
        """
        最近 tick 的净动量方向

        返回: 'UP' / 'DOWN' / 'NONE'
        """
        if not self.ticks:
            return 'NONE'
        # 取最近 10 个 tick 看净方向
        recent = list(self.ticks)[-10:]
        if len(recent) < 3:
            return 'NONE'
        first_price = recent[0][1]
        last_price = recent[-1][1]
        diff = last_price - first_price
        # 需要至少 0.5 点的变化才算有方向
        threshold = 0.5
        if diff > threshold:
            return 'UP'
        elif diff < -threshold:
            return 'DOWN'
        return 'NONE'

    def clear(self):
        """清空所有数据"""
        self.ticks.clear()
        self._last_price = None
        self._last_direction = 0
        self._reversal_count = 0
