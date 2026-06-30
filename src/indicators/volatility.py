# -*- coding: utf-8 -*-
"""
波动率指标分析模块

基于 K 线数据计算波动率相关指标：
- 1 分钟振幅（短期波动强度）
- 5 分钟振幅（中期波动趋势）
- 1 小时平均振幅（波动率稳定性）
- 振幅变化率（波动率加速/减速）
- ATR(14)（标准化波动率）
"""

import time
from decimal import Decimal
from typing import List, Dict, Optional
from collections import deque


# 阈值配置（与 calculate_amplitude 返回值一致，ETHUSDC 1min 振幅约 0.03%-0.1%）
THRESHOLDS = {
    '1min_amplitude': {
        'low': 0.03,      # < 0.03% 低波动
        'normal_min': 0.05,  # 0.05% - 0.15% 正常
        'normal_max': 0.15,
        'high': 0.3       # > 0.3% 高波动
    },
    '1h_amplitude': {
        'ideal_min': 0.08,  # 理想区间 0.08% - 0.15%
        'ideal_max': 0.15
    },
    'amplitude_change_rate': {
        'ideal_min': 0.8,   # 理想区间 0.8 - 1.2
        'ideal_max': 1.2
    }
}


class VolatilityAnalyzer:
    """波动率分析器"""
    
    def __init__(self, max_klines: int = 1440):
        """
        初始化波动率分析器

        Args:
            max_klines: 最多保留的 K 线数量（默认 1440 根，覆盖 24 小时）
        """
        self._klines = deque(maxlen=max_klines)  # 存储已收盘 K 线数据（公开给 web server）
        self.klines = self._klines  # 兼容旧引用
        self.current_kline: Optional[Dict] = None  # 当前未完成的 K 线
        self._atr_cache: Optional[float] = None
        self._atr_cached_at_ts: int = 0  # 上次计算 ATR 时的 current_kline 时间戳

        # v1.10.0: ATR14 24h 百分位
        self._atr14_percentile: int = 0
        self._atr14_ref: str = 'normal'
        self._atr14_percentile_history: deque = deque(maxlen=1440)  # 24h ATR14% 历史值
        self._atr14_last_recorded_ts: int = 0   # 上次记录 ATR14% 的分钟时间戳（去重）
        self._atr14_last_recompute: float = 0   # 上次重算百分位的时间戳
    
    def add_kline(self, kline: dict):
        """
        添加 K 线数据（权威来源 — REST API 拉取）

        Args:
            kline: K 线字典 {
                'timestamp': int,
                'open': Decimal,
                'high': Decimal,
                'low': Decimal,
                'close': Decimal,
                'volume': Decimal
            }
        """
        # 如果是新 K 线（时间戳不同），将上一个 K 线加入历史
        if self.current_kline and kline['timestamp'] != self.current_kline['timestamp']:
            self.klines.append(self.current_kline)

        self.current_kline = kline

    def set_current_kline(self, kline: dict):
        """
        仅更新当前 K 线跟踪（非权威来源 — WS @trade 合成），不写入历史队列。
        ATR/指标计算只依赖 REST API 拉取的权威 K 线。
        """
        self.current_kline = kline
    
    @staticmethod
    def calculate_amplitude(kline: dict) -> float:
        """
        计算单根 K 线的振幅：(High - Low) / Open × 100%
        
        Args:
            kline: K 线字典
        
        Returns:
            振幅百分比
        """
        open_price = float(kline['open'])
        if open_price == 0:
            return 0.0
        
        high_price = float(kline['high'])
        low_price = float(kline['low'])
        
        amplitude = (high_price - low_price) / open_price * 100
        return amplitude
    
    def get_1min_amplitude(self) -> float:
        """获取最近一根已收盘 1 分钟 K 线的振幅"""
        if len(self.klines) > 0:
            return self.calculate_amplitude(self.klines[-1])
        return 0.0

    def get_current_amplitude(self) -> float:
        """获取当前进行中 K 线的振幅（tick 级变化）"""
        if self.current_kline:
            return self.calculate_amplitude(self.current_kline)
        return 0.0
    
    def get_5min_amplitude(self) -> float:
        """
        获取 5 分钟振幅（最近 5 根 K 线综合振幅）
        
        计算方式：(近 5 根最高价 - 近 5 根最低价) / 5 根前开盘价 × 100%
        """
        if len(self.klines) < 5:
            return 0.0
        
        recent_klines = list(self.klines)[-5:]
        highest = max(float(k['high']) for k in recent_klines)
        lowest = min(float(k['low']) for k in recent_klines)
        open_price = float(recent_klines[0]['open'])
        
        if open_price == 0:
            return 0.0
        
        amplitude = (highest - lowest) / open_price * 100
        return amplitude
    
    def get_1h_amplitude(self) -> float:
        """
        获取 1 小时真实振幅：(近 60 根最高 - 近 60 根最低) / 60 根前开盘价 × 100%
        """
        if len(self.klines) < 60:
            if len(self.klines) == 0:
                return 0.0
            klines_to_use = list(self.klines)
        else:
            klines_to_use = list(self.klines)[-60:]

        highest = max(float(k['high']) for k in klines_to_use)
        lowest = min(float(k['low']) for k in klines_to_use)
        open_price = float(klines_to_use[0]['open'])

        if open_price == 0:
            return 0.0

        amplitude = (highest - lowest) / open_price * 100
        return amplitude
    
    def get_1h_atr(self, period: int = 60) -> float:
        """
        获取过去 N 根 K 线的平均振幅（ATR 概念）——每根K线振幅的均值
        """
        if len(self.klines) < period:
            if len(self.klines) == 0:
                return 0.0
            klines_to_use = list(self.klines)
            period = len(klines_to_use)
        else:
            klines_to_use = list(self.klines)[-period:]

        amplitudes = [self.calculate_amplitude(k) for k in klines_to_use]
        return sum(amplitudes) / len(amplitudes)

    def get_amplitude_change_rate(self) -> float:
        """
        获取振幅变化率：上根 1 分钟振幅 / 过去 60 根平均振幅（ATR60）

        反映波动率的加速/减速
        """
        current_amp = self.get_1min_amplitude()
        avg_amp = self.get_1h_atr(60)

        if avg_amp == 0:
            return 1.0  # 默认正常

        change_rate = current_amp / avg_amp
        return change_rate
    
    def get_atr_volatility_percent(self, period: int = 14) -> Optional[float]:
        """
        计算 ATR(14) 波动率百分比：ATR / 当前价格 × 100%
        反映 14 根 K 线的平均波动率相对于当前价格的百分比

        Returns:
            波动率百分比，如果数据不足返回 None
        """
        atr = self.get_atr(period)
        if atr is None:
            return None
        if not self.current_kline:
            return None
        current_price = float(self.current_kline['close'])
        if current_price == 0:
            return None
        return (atr / current_price) * 100

    def get_atr(self, period: int = 14) -> Optional[float]:
        """
        计算 ATR(14)：14 周期平均真实波幅（带缓存，仅在新 K 线收盘时重算）

        Args:
            period: ATR 周期，默认 14

        Returns:
            ATR 值，如果 K 线不足返回 None
        """
        current_ts = self.current_kline['timestamp'] if self.current_kline else 0

        if self._atr_cache is not None and self._atr_cached_at_ts == current_ts:
            return self._atr_cache

        kline_list = list(self.klines)
        if len(kline_list) < period + 1:
            self._atr_cache = None
            self._atr_cached_at_ts = current_ts
            return None

        tr_values = []
        for i in range(1, len(kline_list)):
            high = float(kline_list[i]['high'])
            low = float(kline_list[i]['low'])
            prev_close = float(kline_list[i-1]['close'])

            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            tr = max(tr1, tr2, tr3)
            tr_values.append(tr)

        if len(tr_values) < period:
            self._atr_cache = None
            self._atr_cached_at_ts = current_ts
            return None

        atr = sum(tr_values[-period:]) / period
        self._atr_cache = atr
        self._atr_cached_at_ts = current_ts
        return atr
    
    def get_status_label(self, amplitude: float, threshold_key: str) -> str:
        """
        根据阈值获取状态标签
        
        Args:
            amplitude: 振幅值
            threshold_key: 阈值键名（如 '1min_amplitude'）
        
        Returns:
            状态标签：'偏低' / '正常' / '偏高'
        """
        if threshold_key not in THRESHOLDS:
            return ''
        
        thresholds = THRESHOLDS[threshold_key]
        
        if 'low' in thresholds and amplitude < thresholds['low']:
            return '偏低 🟡'
        elif 'normal_min' in thresholds and amplitude < thresholds['normal_min']:
            return '偏低'
        elif 'normal_max' in thresholds and amplitude > thresholds['normal_max']:
            return '偏高 🟡'
        elif 'high' in thresholds and amplitude > thresholds['high']:
            return '过高 🔴'
        else:
            return '正常'
    
    def get_metrics(self) -> dict:
        """
        获取完整的波动率指标快照
        
        Returns:
            指标字典 {
                '1min_amplitude': float,
                '5min_amplitude': float,
                '1h_amplitude': float,
                'change_rate': float,
                'atr_14': Optional[float],
                '1min_status': str,
                '1h_status': str,
                'change_rate_status': str
            }
        """
        amp_1min = self.get_1min_amplitude()
        amp_1h = self.get_1h_amplitude()
        change_rate = self.get_amplitude_change_rate()
        atr = self.get_atr(14)
        
        return {
            '1min_amplitude': amp_1min,
            '5min_amplitude': self.get_5min_amplitude(),
            '1h_amplitude': amp_1h,
            'change_rate': change_rate,
            'atr_14': atr,
            'atr_volatility_percent': self.get_atr_volatility_percent(14),
            'atr14_percentile': self._atr14_percentile,
            'atr14_ref': self._atr14_ref,
            '1min_status': self.get_status_label(amp_1min, '1min_amplitude'),
            '1h_status': self.get_status_label(amp_1h, '1h_amplitude'),
            'change_rate_status': self.get_change_rate_status(change_rate)
        }
    
    def get_kline_streak(self, lookback: int = 10) -> tuple[int, str]:
        """
        统计最近 N 根已收盘 K 线的连续性

        Returns:
            (最长连续数, 方向 'UP'/'DOWN'/'NONE')
        """
        if len(self.klines) < 2:
            return 0, 'NONE'

        klines_to_use = list(self.klines)[-lookback:]
        directions = []
        for k in klines_to_use:
            if k['close'] > k['open']:
                directions.append(1)  # 阳线
            elif k['close'] < k['open']:
                directions.append(-1)  # 阴线
            else:
                directions.append(0)  # 十字星，打断连续

        # 统计最长连续
        max_streak = 0
        max_dir = 0
        cur_streak = 0
        cur_dir = 0

        for d in directions:
            if d != 0 and d == cur_dir:
                cur_streak += 1
            elif d != 0:
                cur_dir = d
                cur_streak = 1
            else:
                cur_streak = 0
                cur_dir = 0

            if cur_streak > max_streak:
                max_streak = cur_streak
                max_dir = cur_dir

        direction = 'UP' if max_dir == 1 else ('DOWN' if max_dir == -1 else 'NONE')
        return max_streak, direction

    def get_change_rate_status(self, change_rate: float) -> str:
        """获取振幅变化率状态"""
        thresholds = THRESHOLDS['amplitude_change_rate']

        if change_rate < thresholds['ideal_min']:
            return '减速 🟡'
        elif change_rate > thresholds['ideal_max']:
            return '加速 🟡'
        else:
            return '稳定'

    # ─── v1.10.0 ATR14 便捷方法 ─────────────────────────────────

    def get_atr14(self) -> Optional[float]:
        """获取 ATR(14) 绝对值"""
        return self.get_atr(14)

    def get_atr14_pct(self) -> Optional[float]:
        """获取 ATR(14) 相对当前价百分比"""
        return self.get_atr_volatility_percent(14)

    def track_atr14_percentile(self):
        """
        每分钟记录当前 ATR14% 到 24h 历史队列（按分钟去重）
        每小时重算分布（P50/P75/P95），每分钟更新 P 档
        """
        pct = self.get_atr14_pct()
        if pct is None:
            return

        # 按分钟去重：同一分钟只记录一次
        current_minute = int(time.time()) // 60 * 60
        if current_minute == self._atr14_last_recorded_ts:
            return
        self._atr14_last_recorded_ts = current_minute

        self._atr14_percentile_history.append(pct)

        # 启动时或每小时：全量重算分布 + 分级 + P 档
        now = time.time()
        if self._atr14_last_recompute == 0 or (now - self._atr14_last_recompute) >= 3600:
            self.recompute_atr14_percentile()
        else:
            # 每分钟：仅更新当前值在已有分布中的 P 档（轻量，不重排序）
            self._update_atr14_rank()

    def _update_atr14_rank(self):
        """轻量更新 P 档 + 分级：O(n) 计数，不重新排序，但同步 ref 保证一致性"""
        history = list(self._atr14_percentile_history)
        current_pct = self.get_atr14_pct()
        if not history or current_pct is None:
            return
        n = len(history)
        rank = sum(1 for v in history if v <= current_pct)
        self._atr14_percentile = round(rank / n * 100)
        # 同步更新 ref 分级（与 recompute 规则一致）
        p = self._atr14_percentile
        if p < 50:
            self._atr14_ref = 'low'
        elif p < 75:
            self._atr14_ref = 'normal'
        elif p < 95:
            self._atr14_ref = 'elevated'
        else:
            self._atr14_ref = 'high'

    def recompute_atr14_percentile(self):
        """
        重算当前 ATR14% 在 24h 历史中的百分位和 P50/P75/P95 分级

        分级规则:
          - P0-P50   → low       (低于中位数)
          - P50-P75  → normal    (正常区间)
          - P75-P95  → elevated  (偏高)
          - P95-P100 → high      (极端高)
        """
        history = list(self._atr14_percentile_history)
        current_pct = self.get_atr14_pct()
        now = time.time()

        if not history or current_pct is None:
            self._atr14_percentile = 0
            self._atr14_ref = 'normal'
            self._atr14_last_recompute = now
            return

        sorted_vals = sorted(history)
        n = len(sorted_vals)

        # nearest-rank 百分位
        rank = sum(1 for v in sorted_vals if v <= current_pct)
        self._atr14_percentile = round(rank / n * 100)

        if n >= 60:
            p50 = sorted_vals[max(0, (n * 50) // 100 - 1)]
            p75 = sorted_vals[max(0, (n * 75) // 100 - 1)]
            p95 = sorted_vals[max(0, (n * 95) // 100 - 1)]

            if current_pct < p50:
                self._atr14_ref = 'low'
            elif current_pct < p75:
                self._atr14_ref = 'normal'
            elif current_pct < p95:
                self._atr14_ref = 'elevated'
            else:
                self._atr14_ref = 'high'
        else:
            self._atr14_ref = 'normal'

        self._atr14_last_recompute = now
