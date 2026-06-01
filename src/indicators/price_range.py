# -*- coding: utf-8 -*-
"""
价格范围追踪器

追踪最近 X 分钟内 tick 价格的最高/最低价。
"""

from collections import deque
from typing import Optional


class PriceRangeTracker:
    def __init__(self, window_minutes: float = 30.0):
        self.window_seconds = window_minutes * 60.0
        # [(timestamp: float, price: float), ...]
        self.ticks: deque = deque()
        self._high: Optional[float] = None
        self._low: Optional[float] = None

    def set_window(self, minutes: float):
        """动态调整窗口大小"""
        self.window_seconds = minutes * 60.0
        self._cleanup(self._latest_ts() or 0)
        self._recalc()

    def add_tick(self, timestamp: float, price: float):
        self.ticks.append((timestamp, price))
        self._cleanup(timestamp)
        self._recalc()

    def _cleanup(self, now: float):
        cutoff = now - self.window_seconds
        while self.ticks and self.ticks[0][0] < cutoff:
            self.ticks.popleft()
        if not self.ticks:
            self._high = None
            self._low = None

    def _latest_ts(self) -> Optional[float]:
        return self.ticks[-1][0] if self.ticks else None

    def _recalc(self):
        if not self.ticks:
            self._high = None
            self._low = None
            return
        prices = [p for _, p in self.ticks]
        self._high = max(prices)
        self._low = min(prices)

    def get_high(self) -> Optional[float]:
        return self._high

    def get_low(self) -> Optional[float]:
        return self._low

    def seed_from_klines(self, klines: list):
        """
        从历史 K 线列表填充 tick 数据
        klines: [(timestamp_sec: float, high: float, low: float), ...]
        """
        for ts_sec, high, low in klines:
            self.ticks.append((ts_sec, high))
            self.ticks.append((ts_sec, low))
        self._recalc()

    def clear(self):
        self.ticks.clear()
        self._high = None
        self._low = None
