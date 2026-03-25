# -*- coding: utf-8 -*-
"""
流动性指标分析模块

基于订单簿数据计算流动性相关指标：
- 买卖价差
- 价差率
- 订单簿深度
"""

from decimal import Decimal
from typing import List, Tuple, Optional


# 阈值配置
THRESHOLDS = {
    'spread_rate': {
        'normal_max': 0.01,  # < 0.01% 正常
        'warning': 0.02,     # > 0.02% 警告 🔴
    },
    'orderbook_depth': {
        'ideal_min': 2000,   # > 2000 ETH 充足
        'warning': 500,      # < 500 ETH 警告 🔴
    }
}


class LiquidityAnalyzer:
    """流动性分析器"""
    
    def __init__(self):
        """初始化流动性分析器"""
        self.bids: List[Tuple[Decimal, Decimal]] = []  # 买单 [(价格，数量), ...]
        self.asks: List[Tuple[Decimal, Decimal]] = []  # 卖单 [(价格，数量), ...]
    
    def update_orderbook(self, bids: List[Tuple[Decimal, Decimal]], 
                         asks: List[Tuple[Decimal, Decimal]]):
        """
        更新订单簿数据
        
        Args:
            bids: 买单列表 [(价格，数量), ...]，按价格降序排列
            asks: 卖单列表 [(价格，数量), ...]，按价格升序排列
        """
        self.bids = bids
        self.asks = asks
    
    def get_spread(self) -> Optional[Decimal]:
        """
        获取买卖价差：ask[0] - bid[0]
        
        Returns:
            价差（USDC），如果订单簿为空返回 None
        """
        if not self.bids or not self.asks:
            return None
        
        best_bid = self.bids[0][0]
        best_ask = self.asks[0][0]
        
        spread = best_ask - best_bid
        return spread
    
    def get_mid_price(self) -> Optional[Decimal]:
        """
        获取中间价：(best_ask + best_bid) / 2
        
        Returns:
            中间价，如果订单簿为空返回 None
        """
        if not self.bids or not self.asks:
            return None
        
        best_bid = self.bids[0][0]
        best_ask = self.asks[0][0]
        
        mid_price = (best_ask + best_bid) / 2
        return mid_price
    
    def get_spread_rate(self) -> float:
        """
        获取价差率：价差 / 中间价 × 100%
        
        Returns:
            价差率百分比
        """
        spread = self.get_spread()
        mid_price = self.get_mid_price()
        
        if not spread or not mid_price or mid_price == 0:
            return 0.0
        
        spread_rate = float(spread / mid_price * 100)
        return spread_rate
    
    def get_orderbook_depth(self, levels: int = 3) -> float:
        """
        获取订单簿深度：前 N 档买单 + 卖单总量
        
        Args:
            levels: 计算深度档数，默认 3 档
        
        Returns:
            深度总量（ETH）
        """
        bid_depth = sum(float(qty) for _, qty in self.bids[:levels])
        ask_depth = sum(float(qty) for _, qty in self.asks[:levels])
        
        total_depth = bid_depth + ask_depth
        return total_depth
    
    def get_status_label(self, value: float, threshold_key: str) -> str:
        """
        根据阈值获取状态标签
        
        Args:
            value: 指标值
            threshold_key: 阈值键名（如 'spread_rate'）
        
        Returns:
            状态标签
        """
        if threshold_key not in THRESHOLDS:
            return ''
        
        thresholds = THRESHOLDS[threshold_key]
        
        if threshold_key == 'spread_rate':
            if value <= thresholds['normal_max']:
                return '正常'
            elif value > thresholds['warning']:
                return '过高 🔴'
            else:
                return '偏高 🟡'
        
        elif threshold_key == 'orderbook_depth':
            if value >= thresholds['ideal_min']:
                return '充足'
            elif value < thresholds['warning']:
                return '不足 🔴'
            else:
                return '偏少 🟡'
        
        return ''
    
    def get_metrics(self) -> dict:
        """
        获取完整的流动性指标快照
        
        Returns:
            指标字典 {
                'spread': Decimal,
                'spread_rate': float,
                'orderbook_depth': float,
                'best_bid': Decimal,
                'best_ask': Decimal,
                'mid_price': Decimal,
                'spread_status': str,
                'depth_status': str
            }
        """
        spread = self.get_spread()
        spread_rate = self.get_spread_rate()
        depth = self.get_orderbook_depth(3)
        
        best_bid = self.bids[0][0] if self.bids else Decimal('0')
        best_ask = self.asks[0][0] if self.asks else Decimal('0')
        mid_price = self.get_mid_price() or Decimal('0')
        
        return {
            'spread': spread or Decimal('0'),
            'spread_rate': spread_rate,
            'orderbook_depth': depth,
            'best_bid': best_bid,
            'best_ask': best_ask,
            'mid_price': mid_price,
            'spread_status': self.get_status_label(spread_rate, 'spread_rate'),
            'depth_status': self.get_status_label(depth, 'orderbook_depth')
        }
