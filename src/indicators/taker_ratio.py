# -*- coding: utf-8 -*-
"""
v1.10.0 主动成交比率 (Taker Buy/Sell Ratio)
基于 @aggTrade WebSocket 流，统计近 5 分钟 taker buy/sell 成交量占比
"""

import time
from collections import deque
from decimal import Decimal


class TakerRatio:
    """主动成交比率分析器"""

    def __init__(self, window_seconds: int = 300):
        """
        Args:
            window_seconds: 滚动窗口秒数，默认 300（5 分钟）
        """
        self.window_seconds = window_seconds
        self._trades: deque = deque()  # [(ts_ms, is_taker_buy, qty), ...]
        self._buy_volume: float = 0.0
        self._sell_volume: float = 0.0
        self._trade_count: int = 0
        self._buy_count: int = 0
        self._sell_count: int = 0
        self._last_update: float = 0.0

    def add_trade(self, trade: dict):
        """
        添加一笔 aggTrade
        trade: {'price': Decimal, 'qty': Decimal, 'm': bool, 'ts': int}
        """
        ts_ms = int(trade.get('ts', 0) or int(time.time() * 1000))
        m = trade.get('m', True)
        qty = float(trade.get('qty', 0))

        if qty <= 0 or ts_ms <= 0:
            return

        # m=true → 买方是 maker → 卖方 taker → taker sell
        # m=false → 买方是 taker → taker buy
        is_taker_buy = not m

        self._trades.append((ts_ms, is_taker_buy, qty))
        if is_taker_buy:
            self._buy_volume += qty
            self._buy_count += 1
        else:
            self._sell_volume += qty
            self._sell_count += 1
        self._trade_count += 1
        self._last_update = time.time()

        # 清理过期数据
        self._prune()

    def _prune(self):
        """清理超出窗口的旧数据，并同步更新计数"""
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - self.window_seconds * 1000
        while self._trades and self._trades[0][0] < cutoff:
            _, is_taker_buy, qty = self._trades.popleft()
            if is_taker_buy:
                self._buy_volume -= qty
                self._buy_count = max(0, self._buy_count - 1)
            else:
                self._sell_volume -= qty
                self._sell_count = max(0, self._sell_count - 1)
            self._trade_count = max(0, self._trade_count - 1)

    def get_ratio(self) -> dict:
        """
        获取当前主动成交比率
        返回: {'buy_pct': float, 'sell_pct': float, 'trade_count': int, 'last_update': float}
        """
        self._prune()
        total = self._buy_volume + self._sell_volume
        if total <= 0:
            return {'buy_pct': 50.0, 'sell_pct': 50.0, 'trade_count': self._trade_count, 'last_update': self._last_update}
        buy_pct = (self._buy_volume / total) * 100
        sell_pct = (self._sell_volume / total) * 100
        return {
            'buy_pct': round(buy_pct, 2),
            'sell_pct': round(sell_pct, 2),
            'trade_count': self._trade_count,
            'last_update': self._last_update
        }
