# -*- coding: utf-8 -*-
"""
流动性指标计算 - v1.4.3 优化（50 档 + 平滑处理）

分层分析订单簿深度：
- 表层（Bid1-10 / Ask1-10）：60% 权重 - 最稳定
- 中层（Bid11-50 / Ask11-50）：40% 权重 - 中等稳定
- 聚合层（0.5 USDC 聚合前 20 档）：用于验证

注意：减少档位和提高聚合精度可以显著降低波动
"""

import math
from decimal import Decimal
from typing import Dict, List, Tuple
from collections import deque


# 分层配置（50 档版本 - 更稳定）
LAYER_CONFIG = {
    'surface': (0, 10),       # 表层：0-10 档 - 最稳定
    'middle': (10, 50),       # 中层：10-50 档 - 中等稳定
}

# 分层权重
LAYER_WEIGHTS = {
    'surface': 0.60,          # 表层权重更高（更稳定）
    'middle': 0.40,
    'aggregated': 0.0,        # 不使用聚合层（减少波动）
}


class LiquidityAnalyzer:
    """流动性分析器 - 50 档版本 + 平滑处理"""
    
    def __init__(self, max_levels: int = 50, price_step: float = 0.5):
        """
        初始化流动性分析器
        
        Args:
            max_levels: 最大支持档位（50）
            price_step: 聚合价格精度（0.5 USDC）
        """
        self.max_levels = max_levels
        self.price_step = price_step
        # 缓存最新的订单簿数据
        self._last_bids: List[Tuple[str, str]] = []
        self._last_asks: List[Tuple[str, str]] = []
        
        # 平滑处理：用 list 保存最近 3 次的深度值（不用 deque，避免 pop 问题）
        self._depth_history: List[float] = []
    
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
        
        # 聚合层（0.5 USDC 聚合前 20 档）- 仅用于验证，不参与评分
        agg_bids, agg_asks = self.aggregate_orderbook(bids, asks)
        bid_depth = sum(b[1] for b in agg_bids[:20])
        ask_depth = sum(a[1] for a in agg_asks[:20])
        total = bid_depth + ask_depth
        results['aggregated'] = {
            'bid_depth': bid_depth,
            'ask_depth': ask_depth,
            'total_depth': total,
            'imbalance': (bid_depth - ask_depth) / total if total > 0 else 0.0,
        }
        
        return results
    
    def _smooth_depth(self, current_depth: float) -> float:
        """
        平滑深度数据（移动平均）- 优化版
        
        Args:
            current_depth: 当前深度值
        
        Returns:
            平滑后的深度值
        """
        # 简化：只保存 3 次历史，减少内存
        if not hasattr(self, '_depth_history'):
            self._depth_history = []
        
        self._depth_history.append(current_depth)
        if len(self._depth_history) > 3:
            self._depth_history.pop(0)
        
        # 如果历史数据不足，直接返回当前值
        if len(self._depth_history) < 2:
            return current_depth
        
        # 简单平均（比加权更快）
        avg_depth = sum(self._depth_history) / len(self._depth_history)
        return avg_depth
    
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
    
    def update_orderbook(self, bids: List[Tuple[str, str]], 
                         asks: List[Tuple[str, str]]):
        """
        更新缓存的订单簿数据
        
        Args:
            bids: 买单列表 [(价格，数量), ...]
            asks: 卖单列表 [(价格，数量), ...]
        """
        self._last_bids = bids
        self._last_asks = asks
    
    def get_metrics(self, bids: List[Tuple[str, str]] = None, 
                    asks: List[Tuple[str, str]] = None) -> Dict[str, float]:
        """
        获取完整的流动性指标
        
        Args:
            bids: 买单列表（可选，如果不传则使用缓存的数据）
            asks: 卖单列表（可选，如果不传则使用缓存的数据）
        
        Returns:
            流动性指标字典
        """
        # 使用传入的参数或缓存的数据
        if bids is not None and asks is not None:
            # 更新缓存
            self._last_bids = bids
            self._last_asks = asks
        else:
            # 使用缓存的数据
            bids = self._last_bids
            asks = self._last_asks
        
        # 如果缓存为空，返回默认值
        if not bids or not asks:
            return {
                'spread': 0.0,
                'spread_rate': 0.0,
                'spread_status': '--',
                'depth_surface': 0.0,
                'bid_depth_surface': 0.0,
                'ask_depth_surface': 0.0,
                'depth_middle': 0.0,
                'depth_aggregated': 0.0,
                'depth_imbalance': 0.0,
                'depth_status': '--',
                'layers': {},
            }
        
        # 分层分析
        layers = self.analyze_layers(bids, asks)
        
        # 计算绝对价差和价差率
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        spread = best_ask - best_bid
        spread_rate = self.calc_spread_rate(bids, asks)
        
        # 各层深度
        depth_surface = layers['surface']['total_depth']
        depth_middle = layers['middle']['total_depth']
        depth_aggregated = layers['aggregated']['total_depth']
        
        # 深度不平衡（表层 + 中层加权）
        imbalance_surface = layers['surface']['imbalance']
        imbalance_middle = layers['middle']['imbalance']
        depth_imbalance = imbalance_surface * 0.60 + imbalance_middle * 0.40
        
        # 平滑深度数据（减少剧烈波动）
        depth_surface = self._smooth_depth(depth_surface)
        depth_middle = self._smooth_depth(depth_middle)
        depth_aggregated = self._smooth_depth(depth_aggregated)
        
        # 价差状态评估
        if spread_rate < 0.01:
            spread_status = '[OK]'  # 优秀
        elif spread_rate < 0.05:
            spread_status = '[GOOD]'  # 良好
        else:
            spread_status = '[WARN]'  # 较差
        
        # 深度状态评估（平滑后的深度）
        if depth_surface > 50:  # 降低阈值（50 档版本）
            depth_status = '[OK]'  # 深度充足
        elif depth_surface > 25:
            depth_status = '[GOOD]'  # 一般
        else:
            depth_status = '[WARN]'  # 深度不足
        
        return {
            'spread': spread,
            'spread_rate': spread_rate,
            'spread_status': spread_status,
            'depth_surface': depth_surface,
            'bid_depth_surface': layers['surface']['bid_depth'],
            'ask_depth_surface': layers['surface']['ask_depth'],
            'depth_middle': depth_middle,
            'depth_aggregated': depth_aggregated,
            'depth_imbalance': depth_imbalance,
            'depth_status': depth_status,
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
