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

from decimal import Decimal
from typing import List, Dict, Optional
from collections import deque


# 阈值配置（来自文档）
THRESHOLDS = {
    '1min_amplitude': {
        'low': 0.03,      # < 0.03% 低波动
        'normal_min': 0.05,  # 0.05% - 0.15% 正常
        'normal_max': 0.15,
        'high': 0.3       # > 0.3% 高波动
    },
    '1h_avg_amplitude': {
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
    
    def __init__(self, max_klines: int = 200):
        """
        初始化波动率分析器
        
        Args:
            max_klines: 最多保留的 K 线数量（默认 200 根，覆盖 3 小时+）
        """
        self.klines = deque(maxlen=max_klines)  # 存储 K 线数据
        self.current_kline: Optional[Dict] = None  # 当前未完成的 K 线
    
    def add_kline(self, kline: dict):
        """
        添加 K 线数据
        
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
        """获取当前 1 分钟振幅（实时）"""
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
    
    def get_1h_avg_amplitude(self) -> float:
        """
        获取 1 小时平均振幅（最近 60 根 K 线平均振幅）
        """
        if len(self.klines) < 60:
            # 如果 K 线不足 60 根，用现有的计算
            if len(self.klines) == 0:
                return 0.0
            klines_to_use = list(self.klines)
        else:
            klines_to_use = list(self.klines)[-60:]
        
        amplitudes = [self.calculate_amplitude(k) for k in klines_to_use]
        avg_amplitude = sum(amplitudes) / len(amplitudes)
        return avg_amplitude
    
    def get_amplitude_change_rate(self) -> float:
        """
        获取振幅变化率：当前 1 分钟振幅 / 1 小时平均振幅
        
        每秒更新，反映波动率的加速/减速
        """
        current_amp = self.get_1min_amplitude()
        avg_amp = self.get_1h_avg_amplitude()
        
        if avg_amp == 0:
            return 1.0  # 默认正常
        
        change_rate = current_amp / avg_amp
        return change_rate
    
    def get_atr(self, period: int = 14) -> Optional[float]:
        """
        计算 ATR(14)：14 周期平均真实波幅
        
        Args:
            period: ATR 周期，默认 14
        
        Returns:
            ATR 值，如果 K 线不足返回 None
        """
        kline_list = list(self.klines)
        if len(kline_list) < period + 1:
            return None
        
        tr_values = []
        for i in range(1, len(kline_list)):
            high = float(kline_list[i]['high'])
            low = float(kline_list[i]['low'])
            prev_close = float(kline_list[i-1]['close'])
            
            # 真实波幅 TR = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            tr = max(tr1, tr2, tr3)
            tr_values.append(tr)
        
        # 取最近 period 个 TR 值的平均
        if len(tr_values) < period:
            return None
        
        atr = sum(tr_values[-period:]) / period
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
                '1h_avg_amplitude': float,
                'change_rate': float,
                'atr_14': Optional[float],
                '1min_status': str,
                '1h_status': str,
                'change_rate_status': str
            }
        """
        amp_1min = self.get_1min_amplitude()
        amp_1h = self.get_1h_avg_amplitude()
        change_rate = self.get_amplitude_change_rate()
        atr = self.get_atr(14)
        
        return {
            '1min_amplitude': amp_1min,
            '5min_amplitude': self.get_5min_amplitude(),
            '1h_avg_amplitude': amp_1h,
            'change_rate': change_rate,
            'atr_14': atr,
            '1min_status': self.get_status_label(amp_1min, '1min_amplitude'),
            '1h_status': self.get_status_label(amp_1h, '1h_avg_amplitude'),
            'change_rate_status': self.get_change_rate_status(change_rate)
        }
    
    def get_change_rate_status(self, change_rate: float) -> str:
        """获取振幅变化率状态"""
        thresholds = THRESHOLDS['amplitude_change_rate']
        
        if change_rate < thresholds['ideal_min']:
            return '减速 🟡'
        elif change_rate > thresholds['ideal_max']:
            return '加速 🟡'
        else:
            return '稳定'
