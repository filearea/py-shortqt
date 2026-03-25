# -*- coding: utf-8 -*-
"""
流动性指标计算 - v1.4.0

分层分析订单簿深度：
- 表层（Bid1-20 / Ask1-20）：50% 权重
- 中层（Bid21-100 / Ask21-100）：30% 权重
- 深层（Bid101-1000 / Ask101-1000）：20% 权重
"""

from decimal import Decimal
from typing import Dict, List, Tuple, Optional


# 分层配置
LAYER_CONFIG = {
    'surface': (0, 20),      # 表层：0-20 档
    'middle': (20, 100),     # 中层：20-100 档
    'deep': (100, 1000),     # 深层：100-1000 档
}

# 分层权重
LAYER_WEIGHTS = {
    'surface': 0.50,
    'middle': 0.30,
    'deep': 0.20,
}


class LiquidityAnalyzer:
    """流动性分析器 - 分层计算深度指标"""
    
    def __init__(self, max_levels: int = 1000):
        """
        初始化流动性分析器
        
        Args:
            max_levels: 最大支持档位
        """
        self.max_levels = max_levels
    
    def analyze_layers(self, bids: List[Tuple[str, str]], 
                       asks: List[Tuple[str, str]]) -> Dict[str, Dict]:
        """
        分层分析深度
        
        Args:
            bids: 买单列表 [(价格，数量), ...]
            asks: 卖单列表 [(价格，数量), ...]
        
        Returns:
            各层级深度数据
        """
        results = {}
        
        for layer_name, (start, end) in LAYER_CONFIG.items():
            # 确保不超出实际档位
            actual_end = min(end, len(bids), len(asks))
            
            if start >= actual_end:
                # 该层无数据
                results[layer_name] = {
                    'bid_depth': 0.0,
                    'ask_depth': 0.0,
                    'total_depth': 0.0,
                    'imbalance': 0.0,
                }
                continue
            
            # 计算该层深度
            bid_depth = sum(float(b[1]) for b in bids[start:actual_end])
            ask_depth = sum(float(a[1]) for a in asks[start:actual_end])
            total_depth = bid_depth + ask_depth
            
            # 计算深度不平衡
            if total_depth > 0:
                imbalance = (bid_depth - ask_depth) / total_depth
            else:
                imbalance = 0.0
            
            results[layer_name] = {
                'bid_depth': bid_depth,
                'ask_depth': ask_depth,
                'total_depth': total_depth,
                'imbalance': imbalance,
            }
        
        return results
    
    def calc_spread_rate(self, bids: List[Tuple[str, str]], 
                         asks: List[Tuple[str, str]]) -> float:
        """
        计算价差率
        
        Args:
            bids: 买单列表
            asks: 卖单列表
        
        Returns:
            价差率（百分比）
        """
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
    
    def calc_total_depth(self, bids: List[Tuple[str, str]], 
                         asks: List[Tuple[str, str]], 
                         levels: int = 20) -> float:
        """
        计算指定档位的总深度
        
        Args:
            bids: 买单列表
            asks: 卖单列表
            levels: 档位数量
        
        Returns:
            总深度（ETH）
        """
        actual_levels = min(levels, len(bids), len(asks))
        
        bid_depth = sum(float(b[1]) for b in bids[:actual_levels])
        ask_depth = sum(float(a[1]) for a in asks[:actual_levels])
        
        return bid_depth + ask_depth
    
    def get_metrics(self, bids: List[Tuple[str, str]], 
                    asks: List[Tuple[str, str]]) -> Dict[str, float]:
        """
        获取完整的流动性指标
        
        Args:
            bids: 买单列表
            asks: 卖单列表
        
        Returns:
            流动性指标字典
        """
        # 分层分析
        layers = self.analyze_layers(bids, asks)
        
        # 价差率
        spread_rate = self.calc_spread_rate(bids, asks)
        
        # 各层深度
        depth_surface = layers['surface']['total_depth']
        depth_middle = layers['middle']['total_depth']
        depth_deep = layers['deep']['total_depth']
        
        # 深度不平衡（表层）
        depth_imbalance = layers['surface']['imbalance']
        
        return {
            'spread_rate': spread_rate,
            'depth_surface': depth_surface,
            'depth_middle': depth_middle,
            'depth_deep': depth_deep,
            'depth_imbalance': depth_imbalance,
            'layers': layers,
        }


# 工具函数
def analyze_depth_distribution(bids: List[Tuple[str, str]], 
                                asks: List[Tuple[str, str]], 
                                bins: int = 10) -> Dict:
    """
    分析深度分布（用于可视化）
    
    Args:
        bids: 买单列表
        asks: 卖单列表
        bins: 分桶数量
    
    Returns:
        深度分布数据
    """
    if not bids or not asks:
        return {'bid_distribution': [], 'ask_distribution': []}
    
    # 按价格分桶
    min_price = min(float(bids[-1][0]), float(asks[-1][0]))
    max_price = max(float(bids[0][0]), float(asks[0][0]))
    price_range = max_price - min_price
    
    if price_range == 0:
        return {'bid_distribution': [], 'ask_distribution': []}
    
    bucket_size = price_range / bins
    bid_buckets = [0.0] * bins
    ask_buckets = [0.0] * bins
    
    # 分配买单到桶
    for price_str, qty_str in bids:
        price = float(price_str)
        qty = float(qty_str)
        bucket_idx = int((price - min_price) / bucket_size)
        bucket_idx = min(bucket_idx, bins - 1)
        bid_buckets[bucket_idx] += qty
    
    # 分配卖单到桶
    for price_str, qty_str in asks:
        price = float(price_str)
        qty = float(qty_str)
        bucket_idx = int((price - min_price) / bucket_size)
        bucket_idx = min(bucket_idx, bins - 1)
        ask_buckets[bucket_idx] += qty
    
    return {
        'bid_distribution': bid_buckets,
        'ask_distribution': ask_buckets,
        'price_range': (min_price, max_price),
    }


if __name__ == "__main__":
    # 测试示例
    analyzer = LiquidityAnalyzer(max_levels=1000)
    
    # 模拟订单簿数据（200 档）
    bids = [(f"2180.{100-i:03d}", f"{10 + i*0.1:.3f}") for i in range(200)]
    asks = [(f"2180.{100+i:03d}", f"{10 + i*0.1:.3f}") for i in range(200)]
    
    metrics = analyzer.get_metrics(bids, asks)
    
    print("流动性指标：")
    print(f"  价差率：{metrics['spread_rate']:.6f}%")
    print(f"  表层深度：{metrics['depth_surface']:.3f} ETH")
    print(f"  中层深度：{metrics['depth_middle']:.3f} ETH")
    print(f"  深层深度：{metrics['depth_deep']:.3f} ETH")
    print(f"  深度不平衡：{metrics['depth_imbalance']:.4f}")
    
    print("\n分层详情：")
    for layer_name, layer_data in metrics['layers'].items():
        print(f"  {layer_name}:")
        print(f"    买深度：{layer_data['bid_depth']:.3f} ETH")
        print(f"    卖深度：{layer_data['ask_depth']:.3f} ETH")
        print(f"    总深度：{layer_data['total_depth']:.3f} ETH")
        print(f"    不平衡：{layer_data['imbalance']:.4f}")
