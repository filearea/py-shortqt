# -*- coding: utf-8 -*-
"""
指标管理器 - 统一管理所有盘面技术指标

整合波动率、流动性、评分模块，提供统一的数据接口。
"""

from decimal import Decimal
from typing import Dict, List, Tuple, Optional

from .volatility import VolatilityAnalyzer
from .liquidity import LiquidityAnalyzer
from .scorer import QualityScorer


class IndicatorsManager:
    """指标管理器"""
    
    def __init__(self):
        """初始化指标管理器"""
        self.volatility = VolatilityAnalyzer(max_klines=200)
        self.liquidity = LiquidityAnalyzer()
        self.scorer = QualityScorer()
        
        # 缓存最新指标
        self._last_snapshot: Optional[Dict] = None
    
    def update_kline(self, kline: dict):
        """
        更新 K 线数据
        
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
        self.volatility.add_kline(kline)
        # K 线更新后重新计算评分
        self._update_snapshot()
    
    def update_orderbook(self, bids: List[Tuple[Decimal, Decimal]], 
                         asks: List[Tuple[Decimal, Decimal]]):
        """
        更新订单簿数据
        
        Args:
            bids: 买单列表 [(价格，数量), ...]
            asks: 卖单列表 [(价格，数量), ...]
        """
        self.liquidity.update_orderbook(bids, asks)
        # 订单簿更新后重新计算评分
        self._update_snapshot()
    
    def _update_snapshot(self):
        """更新指标快照缓存"""
        vol_metrics = self.volatility.get_metrics()
        liq_metrics = self.liquidity.get_metrics()
        score_result = self.scorer.calculate(vol_metrics, liq_metrics)
        
        self._last_snapshot = {
            'volatility': vol_metrics,
            'liquidity': liq_metrics,
            'score': score_result
        }
    
    def get_snapshot(self) -> dict:
        """
        获取完整的指标快照
        
        Returns:
            指标字典 {
                'volatility': dict,
                'liquidity': dict,
                'score': dict
            }
        """
        if self._last_snapshot is None:
            self._update_snapshot()
        
        return self._last_snapshot
    
    def get_quality_score(self) -> int:
        """获取当前质量评分"""
        snapshot = self.get_snapshot()
        return snapshot['score']['quality_score']
    
    def get_recommendation(self) -> str:
        """获取当前交易建议"""
        snapshot = self.get_snapshot()
        return snapshot['score']['recommendation']
    
    def get_signal_emoji(self) -> str:
        """获取信号灯 emoji"""
        snapshot = self.get_snapshot()
        return snapshot['score']['signal_emoji']
    
    def get_signal_color(self) -> str:
        """获取信号灯颜色"""
        snapshot = self.get_snapshot()
        return snapshot['score']['signal_color']
    
    def check_alerts(self) -> list:
        """
        检查阈值告警
        
        Returns:
            告警列表 [{'type': str, 'message': str, 'level': str}, ...]
        """
        alerts = []
        vol = self.volatility.get_metrics()
        liq = self.liquidity.get_metrics()
        
        # 波动率告警
        if '🟡' in vol['1min_status']:
            alerts.append({
                'type': 'volatility_low',
                'message': f"1 分钟振幅偏低：{vol['1min_amplitude']:.3f}%",
                'level': 'warning'
            })
        elif '🔴' in vol['1min_status']:
            alerts.append({
                'type': 'volatility_high',
                'message': f"1 分钟振幅过高：{vol['1min_amplitude']:.3f}%",
                'level': 'critical'
            })
        
        # 价差率告警
        if '🔴' in liq['spread_status']:
            alerts.append({
                'type': 'spread_high',
                'message': f"价差率过高：{liq['spread_rate']:.4f}%",
                'level': 'critical'
            })
        
        # 订单簿深度告警
        if '🔴' in liq['depth_status']:
            alerts.append({
                'type': 'depth_low',
                'message': f"订单簿深度不足：{liq['orderbook_depth']:.0f} ETH",
                'level': 'critical'
            })
        
        return alerts
    
    def get_display_data(self) -> dict:
        """
        获取 TUI 展示数据（格式化后的指标）
        
        Returns:
            展示数据字典
        """
        snapshot = self.get_snapshot()
        vol = snapshot['volatility']
        liq = snapshot['liquidity']
        score = snapshot['score']
        
        return {
            'volatility_lines': [
                f"1 分钟：{vol['1min_amplitude']:.3f}% {vol['1min_status']}",
                f"5 分钟：{vol['5min_amplitude']:.3f}%",
                f"1 小时：{vol['1h_avg_amplitude']:.3f}% {vol['1h_status']}",
                f"变化率：{vol['change_rate']:.2f} {vol['change_rate_status']}",
                f"ATR(14): {vol['atr_14']:.4f}" if vol['atr_14'] else "ATR(14): --"
            ],
            'liquidity_lines': [
                f"价差：{float(liq['spread']):.2f} USDC",
                f"价差率：{liq['spread_rate']:.4f}% {liq['spread_status']}",
                f"深度：{liq['orderbook_depth']:.0f} ETH {liq['depth_status']}"
            ],
            'score_display': {
                'emoji': score['signal_emoji'],
                'color': score['signal_color'],
                'recommendation': score['recommendation'],
                'score': score['quality_score']
            },
            'alerts': self.check_alerts()
        }
