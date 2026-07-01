# -*- coding: utf-8 -*-
"""
v1.10.0 Web 服务 — 移动端 Web UI 后端
HTTP + WebSocket 服务，内嵌于现有 asyncio 事件循环
"""

import asyncio
import json
import os
import secrets
import socket
import time
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Set

import aiohttp
from aiohttp import web

# 数据目录（用于历史K线查询）
DATA_DIR = Path(__file__).parent.parent.parent / 'data' / 'klines'


class DecimalEncoder(json.JSONEncoder):
    """JSON 序列化 Decimal → float，拦截 Infinity/NaN"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.strftime('%H:%M:%S')
        return super().default(obj)

    def encode(self, o):
        return super().encode(_sanitize(o))


def _sanitize(obj):
    """递归替换 float('inf') / float('nan') → None，避免 JSON 序列化非法值"""
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return None
        if obj == float('inf'):
            return None
        if obj == float('-inf'):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _fmt_d(v):
    """Decimal → float，None → 0"""
    if v is None:
        return 0
    if isinstance(v, Decimal):
        return float(v)
    return v


def _fmt_pct(v):
    """Decimal 百分比 → float 保留4位"""
    if v is None:
        return 0
    return round(float(v), 4)


def _fmt_dt_str(dt):
    """datetime → 字符串，同天 HH:MM，昨天 昨天 HH:MM，其余 MM-DD HH:MM"""
    if dt is None:
        return ''
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime('%H:%M')
    if dt.date() == (now.date() - timedelta(days=1)):
        return f'昨天 {dt.strftime("%H:%M")}'
    return dt.strftime('%m-%d %H:%M')


def _fmt_duration(sec: int) -> str:
    """秒 → 可读时长：3天 12:05:30 / 00:10:41 / 00:00:15"""
    if sec < 0:
        return ''
    if sec == 0:
        return '00:00:00'
    days, r = divmod(sec, 86400)
    h, r2 = divmod(r, 3600)
    m, s = divmod(r2, 60)
    ts = f'{h:02d}:{m:02d}:{s:02d}'
    return f'{days}天 {ts}' if days > 0 else ts


class WebServer:
    """移动端 Web 服务"""

    def __init__(self, trader, config: dict, log_manager=None, token: str = '', app=None):
        self.trader = trader
        self.app = app  # v1.10.0: LiveTradingBot 引用，复用 TUI 操作方法
        self.config = config
        self.log = log_manager
        self.host = config.get('web_ui', {}).get('host', '0.0.0.0')
        self.port = config.get('web_ui', {}).get('port', 8099)

        # 认证 token：优先使用传入 token，否则随机生成
        self.token = token or secrets.token_hex(16)

        # WebSocket 客户端管理
        self._ws_clients: Set[web.WebSocketResponse] = set()

        # 事件队列（增量推送）
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # 订单簿数据（由外部更新，线程安全由 asyncio 保证）
        self._depth_snapshot: dict = {}

        # K 线数据（由外部更新）
        self._klines_cache: list = []

        # Taker ratio 数据（由外部更新）
        self._taker_ratio: dict = {'buy_pct': 50.0, 'sell_pct': 50.0, 'trade_count': 0, 'last_update': 0}

        # 深度压力采样（100ms 间隔，5 分钟窗口）
        self._dp_buy_ts: list = []  # [(timestamp, count), ...]
        self._dp_sell_ts: list = []
        self._dp_last_sample: float = 0
        self._depth_pressure_samples: int = 0

        # 历史行情 WS 健康状态
        self._market_ws_healthy: bool = True

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    # ─── 事件推送 ─────────────────────────────────────────────

    def push_event(self, event_type: str, detail: str):
        """推送增量事件到所有 WebSocket 客户端"""
        try:
            self._event_queue.put_nowait({
                'type': 'event',
                'event': event_type,
                'detail': detail,
                'ts': int(time.time() * 1000)
            })
        except asyncio.QueueFull:
            pass  # 丢弃旧事件，避免阻塞

    def update_depth(self, bids: list, asks: list):
        """更新订单簿快照（由行情回调调用）"""
        if not bids or not asks:
            return
        spread = float(asks[0][0]) - float(bids[0][0])
        mid = (float(asks[0][0]) + float(bids[0][0])) / 2
        spread_pct = (spread / mid * 100) if mid > 0 else 0
        bid_total = sum(float(b[1]) for b in bids[:8])
        ask_total = sum(float(a[1]) for a in asks[:8])
        total = bid_total + ask_total
        pressure_pct = (bid_total / total * 100) if total > 0 else 50
        if pressure_pct > 53:
            pressure = 'buy'
        elif pressure_pct < 47:
            pressure = 'sell'
        else:
            pressure = 'balanced'
        # 最新成交价（优先于买卖中点）
        trade_price = float(getattr(self.trader, 'last_trade_price', None) or 0) if getattr(self.trader, 'last_trade_price', None) else None
        self._depth_snapshot = {
            'type': 'depth',
            'ts': int(time.time() * 1000),
            'bids': [[str(b[0]), str(b[1])] for b in bids[:8]],
            'asks': [[str(a[0]), str(a[1])] for a in asks[:8]],
            'spread': round(spread, 4),
            'spread_pct': round(spread_pct, 6),
            'pressure': pressure,
            'pressure_pct': round(pressure_pct, 1),
            'trade_price': trade_price
        }

    def update_taker_ratio(self, buy_pct: float, sell_pct: float, trade_count: int = 0, last_update: float = 0):
        """更新主动成交比率"""
        self._taker_ratio = {
            'buy_pct': round(buy_pct, 2),
            'sell_pct': round(sell_pct, 2),
            'trade_count': trade_count,
            'last_update': last_update
        }

    def update_klines_cache(self, klines: list):
        """更新 K 线缓存"""
        self._klines_cache = klines

    def _get_price_range(self, current_price: float) -> tuple:
        """从 PriceRangeTracker 读取最高/最低价"""
        t = self.trader
        if t.indicators and hasattr(t.indicators, 'price_range'):
            pr = t.indicators.price_range
            high = pr.get_high()
            low = pr.get_low()
            if high is not None and low is not None:
                return round(high, 2), round(low, 2)
        return current_price, current_price

    def _sample_depth_pressure(self, imbalance: float):
        """深度压力采样（与 TUI DepthPressureTracker 逻辑一致）"""
        now = time.time()
        if now - self._dp_last_sample < 0.1:
            return
        self._dp_last_sample = now
        if imbalance > 0.15:
            self._dp_buy_ts.append(now)
        elif imbalance < -0.15:
            self._dp_sell_ts.append(now)
        self._prune_dp(now)

    def _prune_dp(self, now: float):
        cutoff = now - 300  # 5 分钟窗口
        while self._dp_buy_ts and self._dp_buy_ts[0] < cutoff:
            self._dp_buy_ts.pop(0)
        while self._dp_sell_ts and self._dp_sell_ts[0] < cutoff:
            self._dp_sell_ts.pop(0)

    def _get_depth_pressure_ratio(self) -> tuple:
        """返回 (buy_pct, sell_pct)"""
        now = time.time()
        self._prune_dp(now)
        b, s = len(self._dp_buy_ts), len(self._dp_sell_ts)
        self._depth_pressure_samples = b + s
        total = b + s
        if total == 0:
            return 50.0, 50.0
        return b / total * 100, s / total * 100

    # ─── 状态快照构建 ─────────────────────────────────────────

    def _build_state(self) -> dict:
        """构建完整状态快照"""
        t = self.trader
        cfg = t.config_manager
        indicators = t.indicators

        # 价格 — 优先使用成交价，降级为买一价
        ltp = getattr(t, 'last_trade_price', None)
        price = float(ltp or t.last_price) if (ltp or t.last_price) else 0

        # ATR14
        atr14 = 0
        atr14_pct = 0
        atr14_percentile = 0
        atr14_ref = 'normal'
        if indicators:
            vol = indicators.volatility
            atr14 = round(vol.get_atr14(), 6) if vol.get_atr14() is not None else 0
            atr14_pct = round(vol.get_atr14_pct(), 4) if vol.get_atr14_pct() is not None else 0
            # 读取已计算的百分位和评价
            pct_history = getattr(vol, '_atr14_percentile_history', None)
            hist_len = len(pct_history) if pct_history else 0
            atr14_percentile = getattr(vol, '_atr14_percentile', 0)
            atr14_ref = getattr(vol, '_atr14_ref', 'normal')
            last_recompute = getattr(vol, '_atr14_last_recompute', 0)
            # 自愈逻辑：
            # 1. 初始状态（百分位=0且ref=normal）→ 触发重算或初始化
            if atr14_percentile == 0 and atr14_ref == 'normal':
                if hist_len > 0:
                    vol.recompute_atr14_percentile()
                elif hist_len == 0 and atr14_pct > 0:
                    vol.track_atr14_percentile()
                atr14_percentile = getattr(vol, '_atr14_percentile', 0)
                atr14_ref = getattr(vol, '_atr14_ref', 'normal')
            # 2. 兜底：距上次全量重算超过 1 小时，且有足够历史数据 → 触发重算
            elif hist_len >= 60 and last_recompute > 0 and (time.time() - last_recompute) >= 3600:
                vol.recompute_atr14_percentile()
                atr14_percentile = getattr(vol, '_atr14_percentile', 0)
                atr14_ref = getattr(vol, '_atr14_ref', 'normal')

        # 振幅 / 波动率 / 加速度
        amp_1m = 0
        amp_5m = 0
        amp_1h = 0
        amp_1m_status = '--'
        amp_1h_status = '--'
        amp_change_rate = 0
        amp_change_status = '--'
        if indicators:
            vol_m = indicators.volatility.get_metrics()
            amp_1m = round(vol_m.get('1min_amplitude', 0), 4)
            amp_5m = round(vol_m.get('5min_amplitude', 0), 4)
            amp_1h = round(vol_m.get('1h_amplitude', 0), 4)
            amp_1m_status = vol_m.get('1min_status', '--')
            amp_1h_status = vol_m.get('1h_status', '--')
            amp_change_rate = round(vol_m.get('change_rate', 0), 4)
            amp_change_status = vol_m.get('change_rate_status', '--')

        # 综合评分 + 流动性深度
        score = None
        liq_bid_depth = 0
        liq_ask_depth = 0
        liq_total_depth = 0
        depth_pressure_buy = 50.0
        depth_pressure_sell = 50.0
        depth_pressure_samples = 0
        if indicators:
            snap = indicators.get_snapshot()
            sc = snap.get('score', {})
            score = {
                'total': sc.get('total_score', 0),
                'direction': sc.get('direction', 'NONE'),
                'direction_label': '看多' if sc.get('direction') == 'LONG' else ('看空' if sc.get('direction') == 'SHORT' else '观望'),
                'confidence': round(sc.get('confidence', 0) * 100),
                'recommendation': sc.get('recommendation', '--'),
                'emoji': sc.get('signal_emoji', '🟡'),
                'color': sc.get('signal_color', 'yellow'),
                'trend': round(sc.get('category_scores', {}).get('trend', 0), 1),
                'volatility': round(sc.get('category_scores', {}).get('volatility', 0), 1),
                'depth': round(sc.get('category_scores', {}).get('depth', 0), 1),
            }
            liq = snap.get('liquidity', {})
            liq_bid_depth = round(float(liq.get('bid_depth_surface', 0)), 2)
            liq_ask_depth = round(float(liq.get('ask_depth_surface', 0)), 2)
            liq_total_depth = liq_bid_depth + liq_ask_depth
            # 滚动采样深度压力比
            if liq_total_depth > 0:
                imbalance = (liq_bid_depth - liq_ask_depth) / liq_total_depth
                self._sample_depth_pressure(imbalance)
            bp, sp = self._get_depth_pressure_ratio()
            depth_pressure_buy = round(bp, 2)
            depth_pressure_sell = round(sp, 2)
            depth_pressure_samples = self._depth_pressure_samples

        # 价格范围（从 PriceRangeTracker 读取，由 ticker 回调实时更新 + 启动时回填历史）
        pr_minutes = cfg.get('price_range.minutes', 30) if cfg else 30
        pr_high, pr_low = self._get_price_range(price)

        # 持仓
        position = None
        if t.batch_state and t.batch_state.get('enabled') and not t.batch_state.get('round_closed'):
            # 分批模式
            batches = t.batch_state.get('batches', [])
            filled = [b for b in batches if b.get('status') in ('filled', 'tp_placed', 'tp_closed')]
            if filled:
                total_size = sum(b['size'] for b in filled)
                total_entry_value = sum(b['price'] * b['size'] for b in filled)
                wavg = total_entry_value / total_size if total_size > 0 else Decimal('0')
                side = t.batch_state.get('side', 'LONG')
                if side == 'LONG':
                    fp = (Decimal(str(price)) - wavg) * total_size if price else Decimal('0')
                else:
                    fp = (wavg - Decimal(str(price))) * total_size if price else Decimal('0')
                # 分批模式取首笔成交时间作为开仓时间
                first_fill = None
                for b in filled:
                    ft = b.get('fill_time')
                    if ft:
                        first_fill = first_fill if first_fill and first_fill < ft else ft
                position = {
                    'side': side,
                    'size': float(total_size),
                    'entry_weighted_avg': float(wavg),
                    'floating_pnl': float(fp),
                    'peak_floating_profit': float(getattr(t, '_max_float_pnl', 0) or 0),
                    'peak_floating_loss': float(getattr(t, '_min_float_pnl', 0) or 0),
                    'peak_floating_profit_price': float(getattr(t, '_max_float_pnl_price', 0) or 0),
                    'peak_floating_loss_price': float(getattr(t, '_min_float_pnl_price', 0) or 0),
                    'open_time': first_fill.isoformat() if first_fill else None
                }
        elif t.position:
            side = t.position.get('side', 'NONE')
            entry = float(t.position.get('entry_price', 0))
            size = float(t.position.get('size', 0))
            if side == 'LONG':
                fp = (price - entry) * size if price and entry else 0
            else:
                fp = (entry - price) * size if price and entry else 0
            pos_time = t.position.get('time')
            position = {
                'side': side,
                'size': size,
                'entry_weighted_avg': entry,
                'floating_pnl': round(fp, 4),
                'peak_floating_profit': float(getattr(t, '_max_float_pnl', 0) or 0),
                'peak_floating_loss': float(getattr(t, '_min_float_pnl', 0) or 0),
                'peak_floating_profit_price': float(getattr(t, '_max_float_pnl_price', 0) or 0),
                'peak_floating_loss_price': float(getattr(t, '_min_float_pnl_price', 0) or 0),
                'open_time': pos_time.isoformat() if pos_time else None
            }

        # 挂单列表
        pending_orders = []
        tp_orders = []
        if t.batch_state and t.batch_state.get('enabled'):
            for b in t.batch_state.get('batches', []):
                if b.get('status') == 'pending':
                    pending_orders.append({
                        'index': b['index'] + 1,
                        'price': float(b['price']),
                        'size': float(b['size']),
                        'status': 'pending'
                    })
                elif b.get('status') in ('filled', 'tp_placed'):
                    tp_orders.append({
                        'index': b['index'] + 1,
                        'entry': float(b['price']),
                        'tp_price': float(b.get('tp_price') or 0),
                        'size': float(b['size'])
                    })
        else:
            if t.pending_order:
                pending_orders.append({
                    'index': 1,
                    'price': float(t.pending_order.get('price', 0)),
                    'size': float(t.pending_order.get('size', 0)),
                    'status': 'pending'
                })
            if t.tp_order:
                tp_orders.append({
                    'index': 1,
                    'entry': float(t.position.get('entry_price', 0)) if t.position else 0,
                    'tp_price': float(t.tp_order.get('price', 0)),
                    'size': float(t.tp_order.get('size', 0))
                })

        # 风控
        risk = {
            'sl_price': 0,
            'sm_price': 0,
            'loss_protection_active': False,
            'loss_protection': None,
            'trailing_stop': None
        }
        if t.batch_state and t.batch_state.get('enabled'):
            if t.batch_state.get('sl_order_id'):
                risk['sl_price'] = float(t.batch_state.get('sl_price', 0) or 0)
            if t.batch_state.get('sm_order_id'):
                risk['sm_price'] = float(t.batch_state.get('sm_price', 0) or 0)
        else:
            if t.sl_order:
                risk['sl_price'] = float(t.sl_order.get('trigger', 0))
            if t.stop_market_order:
                risk['sm_price'] = float(t.stop_market_order.get('trigger', 0))
        # 浮亏保护详情
        if t.loss_protection_manager:
            lp_status = t.loss_protection_manager.get_status()
            risk['loss_protection'] = lp_status
            risk['loss_protection_active'] = lp_status.get('status', '未启用') in ('已保护',)
            risk['loss_protection_status'] = lp_status.get('status', '未启用')
        # 移动止损详情
        ts_mgr = getattr(t, 'trailing_stop_manager', None)
        if ts_mgr and ts_mgr.enabled and ts_mgr.entry_price and ts_mgr.grid_prices:
            p = Decimal(str(price)) if price else Decimal('0')
            cl = ts_mgr._get_current_level(p) if price else 0
            risk['trailing_stop'] = {
                'enabled': True,
                'grid_count': ts_mgr.grid_count,
                'grid_prices': [float(gp) for gp in ts_mgr.grid_prices],
                'max_level_reached': ts_mgr.max_level_reached,
                'current_level': cl,
                'entry_price': float(ts_mgr.entry_price)
            }
        else:
            risk['trailing_stop'] = {'enabled': False}

        # 24h 统计 / 统计周期
        stats_period_cfg = cfg.get('stats_period', {}) if cfg else {}
        stats_period = {
            'mode': stats_period_cfg.get('mode', '24h'),
            'timezone': stats_period_cfg.get('timezone', '+8')
        }
        s24 = t.trade_stats_24h or {}
        total_fee_val = float(s24.get('total_fee', 0) or 0)
        stats_24h = {
            'round_count': s24.get('round_count', 0),
            'win_count': s24.get('win_count', 0),
            'win_rate': s24.get('win_rate', 0),
            'total_volume': float(s24.get('total_volume', 0) or 0),
            'total_pnl': float(s24.get('total_pnl', 0) or 0),
            'total_fee': total_fee_val,
            'avg_pnl_ratio': s24.get('avg_pnl_ratio', 0),
            'avg_hold_time': s24.get('avg_hold_time', '--'),
            'expected_value': s24.get('expected_value', 0)
        }

        # 上一笔持仓（仅取最近完全平仓，无已平仓则不显示）
        last_position = None
        if t.position_history:
            # position_history 按 (status_order, -last_action_time_ms) 排序
            # 完全平仓 在最前的是最近平仓的，取第一个
            closed = [p for p in t.position_history if p.get('status') == '完全平仓']
            if closed:
                last = closed[0]
                close_avg = last.get('close_avg_price')
                close_time = last.get('close_time')
                last_position = {
                    'side': last.get('side', 'NONE'),
                    'status': last.get('status', ''),
                    'entry': float(last.get('open_avg_price', 0)),
                    'exit': float(close_avg) if close_avg is not None else 0,
                    'size': float(last.get('max_size', 0)),
                    'closed_size': float(last.get('closed_size', 0)),
                    'pnl': float(last.get('pnl', 0)),
                    'fee': float(last.get('total_fee', 0)),
                    'funding': float(last.get('funding_fee', 0)),
                    'net_pnl': float(last.get('pnl', 0) or 0),
                    'exit_type': last.get('exit_type', 'MANUAL') if last.get('status') != '未平仓' else '',
                    'open_time': _fmt_dt_str(last.get('open_time')),
                    'close_time': _fmt_dt_str(close_time) if close_time else '',
                    'duration': _fmt_duration(int(last.get('duration', 0) or 0))
                }

        # 连接状态
        ws_healthy = getattr(t, '_ws_health', {})
        connection = {
            'ws_healthy': ws_healthy.get('market', True) if ws_healthy else self._market_ws_healthy,
            'user_stream_connected': ws_healthy.get('user_stream_connected', False) if ws_healthy else False,
            'user_stream_last_msg_ts': ws_healthy.get('user_stream_last_msg_ts', 0) if ws_healthy else 0,
            'user_stream_msg_count': ws_healthy.get('user_stream_msg_count', 0) if ws_healthy else 0,
            'user_stream_restart_count': ws_healthy.get('user_stream_restart_count', 0) if ws_healthy else 0,
        }

        # v1.10.0: 诊断 - WebSocket 事件类型
        diag = {}
        listener = getattr(t, 'listener', None)
        if listener:
            diag['seen_event_types'] = list(getattr(listener, '_seen_event_types', set()))
            diag['agg_trade_count'] = getattr(listener, '_agg_trade_count', 0)

        lp = t.loss_protection_manager
        return {
            'type': 'state',
            'ts': int(time.time() * 1000),
            '_diag': diag,
            'price': price,
            'atr14': atr14,
            'atr14_pct': atr14_pct,
            'atr14_percentile': atr14_percentile,
            'atr14_ref': atr14_ref,
            'amplitude_1m': amp_1m,
            'amplitude_5m': amp_5m,
            'amplitude_1h': amp_1h,
            'amplitude_1m_status': amp_1m_status,
            'amplitude_1h_status': amp_1h_status,
            'amplitude_change_rate': amp_change_rate,
            'amplitude_change_status': amp_change_status,
            'available_balance': float(t.available_balance) if t.available_balance else 0,
            'margin_used': float(getattr(t, 'position_margin', 0) or 0) + float(getattr(t, 'order_margin', 0) or 0),
            'privacy_baseline': float(getattr(t, '_privacy_baseline', 0) or 0),
            'api_leverage': cfg.get('leverage.api', 100) if cfg else 100,
            'actual_leverage': cfg.get('leverage.actual', 25) if cfg else 25,
            'price_range_high': pr_high,
            'price_range_low': pr_low,
            'price_range_minutes': pr_minutes,
            'position': position,
            'pending_orders': pending_orders,
            'tp_orders': tp_orders,
            'risk': risk,
            'stats_24h': stats_24h,
            'stats_period': stats_period,
            'last_position': last_position,
            'taker_ratio': self._taker_ratio,
            'score': score,
            'liq_bid_depth': liq_bid_depth,
            'liq_ask_depth': liq_ask_depth,
            'depth_pressure_buy': depth_pressure_buy,
            'depth_pressure_sell': depth_pressure_sell,
            'depth_pressure_samples': depth_pressure_samples,
            'connection': connection,
            # v1.10.0: 下单按钮状态所需 + 提前平仓价格（K线标记用）
            'early_close_order': t.early_close_order is not None,
            'early_close_price': float(t.early_close_order.get('price', 0)) if t.early_close_order else 0,
            # 浮亏保护订单（非分批模式 — K线标记用）
            'lp_orders': {
                'breakeven_stop_price': float(getattr(lp, '_breakeven_stop_price', 0) or 0) if lp else 0,
                'grid1_stop_price': float(getattr(lp, '_grid1_stop_price', 0) or 0) if lp else 0,
            } if lp else None,
            'batch_state': {
                'enabled': t.batch_state.get('enabled', False) if t.batch_state else False,
                'state': t.batch_state.get('state', 'idle') if t.batch_state else 'idle',
                'round_closed': t.batch_state.get('round_closed', True) if t.batch_state else True,
                'early_close_order_id': t.batch_state.get('early_close_order_id') if t.batch_state else None,
                'early_close_price': float(t.batch_state.get('early_close_price', 0)) if t.batch_state else 0,
                'supplement_blocked': t.batch_state.get('supplement_blocked', False) if t.batch_state else False,
                'total_filled_size': float(t.batch_state.get('total_filled_size', 0)) if t.batch_state else 0,
                'total_count': t.batch_state.get('total_count', 0) if t.batch_state else 0,
                'side': t.batch_state.get('side', '') if t.batch_state else '',
                # v1.10.1: 浮亏保护订单（K线标记用）
                'lp_limit_price': float(t.batch_state.get('lp_limit_price', 0)) if t.batch_state else 0,
                'lp_limit_order_id': t.batch_state.get('lp_limit_order_id') if t.batch_state else None,
                'lp_stop_price': float(t.batch_state.get('lp_stop_price', 0)) if t.batch_state else 0,
                'lp_stop_algo_id': t.batch_state.get('lp_stop_algo_id') if t.batch_state else None,
            } if t.batch_state else None,
        }

    def _build_kline_tick(self) -> dict:
        """构建当前 K 线 tick（从 listener 取实时合成数据，每笔成交都会更新）"""
        t = self.trader
        # 优先从 BinanceListener 读取实时合成的 K 线（每笔成交更新 OHLCV）
        listener = getattr(t, 'listener', None)
        if listener and listener.current_kline:
            kline = listener.current_kline
        else:
            # 降级：从 volatility 读取（仅 K 线闭合时更新）
            if t.indicators:
                vol = t.indicators.volatility
                kline = vol.current_kline if hasattr(vol, 'current_kline') else None
            else:
                kline = None
        if not kline:
            return None
        now_ms = int(time.time() * 1000)
        kline_ts = kline.get('timestamp', 0)
        is_closed = (now_ms - kline_ts) >= 60000
        return {
            'type': 'kline_tick',
            'interval': '1m',
            't': kline_ts,
            'o': float(kline.get('open', 0)),
            'h': float(kline.get('high', 0)),
            'l': float(kline.get('low', 0)),
            'c': float(kline.get('close', 0)),
            'v': float(kline.get('volume', 0)),
            'is_closed': is_closed
        }

    # ─── HTTP 处理器 ──────────────────────────────────────────

    def _check_auth(self, request: web.Request) -> bool:
        """验证 token"""
        token = request.query.get('token') or request.headers.get('X-Token', '')
        return token == self.token

    async def _handle_index(self, request: web.Request) -> web.Response:
        """首页"""
        if not self._check_auth(request):
            return self._error_page()
        html_path = Path(__file__).parent / 'static' / 'index.html'
        if html_path.exists():
            return web.FileResponse(html_path, headers={'Cache-Control': 'no-cache, no-store, must-revalidate'})
        return web.Response(text='index.html not found', status=500)

    def _error_page(self) -> web.Response:
        """Token 错误页"""
        html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>认证失败</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0b0e11;color:#eaecef;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;overflow:hidden}
.err-box{text-align:center;padding:32px}
.err-icon{font-size:64px;margin-bottom:16px}
.err-title{font-size:20px;font-weight:700;margin-bottom:8px;color:#eaecef}
.err-desc{font-size:14px;color:#848e9c;margin-bottom:24px;line-height:1.6}
.err-actions{display:flex;gap:12px;justify-content:center}
.btn{padding:10px 24px;border-radius:6px;font-size:14px;font-weight:600;border:none;cursor:pointer}
.btn-retry{background:#fcd535;color:#0b0e11}
.btn-copy{background:#2b3139;color:#eaecef}
</style>
</head>
<body>
<div class="err-box">
<div class="err-icon">&#9888;</div>
<div class="err-title">认证失败</div>
<div class="err-desc">Token 无效或已过期<br>请检查 URL 中的 token 参数<br>或在 PC 终端查看正确的连接地址</div>
<div class="err-actions">
<button class="btn btn-retry" onclick="location.reload()">重试</button>
<button class="btn btn-copy" onclick="navigator.clipboard.writeText(location.href);this.textContent='已复制'">复制地址</button>
</div>
</div>
</body>
</html>'''
        return web.Response(text=html, content_type='text/html; charset=utf-8', status=403)

    async def _handle_state(self, request: web.Request) -> web.Response:
        """HTTP 状态快照（轮询备用）"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            state = self._build_state()
            return web.json_response(state, dumps=lambda o: json.dumps(o, cls=DecimalEncoder))
        except Exception as e:
            if self.log:
                self.log.error(f'[Web] /api/state 异常: {e}', exc_info=True)
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_klines(self, request: web.Request) -> web.Response:
        """K 线数据 API（支持 interval 聚合）— 兜底链: 内存deque → 文件 → 币安API → 回写"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            limit = int(request.query.get('limit', '120'))
        except ValueError:
            limit = 120
        limit = min(limit, 1440)
        interval = request.query.get('interval', '1m')
        factor = {'1m': 1, '5m': 5, '15m': 15, '1h': 60}.get(interval, 1)

        # Layer 1: 内存 deque
        raw_klines = await self._read_memory_klines()
        need = limit * factor

        # Layer 2-3: 检测时间缺口 → 文件补 → API 补 → 回写
        raw_klines = await self._fill_kline_gaps(raw_klines, need)

        # 聚合为指定周期
        result = []
        if factor == 1:
            src = raw_klines[-limit:]
            for k in src:
                result.append({
                    't': k.get('timestamp', 0),
                    'o': float(k.get('open', 0)),
                    'h': float(k.get('high', 0)),
                    'l': float(k.get('low', 0)),
                    'c': float(k.get('close', 0)),
                    'v': float(k.get('volume', 0))
                })
        else:
            src = raw_klines[-need:] if len(raw_klines) > need else raw_klines
            i = 0
            while i + factor <= len(src):
                group = src[i:i + factor]
                o_val = float(group[0].get('open', 0))
                h_val = max(float(k.get('high', 0)) for k in group)
                l_val = min(float(k.get('low', 0)) for k in group)
                c_val = float(group[-1].get('close', 0))
                v_val = sum(float(k.get('volume', 0)) for k in group)
                t_first = group[0].get('timestamp', 0)
                result.append({'t': t_first, 'o': o_val, 'h': h_val, 'l': l_val, 'c': c_val, 'v': v_val})
                i += factor
            result = result[-limit:]

        return web.json_response(result)

    async def _read_memory_klines(self) -> list:
        """从 volatility deque 读取 K 线（含 current_kline）"""
        raw = []
        if self.trader.indicators:
            vol = self.trader.indicators.volatility
            kline_deque = vol._klines if hasattr(vol, '_klines') else None
            if kline_deque:
                raw = list(kline_deque)
            cur = vol.current_kline if hasattr(vol, 'current_kline') else None
            if cur and cur.get('timestamp'):
                if not raw:
                    raw = [cur]
                else:
                    last_ts = raw[-1].get('timestamp', 0) if raw else 0
                    if cur.get('timestamp', 0) != last_ts:
                        raw.append(cur)
        return raw

    async def _fill_kline_gaps(self, raw_klines: list, need: int) -> list:
        """检测时间缺口 → 文件补 → API 补 → 回写 deque + 文件"""
        if len(raw_klines) < 2:
            return raw_klines

        raw_klines.sort(key=lambda x: x.get('timestamp', 0))
        existing_ts = {k.get('timestamp') for k in raw_klines}

        # 检测缺口: 相邻两根间隔 > 60s
        gaps = set()
        for i in range(len(raw_klines) - 1):
            diff = raw_klines[i+1].get('timestamp', 0) - raw_klines[i].get('timestamp', 0)
            if diff > 60000:
                base = raw_klines[i].get('timestamp', 0)
                missing = (diff // 60000) - 1
                for j in range(1, missing + 1):
                    gap_ts = base + j * 60000
                    if gap_ts not in existing_ts:
                        gaps.add(gap_ts)

        if not gaps:
            return raw_klines

        filled = []  # 新填补的 entry 列表
        still_missing = set()

        # Layer 2: 文件兜底
        try:
            from pathlib import Path
            from datetime import datetime as _dt
            data_dir = Path(__file__).parent.parent.parent / 'data' / 'klines' / self.trader.symbol
            today = _dt.now().strftime('%Y-%m-%d')
            file_path = data_dir / f'{today}.jsonl'
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            k = json.loads(line.strip())
                            ts = k.get('timestamp')
                            if ts in gaps:
                                entry = {
                                    'timestamp': ts,
                                    'open': float(k.get('open', 0)),
                                    'high': float(k.get('high', 0)),
                                    'low': float(k.get('low', 0)),
                                    'close': float(k.get('close', 0)),
                                    'volume': float(k.get('volume', 0))
                                }
                                filled.append(entry)
                                existing_ts.add(ts)
                        except Exception:
                            pass
        except Exception:
            pass

        filled_ts = {e['timestamp'] for e in filled}
        still_missing = gaps - filled_ts

        # Layer 3: 币安 API 兜底
        if still_missing:
            try:
                from_ts = min(still_missing) - 120000
                to_ts = max(still_missing) + 120000
                api_klines = await asyncio.to_thread(
                    self.trader.api.get_klines, self.trader.symbol, '1m',
                    startTime=from_ts, endTime=to_ts, limit=1000
                )
                if api_klines:
                    for k in api_klines:
                        ts = k[0] if isinstance(k, list) else k.get('timestamp', 0)
                        if ts in still_missing and ts not in existing_ts:
                            if isinstance(k, list):
                                entry = {'timestamp': ts, 'open': float(k[1]), 'high': float(k[2]),
                                         'low': float(k[3]), 'close': float(k[4]), 'volume': float(k[5])}
                            else:
                                entry = {'timestamp': ts, 'open': float(k.get('open', 0)),
                                         'high': float(k.get('high', 0)), 'low': float(k.get('low', 0)),
                                         'close': float(k.get('close', 0)), 'volume': float(k.get('volume', 0))}
                            filled.append(entry)
                            existing_ts.add(ts)
            except Exception as e:
                if self.log:
                    self.log.system.warning(f'[Web] K线API兜底失败: {e}')

        # 回写: 填入 deque + 追加到文件
        if filled:
            raw_klines.extend(filled)
            raw_klines.sort(key=lambda x: x.get('timestamp', 0))
            await self._writeback_klines(filled)

        return raw_klines

    async def _writeback_klines(self, entries: list):
        """将填补的 K 线回写到内存 deque 和当天 JSONL 文件"""
        if not entries:
            return
        # 写入内存 deque
        try:
            if self.trader.indicators:
                vol = self.trader.indicators.volatility
                kline_deque = vol._klines if hasattr(vol, '_klines') else None
                if kline_deque is not None:
                    existing = {k.get('timestamp') for k in kline_deque}
                    for e in sorted(entries, key=lambda x: x.get('timestamp', 0)):
                        if e.get('timestamp') not in existing:
                            kline_deque.append(e)
                            existing.add(e.get('timestamp'))
        except Exception:
            pass

        # 写入文件
        try:
            from pathlib import Path
            from datetime import datetime as _dt
            data_dir = Path(__file__).parent.parent.parent / 'data' / 'klines' / self.trader.symbol
            data_dir.mkdir(parents=True, exist_ok=True)
            today = _dt.now().strftime('%Y-%m-%d')
            file_path = data_dir / f'{today}.jsonl'
            file_ts = set()
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            k = json.loads(line.strip())
                            ts = k.get('timestamp')
                            if ts:
                                file_ts.add(ts)
                        except Exception:
                            pass
            with open(file_path, 'a', encoding='utf-8') as f:
                for e in sorted(entries, key=lambda x: x.get('timestamp', 0)):
                    ts = e.get('timestamp')
                    if ts and ts not in file_ts:
                        f.write(json.dumps({
                            'timestamp': ts,
                            'open': e.get('open'),
                            'high': e.get('high'),
                            'low': e.get('low'),
                            'close': e.get('close'),
                            'volume': e.get('volume')
                        }) + '\n')
                        file_ts.add(ts)
        except Exception:
            pass

    async def _handle_klines_history(self, request: web.Request) -> web.Response:
        """历史 K 线查询（按日期范围）"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)

        from_str = request.query.get('from', '')
        to_str = request.query.get('to', '')
        if not from_str or not to_str:
            return web.json_response({'error': 'missing from/to params'}, status=400)

        all_klines = []
        try:
            from_date = datetime.strptime(from_str, '%Y-%m-%d')
            to_date = datetime.strptime(to_str, '%Y-%m-%d')
        except ValueError:
            return web.json_response({'error': 'invalid date format, use YYYY-MM-DD'}, status=400)

        current = from_date
        while current <= to_date:
            date_str = current.strftime('%Y-%m-%d')
            file_path = DATA_DIR / self.trader.symbol / f'{date_str}.jsonl'
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                k = json.loads(line)
                                all_klines.append({
                                    't': k.get('timestamp', 0),
                                    'o': float(k.get('open', 0)),
                                    'h': float(k.get('high', 0)),
                                    'l': float(k.get('low', 0)),
                                    'c': float(k.get('close', 0)),
                                    'v': float(k.get('volume', 0))
                                })
                            except json.JSONDecodeError:
                                continue
            current += timedelta(days=1)

        # 按时间排序
        all_klines.sort(key=lambda k: k['t'])
        return web.json_response(all_klines)

    async def _handle_history(self, request: web.Request) -> web.Response:
        """历史持仓查询（懒加载）"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            try:
                offset = int(request.query.get('offset', '0'))
                limit = min(int(request.query.get('limit', '20')), 50)
            except ValueError:
                offset = 0
                limit = 20

            history = list(self.trader.position_history) if self.trader.position_history else []
            # 按 open_time 降序重排（最新在前，不按 status 分组；datetime 对象始终存在）
            history.sort(key=lambda x: x.get('open_time') or datetime.min, reverse=True)
            # 过滤 30 天内的数据
            cutoff = datetime.now() - timedelta(days=30)
            filtered = []
            for h in history:
                close_time_val = h.get('close_time')
                if isinstance(close_time_val, datetime):
                    close_dt = close_time_val
                elif isinstance(close_time_val, str) and close_time_val:
                    try:
                        close_dt = datetime.strptime(close_time_val, '%m-%d %H:%M')
                        close_dt = close_dt.replace(year=datetime.now().year)
                    except ValueError:
                        close_dt = None
                else:
                    close_dt = None
                if close_dt is None:
                    filtered.append(h)
                elif close_dt >= cutoff:
                    filtered.append(h)

            total = len(filtered)
            batch = filtered[offset:offset + limit]
            result = []
            for h in batch:
                close_avg = h.get('close_avg_price')
                close_time = h.get('close_time')
                fee = float(h.get('total_fee', 0))
                funding = float(h.get('funding_fee', 0))
                pnl = float(h.get('pnl', 0))
                status = h.get('status', '')
                # 退出类型（从 status 推导；TP/SL 需从外部补充，默认 MANUAL）
                if status == '未平仓':
                    exit_type = 'OPEN'
                elif status == '部分平仓':
                    exit_type = 'PARTIAL'
                else:
                    exit_type = 'MANUAL'
                # duration: 秒 → 可读格式
                dur = h.get('duration', 0) or 0
                dur_str = _fmt_duration(int(dur))
                result.append({
                    'side': h.get('side', 'NONE'),
                    'status': status,
                    'entry_price': float(h.get('open_avg_price', 0)),
                    'exit_price': float(close_avg) if close_avg is not None else 0,
                    'size': float(h.get('max_size', 0)),
                    'closed_size': float(h.get('closed_size', 0)),
                    'pnl': pnl,
                    'total_fee': fee,
                    'funding': funding,
                    'net_pnl': pnl,  # pnl 在 _pair_positions 已扣费+资费，不重复扣除
                    'exit_type': exit_type,
                    'open_time': _fmt_dt_str(h.get('open_time')),
                    'close_time': _fmt_dt_str(close_time) if close_time else '',
                    'duration': dur_str,
                })
            return web.json_response({'items': result, 'total': total, 'offset': offset, 'limit': limit})
        except Exception as e:
            if self.log:
                self.log.error(f'[Web] /api/history 异常: {e}', exc_info=True)
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_history_refresh(self, request: web.Request) -> web.Response:
        """触发从币安重新拉取历史持仓"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            await self.trader.fetch_position_history()
            return web.json_response({'ok': True})
        except Exception as e:
            if self.log:
                self.log.error(f'[Web] 刷新历史持仓失败: {e}', exc_info=True)
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_asset_curve(self, request: web.Request) -> web.Response:
        """资产曲线数据 — 用当前总资产 + 历史已平仓盈亏倒推"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            period = request.query.get('period', '1d')  # '1d' or '7d'

            stats_cfg = {}
            if self.trader.config_manager:
                stats_cfg = self.trader.config_manager.get_config().get('stats_period', {})
            mode = stats_cfg.get('mode', '24h')
            tz_str = stats_cfg.get('timezone', '+8')

            now = time.time()
            if period == '7d':
                if mode == 'calendar_day':
                    # 7个自然日（含今天）
                    offset_hours = float(tz_str)
                    from datetime import timezone as _tz_dt, timedelta as _td_dt
                    tz = _tz_dt(_td_dt(hours=offset_hours))
                    now_dt = datetime.fromtimestamp(now, tz=tz)
                    today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    start_dt = today_start - timedelta(days=6)
                    start_time = start_dt.timestamp()
                else:
                    start_time = now - 7 * 24 * 3600  # 168小时
                sample_interval_hours = 4
                period_hours = (now - start_time) / 3600
            else:  # 1d
                if mode == 'calendar_day':
                    offset_hours = float(tz_str)
                    from datetime import timezone as _tz_dt, timedelta as _td_dt
                    tz = _tz_dt(_td_dt(hours=offset_hours))
                    now_dt = datetime.fromtimestamp(now, tz=tz)
                    start_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                    start_time = start_dt.timestamp()
                else:
                    start_time = now - 24 * 3600
                sample_interval_hours = 1
                period_hours = (now - start_time) / 3600

            # 当前总资产（可用余额 + 未实现盈亏）
            current_assets = float(self.trader.available_balance or 0)
            position = self.trader.position
            last_price = self.trader.last_price
            if position and last_price and position.get('size', 0) > 0:
                size = float(position['size'])
                entry = float(position['entry_price'])
                price = float(last_price)
                if position['side'] == 'LONG':
                    unrealized = (price - entry) * size
                else:
                    unrealized = (entry - price) * size
                current_assets += unrealized

            # 收集已平仓持仓（在时间范围内的）
            history = list(self.trader.position_history) if self.trader.position_history else []
            closed_positions = []
            for h in history:
                ct = h.get('close_time')
                if ct and isinstance(ct, datetime):
                    ct_ts = ct.timestamp()
                    if start_time <= ct_ts <= now:
                        pnl = float(h.get('pnl', 0) or 0)
                        fee = float(h.get('total_fee', 0) or 0)
                        vol = float(h.get('max_size', 0) or 0)
                        dur = h.get('duration', 0) or 0
                        closed_positions.append({
                            'close_ts': ct_ts,
                            'net_pnl': pnl,
                            'fee': fee,
                            'volume': vol,
                            'duration': dur,
                        })

            # 按 close_time 降序排列
            closed_positions.sort(key=lambda p: p['close_ts'], reverse=True)

            # 计算时间段内交易统计
            stats = {}
            if closed_positions:
                round_count = len(closed_positions)
                win_pos = [p for p in closed_positions if p['net_pnl'] > 0]
                loss_pos = [p for p in closed_positions if p['net_pnl'] < 0]
                win_count = len(win_pos)
                loss_count = len(loss_pos)
                win_rate = round(win_count / round_count * 100, 1) if round_count > 0 else 0
                total_volume = round(sum(p['volume'] for p in closed_positions), 2)
                total_pnl = round(sum(p['net_pnl'] for p in closed_positions), 8)
                total_fee = round(sum(p['fee'] for p in closed_positions), 8)
                if win_count > 0 and loss_count > 0:
                    avg_win = sum(p['net_pnl'] for p in win_pos) / win_count
                    avg_loss = abs(sum(p['net_pnl'] for p in loss_pos)) / loss_count
                    avg_pnl_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0
                    ev = round(win_count / round_count * avg_win - loss_count / round_count * avg_loss, 8)
                elif win_count > 0:
                    avg_pnl_ratio = float('inf')
                    ev = round(sum(p['net_pnl'] for p in win_pos) / win_count, 8)
                elif loss_count > 0:
                    avg_pnl_ratio = 0
                    ev = round(-sum(p['net_pnl'] for p in loss_pos) / loss_count, 8)
                else:
                    avg_pnl_ratio = 0
                    ev = 0
                dur_list = [p['duration'] for p in closed_positions if p['duration'] > 0]
                if dur_list:
                    avg_dur_s = int(sum(dur_list) / len(dur_list))
                    hh = avg_dur_s // 3600; mm = (avg_dur_s % 3600) // 60; ss = avg_dur_s % 60
                    avg_hold_time = f"{hh:02d}:{mm:02d}:{ss:02d}"
                else:
                    avg_hold_time = '--'
                stats = {
                    'round_count': round_count,
                    'win_count': win_count,
                    'win_rate': win_rate,
                    'total_volume': total_volume,
                    'total_pnl': total_pnl,
                    'total_fee': total_fee,
                    'avg_pnl_ratio': None if avg_pnl_ratio == float('inf') else avg_pnl_ratio,
                    'avg_hold_time': avg_hold_time,
                    'expected_value': ev,
                }
            else:
                stats = {
                    'round_count': 0, 'win_count': 0, 'win_rate': 0,
                    'total_volume': 0, 'total_pnl': 0, 'total_fee': 0,
                    'avg_pnl_ratio': 0, 'avg_hold_time': '--', 'expected_value': 0,
                }

            # 自然日模式：从 start_time 正向生成整点锚点，填满到 24:00
            # 非自然日：从 now 倒退，保持原逻辑
            import math
            if mode == 'calendar_day':
                days = 1 if period == '1d' else 7
                end_time = start_time + days * 24 * 3600
                total_samples = int(days * 24 / sample_interval_hours) + 1
                sample_times_desc = []
                for i in range(total_samples - 1, -1, -1):
                    t = start_time + i * sample_interval_hours * 3600
                    sample_times_desc.append(t)
            else:
                num_intervals = int(math.ceil(period_hours / sample_interval_hours))
                sample_times_desc = []
                for i in range(num_intervals, -1, -1):
                    t = start_time + i * sample_interval_hours * 3600
                    if t > now:
                        continue
                    sample_times_desc.append(t)
                if not sample_times_desc or sample_times_desc[0] < now - 1:
                    sample_times_desc.insert(0, now)
            # 把平仓时间点也加入采样，让曲线尖尖可被触摸到
            merged = {}
            for t in sample_times_desc:
                merged[round(t)] = t  # 秒级去重
            max_t = (start_time + (1 if period == '1d' else 7) * 24 * 3600) if mode == 'calendar_day' else now
            for p in closed_positions:
                close_sec = round(p['close_ts'])
                if start_time <= close_sec <= max_t:
                    merged[close_sec] = p['close_ts']
            sample_times_desc = sorted(merged.values(), reverse=True)
            samples = []
            pos_idx = 0
            running_assets = current_assets
            for sample_time in sample_times_desc:
                # 减去在 sample_time 之后平仓的盈亏
                while pos_idx < len(closed_positions) and closed_positions[pos_idx]['close_ts'] > sample_time:
                    running_assets -= closed_positions[pos_idx]['net_pnl']
                    pos_idx += 1
                samples.append({
                    't': round(sample_time * 1000),
                    'v': round(running_assets, 8)
                })

            samples.reverse()  # 时间升序
            return web.json_response({'period': period, 'samples': samples, 'stats': stats, 'current_assets': round(current_assets, 8)})
        except Exception as e:
            if self.log:
                self.log.error(f'[Web] /api/history/asset-curve 异常: {e}', exc_info=True)
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_open(self, request: web.Request) -> web.Response:
        """开仓（做多/做空）"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid json'}, status=400)
        side = body.get('side', 'LONG')
        if side not in ('LONG', 'SHORT'):
            return web.json_response({'error': 'side must be LONG or SHORT'}, status=400)

        try:
            if self.log:
                self.log.info(f'[Web] 移动端操作 — {side} 开仓')
            await self.app.place_order(side)
            return web.json_response({'ok': True, 'action': 'open', 'side': side})
        except Exception as e:
            msg = f'[API错误] 开仓失败: {e}'
            if self.log:
                self.log.error(msg, exc_info=True)
            self.push_event(msg, str(e))
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_close(self, request: web.Request) -> web.Response:
        """全部平仓"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            if self.log:
                self.log.info('[Web] 移动端操作 — 全部平仓')
            await self.trader.close_position_market()
            return web.json_response({'ok': True, 'action': 'close'})
        except Exception as e:
            msg = f'[API错误] 平仓失败: {e}'
            if self.log:
                self.log.error(msg, exc_info=True)
            self.push_event(msg, str(e))
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_close_percent(self, request: web.Request) -> web.Response:
        """部分平仓 — 提前平仓（Maker 挂单）"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid json'}, status=400)
        percent = body.get('percent', 50)
        if not isinstance(percent, (int, float)) or percent <= 0 or percent > 100:
            return web.json_response({'error': 'percent must be 1-100'}, status=400)
        try:
            if self.log:
                self.log.info(f'[Web] 移动端操作 — 提前平仓')
            await self.app.close_position_early()
            return web.json_response({'ok': True, 'action': 'close_percent', 'percent': percent})
        except Exception as e:
            msg = f'[API错误] 提前平仓失败: {e}'
            if self.log:
                self.log.error(msg, exc_info=True)
            self.push_event(msg, str(e))
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_cancel(self, request: web.Request) -> web.Response:
        """撤销所有挂单"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            if self.log:
                self.log.info('[Web] 移动端操作 — 撤单')
            await self.app.cancel_order()
            return web.json_response({'ok': True, 'action': 'cancel'})
        except Exception as e:
            msg = f'[API错误] 撤单失败: {e}'
            if self.log:
                self.log.error(msg, exc_info=True)
            self.push_event(msg, str(e))
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_settings_get(self, request: web.Request) -> web.Response:
        """获取当前设置"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        cfg = self.trader.config_manager.get_config() if self.trader.config_manager else {}
        cfg['_web_token'] = self.token
        return web.json_response(cfg)

    async def _handle_settings_save(self, request: web.Request) -> web.Response:
        """保存设置"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid json'}, status=400)
        if self.trader.config_manager:
            for key, value in body.items():
                if key.startswith('_'):
                    continue
                self.trader.config_manager.set(key, value)
            self.trader.config_manager.save()
            # 刷新实时管理器配置（移动止损格数等修改后即时生效）
            cfg = self.trader.config_manager
            ts_mgr = getattr(self.trader, 'trailing_stop_manager', None)
            if ts_mgr:
                ts_mgr.refresh_config(cfg.get_trailing_stop_config())
            lp_mgr = getattr(self.trader, 'loss_protection_manager', None)
            if lp_mgr:
                lp_mgr.refresh_config(cfg.get_loss_protection_config())
            # 如果修改了杠杆，同步到交易所并更新 trader 内存值
            if 'leverage' in body:
                api_lev, actual_lev = cfg.get_leverage_config()
                self.trader.leverage_limit = api_lev
                self.trader.actual_leverage = actual_lev
                try:
                    self.trader.api.set_leverage(self.trader.symbol, api_lev)
                    if self.log:
                        self.log.info(f'[Web] 杠杆已同步到交易所：{api_lev}x')
                except Exception as e:
                    if self.log:
                        self.log.warning(f'[Web] 杠杆同步到交易所失败：{e}')
            if self.log:
                self.log.info('[Web] 设置已通过 WebUI 保存')
        return web.json_response({'ok': True})

    async def _handle_backup_list(self, request: web.Request) -> web.Response:
        """列出所有备份"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        cfg = self.trader.config_manager
        if not cfg:
            return web.json_response({'backups': []})
        names = cfg.list_backups()
        backups = []
        for name in names:
            bp = cfg.config_path.parent / name
            try:
                mtime = bp.stat().st_mtime
                from datetime import datetime
                dt = datetime.fromtimestamp(mtime).strftime('%m-%d %H:%M')
            except Exception:
                dt = ''
            backups.append({'name': name, 'mtime': dt})
        return web.json_response({'backups': backups})

    async def _handle_backup_create(self, request: web.Request) -> web.Response:
        """创建新备份"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        cfg = self.trader.config_manager
        if not cfg:
            return web.json_response({'error': 'no config manager'}, status=500)
        try:
            path = cfg.backup_config()
            return web.json_response({'ok': True, 'name': path.name if hasattr(path, 'name') else str(path)})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=500)

    async def _handle_backup_restore(self, request: web.Request) -> web.Response:
        """恢复备份"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid json'}, status=400)
        name = body.get('name', '')
        if not name:
            return web.json_response({'error': 'missing name'}, status=400)
        cfg = self.trader.config_manager
        if not cfg:
            return web.json_response({'error': 'no config manager'}, status=500)
        ok = cfg.restore_config(name)
        if not ok:
            return web.json_response({'error': 'backup not found'}, status=404)
        return web.json_response({'ok': True})

    async def _handle_backup_delete(self, request: web.Request) -> web.Response:
        """删除备份"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid json'}, status=400)
        name = body.get('name', '')
        if not name:
            return web.json_response({'error': 'missing name'}, status=400)
        cfg = self.trader.config_manager
        if not cfg:
            return web.json_response({'error': 'no config manager'}, status=500)
        ok = cfg.delete_backup(name)
        return web.json_response({'ok': ok})

    async def _handle_settings_reset(self, request: web.Request) -> web.Response:
        """重置为默认配置"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        cfg = self.trader.config_manager
        if not cfg:
            return web.json_response({'error': 'no config manager'}, status=500)
        cfg.reset_to_defaults()
        return web.json_response({'ok': True})

    async def _handle_privacy_reset(self, request: web.Request) -> web.Response:
        """重置隐私脱敏基数"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        t = self.trader
        if hasattr(t, 'reset_privacy_baseline'):
            t.reset_privacy_baseline()
            return web.json_response({'ok': True, 'baseline': float(t._privacy_baseline or 0)})
        return web.json_response({'error': 'not supported'}, status=500)

    # ─── WebSocket 处理器 ─────────────────────────────────────

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket 连接"""
        token = request.query.get('token', '')
        if token != self.token:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.close(code=4001, message=b'unauthorized')
            return ws

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)

        if self.log:
            self.log.info(f'[Web] WebSocket 客户端已连接 (共 {len(self._ws_clients)} 个)')

        try:
            # 发送初始状态
            state = self._build_state()
            await ws.send_json(state, dumps=lambda o: json.dumps(o, cls=DecimalEncoder))

            async for msg in ws:
                # 客户端消息（心跳/指令）
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get('type') == 'ping':
                            await ws.send_json({'type': 'pong'})
                    except json.JSONDecodeError:
                        pass
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self._ws_clients.discard(ws)

        if self.log:
            self.log.info(f'[Web] WebSocket 客户端已断开 (剩余 {len(self._ws_clients)} 个)')
        return ws

    # ─── 后台推送任务 ──────────────────────────────────────────

    async def _broadcast_state_loop(self):
        """每秒推送状态快照"""
        while True:
            try:
                # v1.10.0：通知 trader 当前是否有 Web 客户端连接
                has_clients = bool(self._ws_clients)
                if self.trader and hasattr(self.trader, '_has_web_clients'):
                    self.trader._has_web_clients = has_clients
                if has_clients:
                    state = self._build_state()
                    stale = set()
                    for ws in self._ws_clients:
                        try:
                            if ws.closed:
                                stale.add(ws)
                            else:
                                await ws.send_json(state, dumps=lambda o: json.dumps(o, cls=DecimalEncoder))
                        except Exception:
                            stale.add(ws)
                    self._ws_clients -= stale
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                if self.log:
                    self.log.system.warning(f'[Web] 状态推送异常: {e}')
                # 完整堆栈写入文件（log 系统可能截断多行消息）
                with open('logs/_crash_state.log', 'a', encoding='utf-8') as f:
                    f.write(f'\n{"="*60}\n{datetime.now().isoformat()}\n{tb}\n')
            await asyncio.sleep(1)

    async def _broadcast_kline_loop(self):
        """每秒推送 K 线 tick"""
        while True:
            try:
                if self._ws_clients:
                    tick = self._build_kline_tick()
                    if tick:
                        stale = set()
                        for ws in self._ws_clients:
                            try:
                                if ws.closed:
                                    stale.add(ws)
                                else:
                                    await ws.send_json(tick)
                            except Exception:
                                stale.add(ws)
                        self._ws_clients -= stale
            except Exception as e:
                if self.log:
                    self.log.system.debug(f'[Web] K线推送异常: {e}')
            await asyncio.sleep(1)

    async def _broadcast_depth_loop(self):
        """每 500ms 推送订单簿"""
        while True:
            try:
                if self._ws_clients and self._depth_snapshot:
                    stale = set()
                    for ws in self._ws_clients:
                        try:
                            if ws.closed:
                                stale.add(ws)
                            else:
                                await ws.send_json(self._depth_snapshot)
                        except Exception:
                            stale.add(ws)
                    self._ws_clients -= stale
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def _broadcast_events_loop(self):
        """推送增量事件"""
        while True:
            try:
                event = await self._event_queue.get()
                if self._ws_clients:
                    stale = set()
                    for ws in self._ws_clients:
                        try:
                            if ws.closed:
                                stale.add(ws)
                            else:
                                await ws.send_json(event)
                        except Exception:
                            stale.add(ws)
                    self._ws_clients -= stale
            except Exception:
                pass

    # ─── 启动 / 停止 ──────────────────────────────────────────

    async def start(self):
        """启动 Web 服务"""
        self._app = web.Application()

        # 静态文件
        static_dir = Path(__file__).parent / 'static'
        self._app.router.add_static('/static/', static_dir, show_index=False)
        # 兼容: /tv-charts.umd.js → /static/tv-charts.umd.js（index.html 中 CDN 后备路径）
        self._app.router.add_get('/tv-charts.umd.js', lambda r: web.FileResponse(static_dir / 'tv-charts.umd.js'))
        # 图表测试页（免认证）
        self._app.router.add_get('/chart-test', lambda r: web.FileResponse(static_dir / 'chart-test.html'))
        # 音效文件
        _sounds_dir = Path(__file__).parent.parent.parent / 'sounds'
        self._app.router.add_get('/sounds/ding.wav', lambda r: web.FileResponse(_sounds_dir / 'ding.wav'))

        # 路由
        self._app.router.add_get('/', self._handle_index)
        self._app.router.add_get('/ws', self._handle_ws)
        self._app.router.add_get('/api/state', self._handle_state)
        self._app.router.add_get('/api/klines', self._handle_klines)
        self._app.router.add_get('/api/klines/history', self._handle_klines_history)
        self._app.router.add_get('/api/history', self._handle_history)
        self._app.router.add_post('/api/history/refresh', self._handle_history_refresh)
        self._app.router.add_get('/api/history/asset-curve', self._handle_asset_curve)
        self._app.router.add_post('/api/open', self._handle_open)
        self._app.router.add_post('/api/close', self._handle_close)
        self._app.router.add_post('/api/close_percent', self._handle_close_percent)
        self._app.router.add_post('/api/cancel', self._handle_cancel)
        self._app.router.add_get('/api/settings', self._handle_settings_get)
        self._app.router.add_post('/api/settings', self._handle_settings_save)
        self._app.router.add_get('/api/settings/backups', self._handle_backup_list)
        self._app.router.add_post('/api/settings/backup', self._handle_backup_create)
        self._app.router.add_post('/api/settings/restore', self._handle_backup_restore)
        self._app.router.add_post('/api/settings/backup/delete', self._handle_backup_delete)
        self._app.router.add_post('/api/settings/reset', self._handle_settings_reset)
        self._app.router.add_post('/api/privacy/reset', self._handle_privacy_reset)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port, reuse_address=True)
        await site.start()

        # 启动后台推送任务
        asyncio.ensure_future(self._broadcast_state_loop())
        asyncio.ensure_future(self._broadcast_kline_loop())
        asyncio.ensure_future(self._broadcast_depth_loop())
        asyncio.ensure_future(self._broadcast_events_loop())

        # 打日志
        local_ip = self._get_local_ip()
        full_url = f'http://{local_ip}:{self.port}?token={self.token}'
        if self.log:
            self.log.info(f'Web UI: {full_url}')
        else:
            print(f'Web UI: {full_url}')

    async def stop(self):
        """停止 Web 服务"""
        # 关闭所有 WebSocket 连接
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_clients.clear()

        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._app = None

    @staticmethod
    def _get_local_ip() -> str:
        """获取本机局域网 IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'


async def start_web_server(trader, host='0.0.0.0', port=8099, log_manager=None, token='', app=None) -> WebServer:
    """启动 Web 服务（由 main_live.py 调用）"""
    config = {
        'web_ui': {
            'host': host,
            'port': port,
            'enabled': True
        }
    }
    server = WebServer(trader, config, log_manager, token=token, app=app)
    await server.start()
    return server
