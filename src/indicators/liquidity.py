# -*- coding: utf-8 -*-
"""
流动性指标计算 - v1.4.0 (200 档 + 聚合分析)

分层分析订单簿深度：
- 表层（Bid1-20 / Ask1-20）：50% 权重
- 中层（Bid21-200 / Ask21-200）：40% 权重
- 聚合层（0.1 USDC 聚合前 50 档）：10% 权重
"""

import math
from decimal import Decimal
from typing import Dict, List, Tuple


# 分层配置（200 档版本）
LAYER_CONFIG = {
    'surface': (0, 20),       # 表层：0-20 档 - 高频交易
    'middle': (20, 200),      # 中层：20-200 档 - 机构订单
}

# 分层权重
LAYER_WEIGHTS = {
    'surface': 0.50,
    'middle': 0.40,
    'aggregated': 0.10,
}


class LiquidityAnalyzer:
    """流动性分析器 - 分层计算深度指标（200 档版本）"""
    
    def __init__(self, max_levels: int = 200, price_step: float = 0.1):
        """
        初始化流动性分析器
        
        Args:
            max_levels: 最大支持档位（200）
            price_step: 聚合价格精度（0.1 USDC）
        """
        self.max_levels = max_levels
        self.price_step = price_step
    
    def aggregate_orderbook(self, bids: List[Tuple[str, str]], 
                            asks: List[Tuple[str, str]]) -> Tuple[List[Tuple[float, float]], ...]:
        """
        聚合订单簿到指定价格精度
        
        Args:
            bids: 买单列表 [(价格，数量), ...]
            asks: 卖单列表 [(价格，数量), ...]
        
        Returns:
            (聚合买单，聚合卖单)
        """
        aggregated_bids = {}
        aggregated_asks = {}
        
        # 聚合买单（向下取整）
        for price_str, qty_str in bids:
            price = float(price_str)
            volume = float(qty_str)
            agg_price = math.floor(price / self.price_step) * self.price_step
            aggregated_bids[agg_price] = aggregated_bids.get(agg_price, 0.0) + volume
        
        # 聚合卖单（向上取整）
        for price_str, qty_str in asks:
            price = float(price_str)
            volume = float(qty_str)
            agg_price = math.ceil(price / self.price_step) * self.price_step
            aggregated_asks[agg_price] = aggregated_asks.get(agg_price, 0.0) + volume
        
        # 转换为排序列表
        bids_list = sorted(aggregated_bids.items(), key=lambda x: x[0], reverse=True)
        asks_list = sorted(aggregated_asks.items(), key=lambda x: x[0])
        
        return bids_list, asks_list
    
    def analyze_layers(self, bids: List[Tuple[str, str]], 
                       asks: List[Tuple[str, str]]) -> Dict[str, Dict]:
        """
        分层分析深度（200 档 + 聚合）
        
        Args:
            bids: 买单列表
            asks: 卖单列表
        
        Returns:
            各层级深度数据
        """
        results = {}
        
        # 表层（0-20 档）
        start, end = LAYER_CONFIG['surface']
        actual_end = min(end, len(bids), len(asks))
        bid_depth = sum(float(b[1]) for b in bids[start:actual_end])
        ask_depth = sum(float(a[1]) for a in asks[start:actual_end])
        total = bid_depth + ask_depth
        results['surface'] = {
            'bid_depth': bid_depth,
            'ask_depth': ask_depth,
            'total_depth': total,
            'imbalance': (bid_depth - ask_depth) / total if total > 0 else 0.0,
        }
        
        # 中层（20-200 档）
        start, end = LAYER_CONFIG['middle']
        actual_end = min(end, len(bids), len(asks))
        bid_depth = sum(float(b[1]) for b in bids[start:actual_end])
        ask_depth = sum(float(a[1]) for a in asks[start:actual_end])
        total = bid_depth + ask_depth
        results['middle'] = {
            'bid_depth': bid_depth,
            'ask_depth': ask_depth,
            'total_depth': total,
            'imbalance': (bid_depth - ask_depth) / total if total > 0 else 0.0,
        }
        
        # 聚合层（0.1 USDC 聚合前 50 档）
        agg_bids, agg_asks = self.aggregate_orderbook(bids, asks)
        bid_depth = sum(b[1] for b in agg_bids[:50])
        ask_depth = sum(a[1] for a in agg_asks[:50])
        total = bid_depth + ask_depth
        results['aggregated'] = {
            'bid_depth': bid_depth,
            'ask_depth': ask_depth,
            'total_depth': total,
            'imbalance': (bid_depth - ask_depth) / total if total > 0 else 0.0,
        }
        
        return results
    
    def calc_spread_rate(self, bids: List[Tuple[str, str]], 
                         asks: List[Tuple[str, str]]) -> float:
        """计算价差率"""
        if not bids or not asks:
            return 0.0
        
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid_price = (best_bid + best_ask) / 2
        
        if mid_price == 0:
            return 0.0
        
        spread = best_ask - best_bid
        spread_rate = (spread / mid_price) * 100
        return spread_rate
    
    def get_metrics(self, bids: List[Tuple[str, str]], 
                    asks: List[Tuple[str, str]]) -> Dict[str, float]:
        """获取完整的流动性指标"""
        # 分层分析
        layers = self.analyze_layers(bids, asks)
        
        # 价差率
        spread_rate = self.calc_spread_rate(bids, asks)
        
        # 各层深度
        depth_surface = layers['surface']['total_depth']
        depth_middle = layers['middle']['total_depth']
        depth_aggregated = layers['aggregated']['total_depth']
        
        # 深度不平衡（表层 + 中层加权）
        imbalance_surface = layers['surface']['imbalance']
        imbalance_middle = layers['middle']['imbalance']
        depth_imbalance = imbalance_surface * 0.60 + imbalance_middle * 0.40
        
        return {
            'spread_rate': spread_rate,
            'depth_surface': depth_surface,
            'depth_middle': depth_middle,
            'depth_aggregated': depth_aggregated,
            'depth_imbalance': depth_imbalance,
            'layers': layers,
        }


if __name__ == "__main__":
    # 测试示例
    analyzer = LiquidityAnalyzer(max_levels=200, price_step=0.1)
    
    # 模拟订单簿数据（200 档）
    bids = [(f"2180.{100-i:03d}", f"{10 + i*0.1:.3f}") for i in range(200)]
    asks = [(f"2180.{100+i:03d}", f"{10 + i*0.1:.3f}") for i in range(200)]
    
    metrics = analyzer.get_metrics(bids, asks)
    
    print("流动性指标（200 档版本）：")
    print(f"  价差率：{metrics['spread_rate']:.6f}%")
    print(f"  表层深度：{metrics['depth_surface']:.3f} ETH")
    print(f"  中层深度：{metrics['depth_middle']:.3f} ETH")
    print(f"  聚合深度：{metrics['depth_aggregated']:.3f} ETH")
    print(f"  深度不平衡：{metrics['depth_imbalance']:.4f}")
