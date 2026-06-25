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
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Set

import aiohttp
from aiohttp import web

# 数据目录（用于历史K线查询）
DATA_DIR = Path(__file__).parent.parent.parent / 'data' / 'klines'


class DecimalEncoder(json.JSONEncoder):
    """JSON 序列化 Decimal → float"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.strftime('%H:%M:%S')
        return super().default(obj)


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


class WebServer:
    """移动端 Web 服务"""

    def __init__(self, trader, config: dict, log_manager=None):
        self.trader = trader
        self.config = config
        self.log = log_manager
        self.host = config.get('web_ui', {}).get('host', '0.0.0.0')
        self.port = config.get('web_ui', {}).get('port', 8099)

        # 认证 token
        self.token = secrets.token_hex(16)

        # WebSocket 客户端管理
        self._ws_clients: Set[web.WebSocketResponse] = set()

        # 事件队列（增量推送）
        self._event_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # 订单簿数据（由外部更新，线程安全由 asyncio 保证）
        self._depth_snapshot: dict = {}

        # K 线数据（由外部更新）
        self._klines_cache: list = []

        # Taker ratio 数据（由外部更新）
        self._taker_ratio: dict = {'buy_pct': 50.0, 'sell_pct': 50.0}

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
        self._depth_snapshot = {
            'type': 'depth',
            'ts': int(time.time() * 1000),
            'bids': [[str(b[0]), str(b[1])] for b in bids[:8]],
            'asks': [[str(a[0]), str(a[1])] for a in asks[:8]],
            'spread': round(spread, 4),
            'spread_pct': round(spread_pct, 6),
            'pressure': pressure,
            'pressure_pct': round(pressure_pct, 1)
        }

    def update_taker_ratio(self, buy_pct: float, sell_pct: float):
        """更新主动成交比率"""
        self._taker_ratio = {'buy_pct': round(buy_pct, 2), 'sell_pct': round(sell_pct, 2)}

    def update_klines_cache(self, klines: list):
        """更新 K 线缓存"""
        self._klines_cache = klines

    # ─── 状态快照构建 ─────────────────────────────────────────

    def _build_state(self) -> dict:
        """构建完整状态快照"""
        t = self.trader
        cfg = t.config_manager
        indicators = t.indicators

        # 价格
        price = float(t.last_price) if t.last_price else 0

        # ATR14
        atr14 = 0
        atr14_pct = 0
        atr14_percentile = 0
        atr14_ref = 'normal'
        if indicators:
            vol = indicators.volatility
            atr14 = round(vol.get_atr14(), 2) if vol.get_atr14() else 0
            atr14_pct = round(vol.get_atr14_pct(), 4) if vol.get_atr14_pct() else 0
            atr14_percentile = getattr(vol, '_atr14_percentile', 0) or 0
            atr14_ref = getattr(vol, '_atr14_ref', 'normal') or 'normal'

        # 振幅
        amp_1m = 0
        if indicators:
            vol_m = indicators.volatility.get_metrics()
            amp_1m = round(vol_m.get('1min_amplitude', 0), 4)

        # 价格范围
        pr_minutes = cfg.get('price_range.minutes', 30) if cfg else 30
        pr_high = price
        pr_low = price
        if hasattr(t, '_price_tracker') and t._price_tracker:
            pr = t._price_tracker.get_range(pr_minutes)
            if pr:
                pr_high = float(pr['high'])
                pr_low = float(pr['low'])

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
                position = {
                    'side': side,
                    'size': float(total_size),
                    'entry_weighted_avg': float(wavg),
                    'floating_pnl': float(fp),
                    'peak_floating_profit': float(t.batch_state.get('peak_floating_profit', 0) or 0),
                    'peak_floating_loss': float(t.batch_state.get('peak_floating_loss', 0) or 0)
                }
        elif t.position:
            side = t.position.get('side', 'NONE')
            entry = float(t.position.get('entry_price', 0))
            size = float(t.position.get('size', 0))
            if side == 'LONG':
                fp = (price - entry) * size if price and entry else 0
            else:
                fp = (entry - price) * size if price and entry else 0
            position = {
                'side': side,
                'size': size,
                'entry_weighted_avg': entry,
                'floating_pnl': round(fp, 4),
                'peak_floating_profit': float(getattr(t, '_peak_floating_profit', 0) or 0),
                'peak_floating_loss': float(getattr(t, '_peak_floating_loss', 0) or 0)
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
                        'tp_price': float(b.get('tp_price', 0)),
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
            'loss_protection_active': False
        }
        if t.batch_state and t.batch_state.get('enabled'):
            if t.batch_state.get('sl_order_id'):
                risk['sl_price'] = float(t.batch_state.get('sl_price', 0) or 0)
            if t.batch_state.get('sm_order_id'):
                risk['sm_price'] = float(t.batch_state.get('sm_price', 0) or 0)
        else:
            if t.sl_order:
                risk['sl_price'] = float(t.sl_order.get('triggerPrice', 0))
            if t.stop_market_order:
                risk['sm_price'] = float(t.stop_market_order.get('triggerPrice', 0))
        if t.loss_protection_manager:
            lp_status = t.loss_protection_manager.get_status()
            risk['loss_protection_active'] = lp_status.get('status', '未启用') in ('已保护',)
            risk['loss_protection_status'] = lp_status.get('status', '未启用')

        # 24h 统计
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

        # 上一笔持仓
        last_position = None
        if t.position_history:
            last = t.position_history[-1]
            last_position = {
                'side': last.get('side', 'NONE'),
                'entry': float(last.get('entry_price', 0)),
                'exit': float(last.get('exit_price', 0)),
                'size': float(last.get('size', 0)),
                'pnl': float(last.get('pnl', 0)),
                'fee': float(last.get('total_fee', 0)),
                'funding': float(last.get('funding', 0)),
                'net_pnl': float(last.get('net_pnl', 0)),
                'exit_type': last.get('exit_type', 'MANUAL'),
                'close_time': last.get('close_time', ''),
                'duration': last.get('duration', '')
            }

        # 连接状态
        ws_healthy = getattr(t, '_ws_health', {})
        connection = {
            'ws_healthy': ws_healthy.get('market', True) if ws_healthy else self._market_ws_healthy
        }

        return {
            'type': 'state',
            'ts': int(time.time() * 1000),
            'price': price,
            'atr14': atr14,
            'atr14_pct': atr14_pct,
            'atr14_percentile': atr14_percentile,
            'atr14_ref': atr14_ref,
            'amplitude_1m': amp_1m,
            'price_range_high': pr_high,
            'price_range_low': pr_low,
            'price_range_minutes': pr_minutes,
            'position': position,
            'pending_orders': pending_orders,
            'tp_orders': tp_orders,
            'risk': risk,
            'stats_24h': stats_24h,
            'last_position': last_position,
            'taker_ratio': self._taker_ratio,
            'connection': connection
        }

    def _build_kline_tick(self) -> dict:
        """构建当前 K 线 tick"""
        t = self.trader
        if not t.indicators:
            return None
        vol = t.indicators.volatility
        kline = vol.current_kline if hasattr(vol, 'current_kline') else None
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
            return web.FileResponse(html_path)
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
        """K 线数据 API"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            limit = int(request.query.get('limit', '100'))
        except ValueError:
            limit = 100
        limit = min(limit, 1440)

        # 从 indicators 的 deque 获取最近的 K 线
        klines = []
        if self.trader.indicators:
            vol = self.trader.indicators.volatility
            kline_deque = vol._klines if hasattr(vol, '_klines') else None
            if kline_deque:
                klines = list(kline_deque)[-limit:]
        result = []
        for k in klines:
            result.append({
                't': k.get('timestamp', 0),
                'o': float(k.get('open', 0)),
                'h': float(k.get('high', 0)),
                'l': float(k.get('low', 0)),
                'c': float(k.get('close', 0)),
                'v': float(k.get('volume', 0))
            })
        return web.json_response(result)

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
            # 过滤 30 天内的数据
            cutoff = datetime.now() - timedelta(days=30)
            filtered = []
            for h in reversed(history):
                close_time_str = h.get('close_time', '')
                try:
                    close_dt = datetime.strptime(close_time_str, '%m-%d %H:%M')
                    close_dt = close_dt.replace(year=datetime.now().year)
                except ValueError:
                    filtered.append(h)
                    continue
                if close_dt >= cutoff:
                    filtered.append(h)

            total = len(filtered)
            batch = filtered[offset:offset + limit]
            result = []
            for h in batch:
                result.append({
                    'side': h.get('side', 'NONE'),
                    'entry_price': float(h.get('entry_price', 0)),
                    'exit_price': float(h.get('exit_price', 0)),
                    'size': float(h.get('size', 0)),
                    'pnl': float(h.get('pnl', 0)),
                    'total_fee': float(h.get('total_fee', 0)),
                    'funding': float(h.get('funding', 0)),
                    'net_pnl': float(h.get('net_pnl', 0)),
                    'exit_type': h.get('exit_type', 'MANUAL'),
                    'close_time': h.get('close_time', ''),
                    'duration': h.get('duration', '')
                })
            return web.json_response({'items': result, 'total': total, 'offset': offset, 'limit': limit})
        except Exception as e:
            if self.log:
                self.log.error(f'[Web] /api/history 异常: {e}', exc_info=True)
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

        if self.log:
            self.log.info(f'[Web] 移动端操作 — {side} 开仓')
        if side == 'LONG':
            self.trader.long()
        else:
            self.trader.short()
        return web.json_response({'ok': True, 'action': 'open', 'side': side})

    async def _handle_close(self, request: web.Request) -> web.Response:
        """全部平仓"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        if self.log:
            self.log.info('[Web] 移动端操作 — 全部平仓')
        self.trader.close_all_positions()
        return web.json_response({'ok': True, 'action': 'close'})

    async def _handle_close_percent(self, request: web.Request) -> web.Response:
        """部分平仓"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid json'}, status=400)
        percent = body.get('percent', 50)
        if not isinstance(percent, (int, float)) or percent <= 0 or percent > 100:
            return web.json_response({'error': 'percent must be 1-100'}, status=400)
        if self.log:
            self.log.info(f'[Web] 移动端操作 — 部分平仓 {percent}%')
        self.trader.close_percent(percent)
        return web.json_response({'ok': True, 'action': 'close_percent', 'percent': percent})

    async def _handle_cancel(self, request: web.Request) -> web.Response:
        """撤销所有挂单"""
        if not self._check_auth(request):
            return web.json_response({'error': 'unauthorized'}, status=403)
        if self.log:
            self.log.info('[Web] 移动端操作 — 撤单')
        self.trader.cancel_all_orders()
        return web.json_response({'ok': True, 'action': 'cancel'})

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
                self.trader.config_manager.set(key, value)
            self.trader.config_manager.save()
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
        return web.json_response({'ok': ok})

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
                if self.log:
                    self.log.system.warning(f'[Web] 状态推送异常: {e}')
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

        # 路由
        self._app.router.add_get('/', self._handle_index)
        self._app.router.add_get('/ws', self._handle_ws)
        self._app.router.add_get('/api/state', self._handle_state)
        self._app.router.add_get('/api/klines', self._handle_klines)
        self._app.router.add_get('/api/klines/history', self._handle_klines_history)
        self._app.router.add_get('/api/history', self._handle_history)
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

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
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


async def start_web_server(trader, host='0.0.0.0', port=8099, log_manager=None) -> WebServer:
    """启动 Web 服务（由 main_live.py 调用）"""
    config = {
        'web_ui': {
            'host': host,
            'port': port,
            'enabled': True
        }
    }
    server = WebServer(trader, config, log_manager)
    await server.start()
    return server
