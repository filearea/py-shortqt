# -*- coding: utf-8 -*-
"""
指标管理器 - 趋势剥头皮版

整合波动率、流动性、tick追踪、评分模块，提供统一的数据接口。
"""

import time
from decimal import Decimal
from typing import Dict, List, Tuple, Optional

from .volatility import VolatilityAnalyzer
from .liquidity import LiquidityAnalyzer
from .scorer import ScalpingScorer
from .tick_tracker import TickTracker


class IndicatorsManager:
    """指标管理器"""

    def __init__(self):
        self.volatility = VolatilityAnalyzer(max_klines=200)
        self.liquidity = LiquidityAnalyzer(max_levels=50, price_step=0.5)
        self.tick_tracker = TickTracker(window_seconds=30.0)
        self.scorer = ScalpingScorer()

        # 动态参数（由交易系统传入）
        self._tp_points: float = 0.99        # 止盈点数
        self._sl_points: float = 3.99        # 止损点数
        self._leverage: int = 50             # 杠杆倍数
        self._balance_usdt: float = 50.0     # 账户余额 USDT

        # 缓存
        self._last_snapshot: Optional[Dict] = None

    # ─── 数据更新入口 ──────────────────────────────────────────

    def update_kline(self, kline: dict):
        """更新 K 线数据 — 仅收盘时重算"""
        old_ts = self.volatility.current_kline['timestamp'] if self.volatility.current_kline else None
        self.volatility.add_kline(kline)
        new_ts = self.volatility.current_kline['timestamp'] if self.volatility.current_kline else None

        if old_ts is None or new_ts != old_ts:
            self._update_snapshot()

    def update_orderbook(self, bids: List[Tuple[Decimal, Decimal]],
                         asks: List[Tuple[Decimal, Decimal]]):
        """更新订单簿 — 每次 depth 事件都重算"""
        self.liquidity.update_orderbook(bids, asks)
        self._update_snapshot()

    def update_tick(self, price: Decimal):
        """
        更新 bookTicker 价格流（每秒约 5 次）
        记录到 tick 追踪器，用于震荡检测
        """
        self.tick_tracker.add_tick(time.time(), float(price))

    # ─── 动态参数设置 ──────────────────────────────────────────

    def set_trading_params(self, tp_points: Optional[float] = None,
                           sl_points: Optional[float] = None,
                           leverage: Optional[int] = None,
                           balance_usdt: Optional[float] = None):
        """更新交易参数（止盈/止损/杠杆/余额）"""
        if tp_points is not None:
            self._tp_points = tp_points
        if sl_points is not None:
            self._sl_points = sl_points
        if leverage is not None:
            self._leverage = leverage
        if balance_usdt is not None:
            self._balance_usdt = balance_usdt

    # ─── 内部计算 ───────────────────────────────────────────────

    def _update_snapshot(self):
        """重算全部指标 + 评分"""
        vol_metrics = self.volatility.get_metrics()
        liq_metrics = self.liquidity.get_metrics()

        # K线连续性
        streak, streak_dir = self.volatility.get_kline_streak(lookback=10)

        # Tick数据
        tick_reversals = self.tick_tracker.get_reversal_count()
        tick_amplitude_pct = self.tick_tracker.get_max_amplitude()
        tick_momentum = self.tick_tracker.get_tick_momentum()

        # TP目标距离（百分比）
        current_price = float(self.volatility.current_kline['close']) if self.volatility.current_kline else 0
        tp_pct = (self._tp_points / current_price * 100) if current_price > 0 else 0

        # 所需ETH = (余额 × 杠杆) / 当前价格
        required_eth = (self._balance_usdt * self._leverage / current_price) if current_price > 0 else 0

        # 买卖深度
        bid_depth = liq_metrics.get('bid_depth_surface', 0)
        ask_depth = liq_metrics.get('ask_depth_surface', 0)

        scorer_params = {
            'kline_streak': streak,
            'kline_streak_direction': streak_dir,
            'tick_reversals': tick_reversals,
            'tick_amplitude_pct': tick_amplitude_pct,
            'tick_momentum': tick_momentum,
            '1min_amplitude': vol_metrics.get('1min_amplitude', 0),
            'tp_target_pct': tp_pct,
            'bid_depth': bid_depth,
            'ask_depth': ask_depth,
            'required_eth': required_eth,
            'predicted_direction': 'NONE',  # 由 scorer 决定
            'current_price': current_price,
        }

        score_result = self.scorer.score(scorer_params)

        self._last_snapshot = {
            'volatility': vol_metrics,
            'liquidity': liq_metrics,
            'score': score_result,
            '_scorer_params': scorer_params,  # 内部用，TUI不显示
        }

    # ─── 对外接口 ───────────────────────────────────────────────

    def get_snapshot(self) -> dict:
        if self._last_snapshot is None:
            self._update_snapshot()
        return self._last_snapshot

    def get_quality_score(self) -> int:
        snapshot = self.get_snapshot()
        return snapshot['score']['total_score']

    def get_recommendation(self) -> str:
        snapshot = self.get_snapshot()
        return snapshot['score']['recommendation']

    def get_signal_emoji(self) -> str:
        snapshot = self.get_snapshot()
        return snapshot['score']['signal_emoji']

    def get_signal_color(self) -> str:
        snapshot = self.get_snapshot()
        return snapshot['score']['signal_color']

    def check_alerts(self) -> list:
        alerts = []
        vol = self.volatility.get_metrics()
        liq = self.liquidity.get_metrics()

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

        if '🔴' in liq['spread_status']:
            alerts.append({
                'type': 'spread_high',
                'message': f"价差率过高：{liq['spread_rate']:.6f}%",
                'level': 'critical'
            })

        if '🔴' in liq['depth_status']:
            alerts.append({
                'type': 'depth_low',
                'message': f"订单簿深度不足：{liq['depth_surface']:.0f} ETH",
                'level': 'critical'
            })

        return alerts

    def get_display_data(self) -> dict:
        snapshot = self.get_snapshot()
        vol = snapshot['volatility']
        liq = snapshot['liquidity']
        score = snapshot['score']

        # 方向指示
        direction = score.get('direction', 'NONE')
        direction_label = ''
        if direction == 'LONG':
            direction_label = '→ 看多'
        elif direction == 'SHORT':
            direction_label = '→ 看空'
        else:
            direction_label = '→ 无方向'

        confidence = score.get('confidence', 0)
        category_scores = score.get('category_scores', {})

        return {
            'volatility_lines': [
                f"上根K线：{vol['1min_amplitude']:.3f}% {vol['1min_status']}",
                f"5 分钟：{vol['5min_amplitude']:.3f}%",
                f"1 小时：{vol['1h_amplitude']:.3f}% {vol['1h_status']}",
                f"变化率：{vol['change_rate']:.2f} {vol['change_rate_status']}",
            ],
            'liquidity_lines': [
                f"价差：{float(liq['spread']):.4f} USDC",
                f"价差率：{liq['spread_rate']:.6f}% {liq['spread_status']}",
            ],
            'score_display': {
                'emoji': score.get('signal_emoji', '🟡'),
                'color': score.get('signal_color', 'yellow'),
                'recommendation': score.get('recommendation', '观望'),
                'score': score.get('total_score', 0),
                'direction': direction,
                'direction_label': direction_label,
                'confidence': confidence,
                'category_scores': category_scores,
            },
            'alerts': self.check_alerts()
        }
