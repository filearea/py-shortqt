# -*- coding: utf-8 -*-
"""
动态评分器 - v1.4.0

基于历史百分位数，动态评估盘面质量（0-100 分）
输出：综合评分 + 分类评分 + 交易建议
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class DynamicScorer:
    """动态评分器 - 自动维护历史数据窗口"""
    
    def __init__(self, window_days: int = 14, data_dir: Optional[Path] = None):
        """
        初始化评分器
        
        Args:
            window_days: 历史数据窗口（天数）
            data_dir: 数据目录（自动加载历史数据）
        """
        self.window_days = window_days
        self.data_dir = data_dir
        
        # 历史数据存储
        self.historical_data = {
            '1min_amplitude': [],
            '3h_avg_amplitude': [],
            'spread_rate': [],
            'depth_surface': [],
            'depth_middle': [],
            'depth_deep': [],
            'depth_imbalance': [],
            'atr_14': [],
            'volume_trend': [],
        }
        
        # 如果提供了数据目录，自动加载历史数据
        if data_dir and data_dir.exists():
            self._load_historical_data()
    
    def _load_historical_data(self):
        """从数据目录加载历史数据"""
        # TODO: 实现从 data/klines 和 data/orderbook 加载历史数据
        # 目前先从 market.log 加载
        pass
    
    def update_history(self, new_data: Dict[str, float]):
        """
        添加新数据，维护滚动窗口
        
        Args:
            new_data: 新数据字典
        """
        cutoff = datetime.now() - timedelta(days=self.window_days)
        
        for key, value in new_data.items():
            if key in self.historical_data:
                self.historical_data[key].append({
                    'value': value,
                    'timestamp': datetime.now().isoformat()
                })
                
                # 清理过期数据
                self.historical_data[key] = [
                    d for d in self.historical_data[key]
                    if datetime.fromisoformat(d['timestamp']) > cutoff
                ]
    
    def _get_history_values(self, key: str) -> List[float]:
        """获取历史值列表"""
        return [d['value'] for d in self.historical_data.get(key, [])]
    
    def _percentile_score(self, current_value: float, historical_data: List[float], 
                          higher_is_better: bool = True) -> float:
        """
        基于历史百分位数评分（0-100 分）
        
        Args:
            current_value: 当前值
            historical_data: 历史数据列表
            higher_is_better: True=越高越好，False=越低越好
        
        Returns:
            0-100 分
        """
        if not historical_data:
            return 50.0  # 默认中等分数
        
        # 计算当前值的百分位排名
        sorted_data = sorted(historical_data)
        count_below = sum(1 for x in sorted_data if x <= current_value)
        percentile_rank = count_below / len(sorted_data) * 100
        
        if higher_is_better:
            score = percentile_rank  # 越高分数越高
        else:
            score = 100 - percentile_rank  # 越低分数越高
        
        return max(0.0, min(100.0, score))
    
    def _confidence_weight(self, sample_count: int, target_count: int = 100) -> float:
        """
        根据样本量计算置信度权重
        
        Args:
            sample_count: 样本数量
            target_count: 目标样本数
        
        Returns:
            权重（0-1）
        """
        if sample_count < 30:
            # 统计学上不足以计算百分位数
            return sample_count / 30 * 0.5  # 最多 50% 权重
        elif sample_count < target_count:
            # 可参考但需打折
            return 0.5 + (sample_count - 30) / (target_count - 30) * 0.5
        else:
            return 1.0
    
    def _calc_volatility_score(self, current_data: Dict[str, float], 
                                history: Dict[str, List[float]]) -> float:
        """
        波动率评分（30%）
        
        指标：
        - 1 分钟振幅（越低越好）
        - 3 小时平均振幅（越低越好）
        """
        amp_1min_score = self._percentile_score(
            current_data.get('1min_amplitude', 0),
            history.get('1min_amplitude', []),
            higher_is_better=False
        )
        
        amp_3h_score = self._percentile_score(
            current_data.get('3h_avg_amplitude', 0),
            history.get('3h_avg_amplitude', []),
            higher_is_better=False
        )
        
        # 平均波动率评分
        vol_score = (amp_1min_score + amp_3h_score) / 2
        
        return vol_score
    
    def _calc_liquidity_score(self, current_data: Dict[str, float], 
                               history: Dict[str, List[float]]) -> float:
        """
        流动性评分（40%）
        
        分层加权：
        - 表层深度（50%）：Bid1-20 + Ask1-20
        - 中层深度（30%）：Bid21-100 + Ask21-100
        - 深层深度（20%）：Bid101-1000 + Ask101-1000
        - 深度不平衡（越低越好）
        """
        depth_surface_score = self._percentile_score(
            current_data.get('depth_surface', 0),
            history.get('depth_surface', []),
            higher_is_better=True
        )
        
        depth_middle_score = self._percentile_score(
            current_data.get('depth_middle', 0),
            history.get('depth_middle', []),
            higher_is_better=True
        )
        
        depth_deep_score = self._percentile_score(
            current_data.get('depth_deep', 0),
            history.get('depth_deep', []),
            higher_is_better=True
        )
        
        # 分层加权深度评分
        depth_score = (
            depth_surface_score * 0.50 +
            depth_middle_score * 0.30 +
            depth_deep_score * 0.20
        )
        
        # 深度不平衡评分（越接近 0 越好）
        imbalance = current_data.get('depth_imbalance', 0)
        imbalance_score = self._percentile_score(
            abs(imbalance),
            [abs(x) for x in history.get('depth_imbalance', [])],
            higher_is_better=False
        )
        
        # 流动性综合评分
        liq_score = depth_score * 0.80 + imbalance_score * 0.20
        
        return liq_score
    
    def _calc_momentum_score(self, current_data: Dict[str, float], 
                              history: Dict[str, List[float]]) -> float:
        """
        动量评分（30%）
        
        指标：
        - ATR(14)（越高越好）
        - 成交量趋势（越高越好）
        """
        atr_score = self._percentile_score(
            current_data.get('atr_14', 0),
            history.get('atr_14', []),
            higher_is_better=True
        )
        
        volume_score = self._percentile_score(
            current_data.get('volume_trend', 0),
            history.get('volume_trend', []),
            higher_is_better=True
        )
        
        # 平均动量评分
        mom_score = (atr_score + volume_score) / 2
        
        return mom_score
    
    def score(self, current_data: Dict[str, float]) -> Dict:
        """
        计算当前盘面评分
        
        Args:
            current_data: 当前数据字典
        
        Returns:
            评分结果字典
        """
        # 提取历史值列表
        history = {
            key: self._get_history_values(key)
            for key in self.historical_data.keys()
        }
        
        # 1. 波动率评分（30%）
        vol_score = self._calc_volatility_score(current_data, history)
        
        # 2. 流动性评分（40%）
        liq_score = self._calc_liquidity_score(current_data, history)
        
        # 3. 动量评分（30%）
        mom_score = self._calc_momentum_score(current_data, history)
        
        # 4. 综合评分
        total_score = (
            vol_score * 0.30 +
            liq_score * 0.40 +
            mom_score * 0.30
        )
        
        # 5. 交易建议
        if total_score >= 75:
            recommendation = "适合交易"
            signal_emoji = "🟢"
            signal_color = "green"
        elif total_score >= 50:
            recommendation = "观望"
            signal_emoji = "🟡"
            signal_color = "yellow"
        else:
            recommendation = "暂停交易"
            signal_emoji = "🔴"
            signal_color = "red"
        
        return {
            'total_score': round(total_score, 1),
            'category_scores': {
                'volatility': round(vol_score, 1),
                'liquidity': round(liq_score, 1),
                'momentum': round(mom_score, 1),
            },
            'recommendation': recommendation,
            'signal_emoji': signal_emoji,
            'signal_color': signal_color,
            'timestamp': datetime.now().isoformat()
        }


# 简化版：用于数据不足时的文档阈值评分
class SimpleScorer:
    """简单评分器（基于文档阈值，用于数据积累期）"""
    
    def __init__(self):
        # 文档阈值（经验值）
        self.thresholds = {
            '1min_amplitude': {'low': 0.03, 'normal_min': 0.05, 'normal_max': 0.15, 'high': 0.3},
            'spread_rate': {'excellent': 0.005, 'good': 0.01, 'acceptable': 0.02},
            'depth_surface': {'good': 50, 'acceptable': 20},
        }
    
    def score(self, current_data: Dict[str, float]) -> Dict:
        """简化评分"""
        # TODO: 实现基于文档阈值的评分
        return {
            'total_score': 50.0,
            'category_scores': {
                'volatility': 50.0,
                'liquidity': 50.0,
                'momentum': 50.0,
            },
            'recommendation': "观望",
            'signal_emoji': "🟡",
            'signal_color': "yellow",
            'timestamp': datetime.now().isoformat()
        }


if __name__ == "__main__":
    # 测试示例
    scorer = DynamicScorer(window_days=14)
    
    # 模拟历史数据
    for i in range(100):
        scorer.update_history({
            '1min_amplitude': 0.05 + i * 0.001,
            '3h_avg_amplitude': 0.06 + i * 0.0005,
            'spread_rate': 0.005 + i * 0.0001,
            'depth_surface': 50 + i,
            'depth_middle': 100 + i * 2,
            'depth_deep': 400 + i * 5,
            'depth_imbalance': 0.01 + i * 0.001,
            'atr_14': 1.0 + i * 0.01,
            'volume_trend': 0.8 + i * 0.005,
        })
    
    # 当前数据
    current_data = {
        '1min_amplitude': 0.107,
        '3h_avg_amplitude': 0.068,
        'spread_rate': 0.0005,
        'depth_surface': 55.0,
        'depth_middle': 125.0,
        'depth_deep': 460.0,
        'depth_imbalance': 0.1,
        'atr_14': 1.4,
        'volume_trend': 1.2,
    }
    
    result = scorer.score(current_data)
    
    print(f"盘面质量评分：{result['total_score']}/100")
    print(f"波动率评分：{result['category_scores']['volatility']}/100")
    print(f"流动性评分：{result['category_scores']['liquidity']}/100")
    print(f"动量评分：{result['category_scores']['momentum']}/100")
    print(f"交易建议：{result['recommendation']} {result['signal_emoji']}")
