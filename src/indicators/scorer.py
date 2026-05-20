# -*- coding: utf-8 -*-
"""
趋势剥头皮评分器

三维度评分：
1. 趋势强度（40%）：K线连续性 + tick震荡，取高分
2. 波动匹配（30%）：振幅 vs TP目标距离
3. 深度充足度（30%）：买卖盘 vs 所需ETH

输出：综合分数 + 预计方向 + 置信度
"""

from datetime import datetime
from typing import Dict


class ScalpingScorer:
    """趋势剥头皮评分器"""

    def __init__(self):
        # 趋势连续性阈值
        self.streak_thresholds = {
            5: 100,  # 5连 → 满分
            4: 80,
            3: 60,
            2: 40,
        }

        # tick反转阈值
        self.tick_reversal_thresholds = {
            5: 100,  # 5次反转 → 满分
            4: 80,
            3: 60,
            2: 40,
        }

        # 评分权重
        self.weight_trend = 0.40
        self.weight_vol = 0.30
        self.weight_depth = 0.30

    def _score_trend(self, params: Dict) -> tuple[float, str]:
        """
        趋势强度评分（40%）

        K线连续性和tick震荡取高分，不叠加
        返回 (分数, 方向 'UP'/'DOWN'/'NONE')
        """
        # A. K线连续性评分
        kline_streak = params.get('kline_streak', 0)
        kline_dir = params.get('kline_streak_direction', 'NONE')
        kline_score = 0
        for threshold, score in sorted(self.streak_thresholds.items(), reverse=True):
            if kline_streak >= threshold:
                kline_score = score
                break

        # B. tick震荡评分
        tick_reversals = params.get('tick_reversals', 0)
        tick_amplitude_pct = params.get('tick_amplitude_pct', 0)
        tp_pct = params.get('tp_target_pct', 0)
        tick_score = 0

        if tick_reversals > 0 and tp_pct > 0:
            # 反转次数达标 且 振幅足够
            if tick_amplitude_pct >= tp_pct:
                for threshold, score in sorted(self.tick_reversal_thresholds.items(), reverse=True):
                    if tick_reversals >= threshold:
                        tick_score = score
                        break

        # 取高分（趋势和震荡不同时发生）
        if kline_score >= tick_score:
            return float(kline_score), kline_dir
        else:
            return float(tick_score), params.get('tick_momentum', 'NONE')

    def _score_volatility(self, params: Dict) -> float:
        """
        波动匹配评分（30%）

        比较振幅和TP目标的比值
        """
        tp_pct = params.get('tp_target_pct', 0)
        if tp_pct <= 0:
            return 50.0

        # 优先用tick振幅（更实时），其次用1min振幅
        tick_amp = params.get('tick_amplitude_pct', 0)
        min_amp = params.get('1min_amplitude', 0)
        amplitude = max(tick_amp, min_amp)

        if amplitude <= 0:
            return 0.0

        ratio = amplitude / tp_pct

        # 线性映射
        if ratio >= 2.0:
            return 100.0  # 振幅是TP的2倍，轻松打到
        elif ratio >= 1.0:
            return 50.0 + (ratio - 1.0) / 1.0 * 50.0  # 50→100
        elif ratio >= 0.5:
            return 25.0 + (ratio - 0.5) / 0.5 * 25.0  # 25→50
        else:
            return ratio / 0.5 * 25.0  # 0→25

    def _score_depth(self, params: Dict) -> float:
        """
        深度充足度评分（30%）

        买卖盘vs所需ETH量
        """
        required_eth = params.get('required_eth', 0)
        if required_eth <= 0:
            return 50.0

        # 双向深度都检查（开多看买盘，开空看卖盘）
        bid_depth = params.get('bid_depth', 0)
        ask_depth = params.get('ask_depth', 0)

        # 方向由趋势决定，但评分取最差边的保障
        # 即：无论开多开空，对手盘都要能吃下
        direction = params.get('predicted_direction', 'NONE')
        if direction == 'LONG':
            # 开多 → 平仓时卖盘要能吃下
            opposing_depth = ask_depth
        elif direction == 'SHORT':
            # 开空 → 平仓时买盘要能吃下
            opposing_depth = bid_depth
        else:
            # 无方向 → 取较差的一边
            opposing_depth = min(bid_depth, ask_depth)

        ratio = opposing_depth / required_eth

        if ratio >= 1.0:
            return 100.0  # 完全能吃下
        elif ratio >= 0.5:
            return 50.0 + (ratio - 0.5) / 0.5 * 50.0  # 50→100
        elif ratio >= 0.2:
            return 25.0 + (ratio - 0.2) / 0.3 * 25.0  # 25→50
        else:
            return ratio / 0.2 * 25.0  # 0→25

    def score(self, params: Dict) -> Dict:
        """
        综合评分

        Args:
            params: {
                'kline_streak': int,           # 最长K线连续数
                'kline_streak_direction': str, # 'UP'/'DOWN'/'NONE'
                'tick_reversals': int,         # 30秒内tick反转次数
                'tick_amplitude_pct': float,   # 30秒内tick振幅%
                'tick_momentum': str,          # 'UP'/'DOWN'/'NONE'
                '1min_amplitude': float,       # 上根K线振幅%
                'tp_target_pct': float,        # TP目标距离%
                'bid_depth': float,            # 买盘深度ETH
                'ask_depth': float,            # 卖盘深度ETH
                'required_eth': float,         # 所需ETH量
                'current_price': float,        # 当前价格
            }

        Returns:
            {
                'total_score': float,
                'direction': 'LONG'/'SHORT'/'NONE',
                'confidence': float,
                'category_scores': {...},
                'recommendation': str,
                'signal_emoji': str,
                'signal_color': str,
            }
        """
        # 1. 趋势评分
        trend_score, trend_dir = self._score_trend(params)

        # 2. 波动匹配评分
        vol_score = self._score_volatility(params)

        # 3. 深度充足度评分
        depth_score = self._score_depth(params)

        # 综合评分
        total_score = (
            trend_score * self.weight_trend
            + vol_score * self.weight_vol
            + depth_score * self.weight_depth
        )

        # 方向判定：综合趋势方向 + tick动量
        predicted_direction = 'NONE'
        if trend_dir != 'NONE':
            predicted_direction = 'LONG' if trend_dir == 'UP' else 'SHORT'
        else:
            tick_momentum = params.get('tick_momentum', 'NONE')
            if tick_momentum != 'NONE':
                predicted_direction = 'LONG' if tick_momentum == 'UP' else 'SHORT'

        # 置信度：基于趋势强度和数据可用性
        confidence = 0.0
        if trend_score >= 80:
            confidence = 0.9
        elif trend_score >= 60:
            confidence = 0.7
        elif trend_score >= 40:
            confidence = 0.5
        else:
            confidence = 0.3

        # 如果深度不足，降低置信度
        if depth_score < 50:
            confidence = min(confidence, 0.5)

        # 交易建议
        if total_score >= 70:
            recommendation = "开仓"
            signal_emoji = "🟢"
            signal_color = "green"
        elif total_score >= 50:
            recommendation = "观望"
            signal_emoji = "🟡"
            signal_color = "yellow"
        else:
            recommendation = "暂停"
            signal_emoji = "🔴"
            signal_color = "red"

        return {
            'total_score': round(total_score, 1),
            'direction': predicted_direction,
            'confidence': confidence,
            'category_scores': {
                'trend': round(trend_score, 1),
                'volatility': round(vol_score, 1),
                'depth': round(depth_score, 1),
            },
            'recommendation': recommendation,
            'signal_emoji': signal_emoji,
            'signal_color': signal_color,
            'timestamp': datetime.now().isoformat(),
        }
