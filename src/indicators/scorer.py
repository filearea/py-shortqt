# -*- coding: utf-8 -*-
"""
盘面质量评分模块

综合波动率和流动性指标，计算盘面质量评分 (0-100)，
并提供交易建议（适合/观望/暂停）。
"""

from typing import Optional


# 评分权重配置
WEIGHTS = {
    'volatility': 30,      # 波动率适中：30 分
    'spread_rate': 30,     # 价差率低：30 分
    'orderbook_depth': 20, # 订单簿深度：20 分
    'amplitude_stable': 20 # 振幅稳定：20 分
}

# 阈值配置
THRESHOLDS = {
    'volatility': {
        'ideal_min': 0.05,   # 理想振幅区间 0.05% - 0.15%
        'ideal_max': 0.15,
        'acceptable_min': 0.03,  # 可接受区间 0.03% - 0.3%
        'acceptable_max': 0.3
    },
    'spread_rate': {
        'excellent': 0.005,  # < 0.005% 优秀
        'good': 0.01,        # < 0.01% 良好
        'acceptable': 0.02   # < 0.02% 可接受
    },
    'orderbook_depth': {
        'excellent': 3000,   # > 3000 ETH 优秀
        'good': 2000,        # > 2000 ETH 良好
        'acceptable': 1000   # > 1000 ETH 可接受
    },
    'amplitude_change_rate': {
        'stable_min': 0.8,   # 0.8 - 1.2 稳定
        'stable_max': 1.2
    }
}


class QualityScorer:
    """盘面质量评分器"""
    
    def __init__(self):
        """初始化评分器"""
        self.last_score: int = 0
        self.last_recommendation: str = '观望'
    
    def calculate(self, volatility: dict, liquidity: dict) -> dict:
        """
        计算盘面质量评分
        
        Args:
            volatility: 波动率指标字典（来自 VolatilityAnalyzer.get_metrics()）
            liquidity: 流动性指标字典（来自 LiquidityAnalyzer.get_metrics()）
        
        Returns:
            评分类字典 {
                'quality_score': int,        # 0-100 分
                'recommendation': str,       # '适合交易' / '观望' / '暂停交易'
                'signal_color': str,         # 'green' / 'yellow' / 'red'
                'signal_emoji': str,         # '🟢' / '🟡' / '🔴'
                'details': list,             # 评分详情
                'score_breakdown': dict      # 各部分得分
            }
        """
        score = 0
        details = []
        breakdown = {}
        
        # 1. 波动率评分 (30 分)
        vol_score, vol_detail = self._score_volatility(volatility)
        score += vol_score
        breakdown['volatility'] = vol_score
        details.append(vol_detail)
        
        # 2. 价差率评分 (30 分)
        spread_score, spread_detail = self._score_spread_rate(liquidity)
        score += spread_score
        breakdown['spread_rate'] = spread_score
        details.append(spread_detail)
        
        # 3. 订单簿深度评分 (20 分)
        depth_score, depth_detail = self._score_depth(liquidity)
        score += depth_score
        breakdown['depth'] = depth_score
        details.append(depth_detail)
        
        # 4. 振幅稳定性评分 (20 分)
        stable_score, stable_detail = self._score_amplitude_stability(volatility)
        score += stable_score
        breakdown['stability'] = stable_score
        details.append(stable_detail)
        
        # 确保不超过 100 分
        score = min(100, score)
        
        # 确定交易建议和信号灯
        recommendation, signal_color, signal_emoji = self._get_recommendation(score)
        
        # 保存上次评分
        self.last_score = score
        self.last_recommendation = recommendation
        
        return {
            'quality_score': score,
            'recommendation': recommendation,
            'signal_color': signal_color,
            'signal_emoji': signal_emoji,
            'details': details,
            'score_breakdown': breakdown
        }
    
    def _score_volatility(self, volatility: dict) -> tuple:
        """
        波动率评分 (30 分)
        
        理想区间：0.05% - 0.15%
        """
        amp = volatility.get('1min_amplitude', 0)
        thresholds = THRESHOLDS['volatility']
        
        if thresholds['ideal_min'] <= amp <= thresholds['ideal_max']:
            return 30, f'波动率适中 +30'
        elif thresholds['acceptable_min'] <= amp < thresholds['ideal_min']:
            return 20, f'波动率偏低 +20'
        elif thresholds['ideal_max'] < amp <= thresholds['acceptable_max']:
            return 20, f'波动率偏高 +20'
        elif amp < thresholds['acceptable_min']:
            return 0, f'波动率过低 +0'
        else:  # amp > thresholds['acceptable_max']
            return 0, f'波动率过高 +0'
    
    def _score_spread_rate(self, liquidity: dict) -> tuple:
        """
        价差率评分 (30 分)
        """
        spread_rate = liquidity.get('spread_rate', 0)
        thresholds = THRESHOLDS['spread_rate']
        
        if spread_rate <= thresholds['excellent']:
            return 30, f'价差率优秀 +30'
        elif spread_rate <= thresholds['good']:
            return 25, f'价差率良好 +25'
        elif spread_rate <= thresholds['acceptable']:
            return 15, f'价差率可接受 +15'
        else:
            return 0, f'价差率过高 +0'
    
    def _score_depth(self, liquidity: dict) -> tuple:
        """
        订单簿深度评分 (20 分)
        """
        depth = liquidity.get('orderbook_depth', 0)
        thresholds = THRESHOLDS['orderbook_depth']
        
        if depth >= thresholds['excellent']:
            return 20, f'深度充足 +20'
        elif depth >= thresholds['good']:
            return 15, f'深度良好 +15'
        elif depth >= thresholds['acceptable']:
            return 10, f'深度可接受 +10'
        else:
            return 0, f'深度不足 +0'
    
    def _score_amplitude_stability(self, volatility: dict) -> tuple:
        """
        振幅稳定性评分 (20 分)
        
        基于振幅变化率：0.8 - 1.2 为稳定
        """
        change_rate = volatility.get('change_rate', 1.0)
        thresholds = THRESHOLDS['amplitude_change_rate']
        
        if thresholds['stable_min'] <= change_rate <= thresholds['stable_max']:
            return 20, f'振幅稳定 +20'
        elif change_rate < 0.5 or change_rate > 2.0:
            return 0, f'振幅剧烈波动 +0'
        else:
            return 10, f'振幅较稳定 +10'
    
    def _get_recommendation(self, score: int) -> tuple:
        """
        根据评分确定交易建议
        
        Returns:
            (recommendation, signal_color, signal_emoji)
        """
        if score >= 70:
            return '适合交易', 'green', '🟢'
        elif score >= 40:
            return '观望', 'yellow', '🟡'
        else:
            return '暂停交易', 'red', '🔴'
    
    def get_last_score(self) -> int:
        """获取上次评分"""
        return self.last_score
    
    def get_last_recommendation(self) -> str:
        """获取上次交易建议"""
        return self.last_recommendation
