# -*- coding: utf-8 -*-
"""
实盘交易模块 - 币安 Futures 实盘交易
v1.5.0 - 新增移动止损 + 浮亏保护
"""

import asyncio
import time
from collections import deque
from pathlib import Path
from decimal import Decimal, ROUND_DOWN
from datetime import datetime
from typing import Optional, Dict, List

from src.api.binance_client import BinanceClient, BinanceAPIError
from src.api.signature import get_timestamp
from src.api.user_stream_ws import UserStreamWebSocket
from src.logger import TradeLogger
from src.trading.trailing_stop import TrailingStopManager
from src.trading.loss_protection import LossProtectionManager


class LiveTrader:
    """实盘交易器"""
    
    def __init__(self, api_key: str, api_secret: str, symbol: str,
                 leverage_limit: int = 100, actual_leverage: int = 25,
                 testnet: bool = False, logger: TradeLogger = None,
                 config_manager=None, log_manager=None, indicators_manager=None):
        self.symbol = symbol
        self.leverage_limit = leverage_limit
        self.actual_leverage = actual_leverage
        self.testnet = testnet
        self.logger = logger
        self.config_manager = config_manager
        self.log_manager: LogManager = log_manager  # 由 main_live.py 传入
        self.indicators = indicators_manager  # 指标管理器，用于 ATR14 模式止盈止损
        
        self.api = BinanceClient(api_key, api_secret, testnet)
        self.listener = None  # 行情 WebSocket 监听器（从外部传入）
        self.user_stream_ws: Optional[UserStreamWebSocket] = None
        self.listen_key: Optional[str] = None

        # 声音文件路径
        self._ding_path: Optional[str] = None
        for candidate in [
            Path(__file__).parent.parent.parent / 'sounds' / 'ding.wav',
            Path.cwd() / 'sounds' / 'ding.wav',
        ]:
            if candidate.exists():
                self._ding_path = str(candidate)
                break
        
        # 交易状态
        self.position: Optional[Dict] = None
        self.pending_order: Optional[Dict] = None
        self.tp_order: Optional[Dict] = None
        self.sl_order: Optional[Dict] = None
        self.stop_market_order: Optional[Dict] = None
        self.early_close_order: Optional[Dict] = None
        
        self.tp_order_backup: Optional[Dict] = None
        self.sl_order_backup: Optional[Dict] = None

        # 历史持仓
        self.position_history = deque(maxlen=500)  # v1.10.0: 最多保留500条

        # BNB 价格缓存（用于手续费换算）
        self._bnb_prices: dict = {}       # {timestamp_sec: Decimal}
        self._bnb_last_fetch_ms: int = 0
        self._bnb_ticker_price: Decimal = Decimal('0')
        self._bnb_ticker_ts: float = 0

        # 24 小时交易统计
        self.trade_stats_24h: dict = {}

        # 隐私脱敏：启动时快照账户余额作为百分比基数
        self._privacy_baseline: Optional[Decimal] = None
        self._privacy_baseline_set: bool = False

        # 账户信息
        self.available_balance: Decimal = Decimal('0')
        self.position_margin: Decimal = Decimal('0')
        self.order_margin: Decimal = Decimal('0')
        self._ws_balance_update_reason: str = ''  # WS余额更新来源（用于调试）
        
        # 行情数据
        self.last_price: Optional[Decimal] = None
        self.orderbook: Dict = {'bids': [], 'asks': []}

        # 挂单成交检测（v1.5.5）
        self._order_placed_at: Optional[float] = None  # 挂单时间戳（秒）
        self._fill_check_done: bool = False  # 是否已经确认成交并处理过

        # 关键价格表：每帧与订单簿比对，穿透时 REST 确认（替代失效的用户流 WS）
        self._key_prices: List[Dict] = []  # [{type, price, order_id, order_category}]
        self._key_price_triggered: set = set()  # 已触发过的 order_id，防止重复查 REST

        # 操作日志
        self.action_log: List[Dict] = []
        
        # v1.5.0 新增：移动止损和浮亏保护管理器
        self.trailing_stop_manager: Optional[TrailingStopManager] = None
        self.loss_protection_manager: Optional[LossProtectionManager] = None

        # v1.9.0 新增：分批建仓模式
        self.batch_state: Optional[Dict] = None  # 分批状态（None = 非分批模式）
        self._batch_order_map: Dict[int, int] = {}  # order_id → batch_index
        self._batch_tp_map: Dict[int, int] = {}  # tp_order_id → batch_index

        # v1.9.0 新增：WS 健康与 REST 兜底
        self._ws_health = {
            'user_stream_connected': False,
            'user_stream_last_msg_ts': 0.0,
            'user_stream_msg_count': 0,
            'user_stream_restart_count': 0,
            'fallback_active': False,
        }
        self._rest_fallback_task: Optional[asyncio.Task] = None
        self._rest_fallback_interval: float = 3.0  # REST 轮询间隔（秒）

        self._has_web_clients = False  # v1.10.0：由 Web 服务更新，控制 TUI 音效
        self.running = False
        self.connected = False

    def play_ding(self, count: int = 1, throttle_key: str = None):
        """播放提示音（count 控制响几声）"""
        if not self._ding_path:
            return
        if self.config_manager and not self.config_manager.is_sound_enabled():
            return
        # v1.10.0：如果 Web UI 有客户端连接，TUI 端不播放音效（手机端已播）
        if getattr(self, '_has_web_clients', False):
            return
        # v1.10.0：5秒节流，同类型音效不重复播放
        if throttle_key:
            now = time.time()
            if not hasattr(self, '_sound_throttle'):
                self._sound_throttle = {}
            last = self._sound_throttle.get(throttle_key, 0)
            if now - last < 5:
                return
            self._sound_throttle[throttle_key] = now
        try:
            import winsound
            for _ in range(count):
                winsound.PlaySound(self._ding_path, winsound.SND_FILENAME)
        except Exception:
            pass

    def _trigger_sound(self, action: str):
        """根据 action 名称自动触发对应音效"""
        if '开仓成交' in action or '开仓成交（部分）' in action:
            self.play_ding(1, throttle_key='open')  # 开仓响一声，5秒内不重复
        elif '持仓超时' in action:
            self.play_ding(4)  # 不节流（极少触发）
        elif any(kw in action for kw in [
            '止盈成交', '止损成交', '保底止损成交',
            '提前平仓成交', '移动止损成交',
            '手动平仓成交', '持仓同步成交',
            '浮亏保护成交',
        ]):
            self.play_ding(3, throttle_key='close')  # 平仓响三声，5秒内不重复

    def format_money(self, value: Decimal) -> str:
        """格式化金额用于 TUI 显示（自动应用隐私脱敏）

        脱敏关闭：返回绝对金额字符串，如 "1.5 USDT"
        脱敏开启：返回相对百分比字符串，如 "0.250%"
        注意：此方法仅用于 TUI 显示，日志写入使用原始金额
        """
        privacy_enabled = False
        if self.config_manager:
            privacy_enabled = self.config_manager.get('privacy.enabled', False)

        if not privacy_enabled or self._privacy_baseline is None or self._privacy_baseline == 0:
            f_val = float(value)
            if f_val == 0:
                return '0 USDT'
            formatted = f'{f_val:.6f}'.rstrip('0').rstrip('.')
            return f"{formatted} USDT"
        else:
            pct = float(value / self._privacy_baseline * Decimal('100'))
            if pct == 0:
                return '0.000%'
            # 保留3位小数，去尾零
            formatted = f'{pct:.3f}'.rstrip('0').rstrip('.')
            return f"{formatted}%"

    def reset_privacy_baseline(self) -> bool:
        """重置脱敏基数为当前可用余额

        仅在隐私脱敏开启且当前余额>0时生效。
        返回 True 表示已重置，False 表示跳过（脱敏未开启或余额为零）。
        """
        privacy_enabled = False
        if self.config_manager:
            privacy_enabled = self.config_manager.get('privacy.enabled', False)
        if not privacy_enabled:
            return False
        if self.available_balance <= 0:
            return False
        self._privacy_baseline = self.available_balance
        self._add_action("脱敏基数已重置", "")
        return True

    def mask_log_text(self, detail: str) -> str:
        """对日志文本中的金额进行脱敏匹配替换

        匹配 USDT/USDC/U 前的浮点数（含正负号），替换为百分比。
        脱敏关闭时直接返回原字符串。
        """
        privacy_enabled = False
        if self.config_manager:
            privacy_enabled = self.config_manager.get('privacy.enabled', False)

        if not privacy_enabled or self._privacy_baseline is None or self._privacy_baseline == 0:
            return detail

        import re
        # 匹配：可选正负号 + 浮点数 + U(随后的 SDT/SDC 可选)
        pattern = re.compile(r'([+-]?\d+\.\d+)\s*(USDT|USDC|U)\b')

        def _replacer(m):
            amount_str = m.group(1)
            try:
                amount = Decimal(amount_str)
                pct = float(amount / self._privacy_baseline * Decimal('100'))
                formatted = f'{pct:.3f}'.rstrip('0').rstrip('.')
                return f"{formatted}%"
            except Exception:
                return m.group(0)

        return pattern.sub(_replacer, detail)

    def _get_calendar_day_cutoff_ms(self, tz_str: str, now_ms: int) -> int:
        """计算给定 UTC 时区今天 00:00:00 对应的 UTC 毫秒时间戳"""
        import calendar as _calendar
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        try:
            offset_hours = float(tz_str)
        except (ValueError, TypeError):
            offset_hours = 8.0
        tz = _tz(_td(hours=offset_hours))
        now_dt = _dt.fromtimestamp(now_ms / 1000.0, tz=tz)
        today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff_dt_utc = today_start.astimezone(_tz.utc)
        return int(_calendar.timegm(cutoff_dt_utc.timetuple()) * 1000)

    async def initialize(self) -> bool:
        """初始化"""
        self.log_manager.system.debug("\n初始化实盘交易...") if self.log_manager else None
        self.log_manager.system.debug("=" * 70) if self.log_manager else None
        
        try:
            offset = self.api.sync_time()
            server_time_ms = get_timestamp()
            self.log_manager.system.debug(f"✓ API 连接成功，服务器时间：{datetime.fromtimestamp(server_time_ms/1000)}（偏移：{offset:+d}ms）") if self.log_manager else None
            
            account = self.api.get_account()
            for asset in account.get('assets', []):
                if asset['asset'] == 'USDC':
                    self.available_balance = Decimal(asset['availableBalance'])
                    # 记录启动时余额基数（用于金额脱敏）
                    if not self._privacy_baseline_set and self.available_balance > 0:
                        self._privacy_baseline = self.available_balance
                        self._privacy_baseline_set = True
                    break

            self.log_manager.system.debug(f"✓ 账户余额：{self.available_balance} USDC") if self.log_manager else None
            self.api.set_leverage(self.symbol, self.leverage_limit)
            self.log_manager.system.debug(f"✓ 杠杆已设置：{self.leverage_limit}x") if self.log_manager else None
            await self._start_user_stream()
            self.api.cancel_all_orders(self.symbol)
            self.log_manager.system.debug("✓ 已撤销所有遗留挂单") if self.log_manager else None
            
            # v1.5.0 新增：初始化移动止损和浮亏保护管理器
            if self.config_manager:
                trailing_config = self.config_manager.get_trailing_stop_config()
                loss_protection_config = self.config_manager.get_loss_protection_config()
                
                self.trailing_stop_manager = TrailingStopManager(self, None, trailing_config)
                self.loss_protection_manager = LossProtectionManager(self, None, loss_protection_config)
                
                if trailing_config.get('enabled'):
                    self.log_manager.system.debug(f"✓ 移动止损已启用：{trailing_config.get('grid_count')}格") if self.log_manager else None
                if loss_protection_config.get('enabled'):
                    self.log_manager.system.debug(f"✓ 浮亏保护已启用：{loss_protection_config.get('trigger_minutes')}分钟触发") if self.log_manager else None
            
            self._add_action("初始化", "实盘初始化成功")
            self.connected = True
            self.log_manager.system.debug("=" * 70) if self.log_manager else None
            return True
        
        except BinanceAPIError as e:
            msg = f"✗ API 错误：[{e.code}] {e.msg}"
            print(msg)
            if self.log_manager:
                self.log_manager.system.error(msg, exc_info=True)
            return False
        except Exception as e:
            msg = f"✗ 初始化失败：{e}"
            print(msg)
            if self.log_manager:
                self.log_manager.system.error(msg, exc_info=True)
            return False
    
    async def _start_user_stream(self):
        """启动用户数据流 WebSocket（可选功能，失败不影响主程序）"""
        try:
            self.listen_key = self.api.get_listen_key()
            self.log_manager.system.debug(f"listenKey已获取：{self.listen_key[:16]}...") if self.log_manager else None
            self.user_stream_ws = UserStreamWebSocket(
                self.listen_key,
                api_client=self.api,
                testnet=self.testnet,
                log_func=lambda msg: self.log_manager.system.debug(msg) if self.log_manager else None
            )
            self.user_stream_ws.add_order_callback(self._on_order_update)
            self.user_stream_ws.add_account_callback(self._on_account_update)
            self.user_stream_task = asyncio.create_task(self.user_stream_ws.connect())

            # 等待 WebSocket 真正连接成功（最多 10 秒）
            for i in range(20):
                if self.user_stream_ws.connected:
                    break
                await asyncio.sleep(0.5)
            else:
                self.log_manager.system.debug("⚠ 用户数据流连接超时，订单回调可能延迟") if self.log_manager else None

            if self.user_stream_ws.connected:
                self.log_manager.system.debug("✓ 用户数据流已连接（用于订单状态更新，30分钟自动保活）") if self.log_manager else None
            else:
                self.log_manager.system.debug("⚠ 用户数据流连接中，订单状态更新可能有延迟") if self.log_manager else None
        except Exception as e:
            self.log_manager.system.debug(f"⚠ 用户数据流启动失败：{e}") if self.log_manager else None
            self.log_manager.system.debug("  程序仍可正常运行，但订单状态更新可能有延迟") if self.log_manager else None
            self.log_manager.system.debug("  建议：检查网络连接或防火墙设置") if self.log_manager else None

    def _on_account_update(self, account_data: dict):
        """账户更新回调（WebSocket，实时）"""
        try:
            balances = account_data.get('B', [])
            for bal in balances:
                if bal.get('a') == 'USDC':
                    wallet_balance = Decimal(bal.get('wb', '0'))
                    cross_balance = Decimal(bal.get('cw', '0'))
                    if wallet_balance > 0:
                        self.available_balance = wallet_balance
                        self._ws_balance_update_reason = account_data.get('m', '')
        except Exception:
            pass
    
    async def sync_position_from_exchange(self):
        """从交易所同步持仓状态（用于修复用户数据流失效时的状态不一致）"""
        try:
            positions = self.api.get_position(self.symbol)
            total_size = Decimal('0')
            entry_price = Decimal('0')
            side = None
            
            for pos in positions:
                size = Decimal(pos['positionAmt'])
                if size != 0:
                    total_size = abs(size)
                    entry_price = Decimal(pos['entryPrice'])
                    side = 'LONG' if size > 0 else 'SHORT'
                    break
            
            # 如果有持仓但程序不知道，强制同步
            if total_size > 0 and not self.position:
                self.log_manager.system.debug(f"\n[持仓同步] 发现未同步的持仓！") if self.log_manager else None
                self.log_manager.system.debug(f"  方向：{side}, 数量：{total_size}, 开仓价：{entry_price}") if self.log_manager else None
                
                self.position = {
                    'side': side,
                    'entry_price': entry_price,
                    'size': total_size,
                    'time': datetime.now()
                }
                self.pending_order = None  # 清除挂单状态
                self._add_action("持仓同步", f"{side} @ {entry_price} x {total_size}")
                
                # 下止盈止损单
                self.log_manager.system.debug(f"  开始下止盈止损单...") if self.log_manager else None
                asyncio.create_task(self._safe_place_tp_sl_orders())
            
            # 如果程序有持仓但实际没有，清除（彻底平仓）
            elif total_size == 0 and self.position:
                self.log_manager.system.debug(f"[持仓同步] 持仓已平仓（程序未感知）") if self.log_manager else None

                entry_price = self.position['entry_price']
                pos_size = self.position['size']
                pos_side = self.position['side']

                # 查最近成交获取平仓价和手续费，计算 PnL
                exit_price = Decimal('0')
                commission = Decimal('0')
                try:
                    pos_time = self.position.get('time')
                    start_ms = int(pos_time.timestamp() * 1000) - 2000 if pos_time else None
                    fills = self.api.get_fills(self.symbol, limit=5, startTime=start_ms)
                    total_fill_qty = Decimal('0')
                    expected_side = 'SELL' if pos_side == 'LONG' else 'BUY'
                    for fill in fills:
                        if fill.get('side', '') == expected_side:
                            fq = Decimal(fill.get('qty', '0'))
                            fp = Decimal(fill.get('price', '0'))
                            fc = Decimal(fill.get('commission', '0'))
                            total_fill_qty += fq
                            if exit_price == 0:
                                exit_price = fp
                            else:
                                exit_price = (exit_price * (total_fill_qty - fq) + fp * fq) / total_fill_qty
                            commission += fc
                            if total_fill_qty >= pos_size:
                                break
                except Exception:
                    pass

                # 计算持仓时长
                duration_str = ""
                if pos_time:
                    elapsed = int((datetime.now() - pos_time).total_seconds())
                    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
                    duration_str = f" | 持仓 {h:02d}:{m:02d}:{s:02d}"

                if exit_price > 0:
                    if pos_side == 'LONG':
                        pnl = (exit_price - entry_price) * pos_size - commission
                    else:
                        pnl = (entry_price - exit_price) * pos_size - commission
                    pnl_str = f"{pnl:+.6f} USDT"
                    self._add_action("持仓同步成交",
                        f"PnL: {pnl_str}{duration_str} | 平仓均价 {exit_price:.2f}")
                else:
                    self._add_action("持仓同步成交",
                        f"持仓已清除（无法获取平仓价）{duration_str}")

                # 主动刷新账户余额
                self.sync_account()
                if self.logger:
                    self.logger.log_balance('position_closed', self.available_balance, {
                        'reason': 'sync_detected',
                        'last_position': pos_side,
                        'last_entry': float(entry_price),
                        'close_price': float(exit_price) if exit_price > 0 else 0,
                        'pnl': float(pnl) if exit_price > 0 else 0,
                    })

                # 取消交易所所有剩余挂单
                try:
                    self.api.cancel_all_orders(self.symbol)
                    self.api.cancel_all_open_orders(self.symbol)
                except Exception:
                    pass

                # 清空本地状态
                self.position = None
                self.tp_order = None
                self.sl_order = None
                self.stop_market_order = None
                self.early_close_order = None
                self._key_prices = []
                self._key_price_triggered.clear()
                if self.trailing_stop_manager:
                    asyncio.create_task(self.trailing_stop_manager.on_position_closed())
                if self.loss_protection_manager:
                    self.loss_protection_manager.on_position_closed()

                # 刷新历史持仓（延迟等 Binance 同步）
                asyncio.create_task(self._refresh_history_delayed())

            return total_size > 0
        except Exception as e:
            self.log_manager.system.debug(f"[持仓同步] 失败：{e}") if self.log_manager else None
            return False
    
    def _on_order_update(self, order_data: dict):
        """订单更新回调（同步函数）"""
        order_status = order_data.get('X')
        order_id = order_data.get('i')
        order_type = order_data.get('ot')

        # 记录详细订单信息
        self.log_manager.system.debug(f"[订单更新] 状态={order_status}, 类型={order_type}, ID={order_id}, 方向={order_data.get('S', '?')}") if self.log_manager else None
        self.log_manager.system.debug(f"  成交数量：{order_data.get('z', 0)}, 成交均价：{order_data.get('ap', 0)}") if self.log_manager else None
        self.log_manager.system.debug(f"  手续费：{order_data.get('fc', 0)} {order_data.get('fs', 'USDC')}") if self.log_manager else None

        # v1.9.0：分批模式事件路由
        if self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed'):
            self._route_batch_order_update(order_data, order_status, order_id)
            return

        # 新版 TradingLogger — 记录订单生命周期
        if self.log_manager:
            try:
                if order_status == 'FILLED':
                    self.log_manager.trading.log_order_filled(
                        order_id=str(order_id),
                        avg_price=float(order_data.get('ap', 0)),
                        filled_qty=float(order_data.get('z', 0)),
                        commission=float(order_data.get('fc', 0)),
                        commission_asset=str(order_data.get('fs', 'USDC')),
                    )
                elif order_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                    self.log_manager.trading.log_order_canceled(
                        order_id=str(order_id),
                        reason=order_status,
                    )
            except Exception:
                pass
        
        # 开仓挂单成交
        if self.pending_order and self.pending_order.get('orderId') == order_id:
            if order_status == 'FILLED':
                side = self.pending_order.get('side', 'LONG')
                entry_price = Decimal(order_data.get('ap', '0'))
                filled_qty = Decimal(order_data.get('z', '0'))
                commission = Decimal(order_data.get('fc', '0'))
                commission_asset = order_data.get('fs', 'USDC')
                original_size = self.pending_order.get('size', Decimal('0'))

                # 校验成交量：FILLED 但成交量 < 挂单量，说明部分成交
                if filled_qty < original_size and filled_qty > 0:
                    self.log_manager.system.debug(f"[开仓成交] 部分成交：挂单 {original_size}，已成交 {filled_qty}，剩余 {(original_size - filled_qty)} 待确认...") if self.log_manager else None
                    self._add_action("部分成交", f"{side} @ {entry_price} x {filled_qty}/{original_size} | 等待超时撤单")
                    self.sync_account()
                    self.position = {
                        'side': side,
                        'entry_price': entry_price,
                        'size': filled_qty,
                        'time': datetime.now()
                    }
                    self.pending_order = None

                    if self.logger:
                        self.logger.record_signal(side, entry_price, self.orderbook)

                    asyncio.create_task(self._safe_place_tp_sl_orders())
                else:
                    self.log_manager.system.debug(f"[开仓成交] 挂单完全成交，立即下止盈止损单...") if self.log_manager else None
                    self.log_manager.system.debug(f"  成交价：{entry_price}, 成交量：{filled_qty}") if self.log_manager else None
                    self.log_manager.system.debug(f"  手续费：{commission} {commission_asset}") if self.log_manager else None
                    self._add_action("开仓成交", f"{side} @ {entry_price} x {filled_qty} | 手续费 {commission} {commission_asset}")

                    self.sync_account()
                    self.position = {
                        'side': side,
                        'entry_price': entry_price,
                        'size': filled_qty,
                        'time': datetime.now()
                    }
                    self.pending_order = None

                    if self.logger:
                        self.logger.record_signal(side, entry_price, self.orderbook)

                    asyncio.create_task(self._safe_place_tp_sl_orders())
            
            elif order_status == 'CANCELED':
                # 开仓挂单被撤销（可能是部分成交后超时）
                self._add_action("开仓撤销", "开仓挂单已撤销")
                self.pending_order = None
                self._order_placed_at = None
        
        # 止盈单成交 → 撤销止损单和保底止损单
        elif self.tp_order and self.tp_order.get('orderId') == order_id:
            if order_status == 'FILLED':
                # 计算真实 PnL
                fill_price = Decimal(order_data.get('ap', '0'))  # 成交均价
                fill_qty = Decimal(order_data.get('z', '0'))  # 成交数量
                commission = Decimal(order_data.get('fc', '0'))  # 手续费
                commission_asset = order_data.get('fs', 'USDC')
                
                if self.position:
                    if self.tp_order['side'] == 'SELL':  # 多单止盈
                        pnl = (fill_price - self.position['entry_price']) * fill_qty - commission
                    else:  # 空单止盈
                        pnl = (self.position['entry_price'] - fill_price) * fill_qty - commission
                    
                    self.log_manager.system.debug(f"[止盈单成交] 止盈单已成交！") if self.log_manager else None
                    self.log_manager.system.debug(f"  成交价：{fill_price}, 成交量：{fill_qty}") if self.log_manager else None
                    self.log_manager.system.debug(f"  手续费：{commission} {commission_asset}") if self.log_manager else None
                    self.log_manager.system.debug(f"[平仓盈亏] PnL: {pnl:+.6f} USDT") if self.log_manager else None
                    self._add_action("止盈成交", f"PnL: {pnl:+.6f} USDT")

                    # 刷新历史持仓
                    asyncio.create_task(self._refresh_history_delayed())

                    # 记录信号结果
                    if self.logger:
                        duration = (datetime.now() - self.position['time']).total_seconds() if 'time' in self.position else 0
                        self.logger.update_signal_result('TP', float(pnl), duration)

                    # 先同步余额再记录，确保余额是最新的
                    self.sync_account()

                    # 记录平仓后账户余额（用于复合收益率计算）
                    if self.logger:
                        self.logger.log_balance('position_closed', self.available_balance, {
                            'reason': 'TP',
                            'pnl': float(pnl),
                            'entry_price': float(self.position['entry_price']),
                            'close_price': float(fill_price)
                        })

                self._cancel_other_orders(exclude='tp')
                self.tp_order = None
            elif order_status in ['CANCELED', 'EXPIRED']:
                self.log_manager.system.debug(f"[止盈单取消] 止盈单已取消/过期") if self.log_manager else None
                self.tp_order = None
        
        # 止损单成交 → 撤销止盈单和保底止损单
        elif self.sl_order and self.sl_order.get('algoId') == order_id:
            if order_status == 'FILLED':
                # 计算真实 PnL
                fill_price = Decimal(order_data.get('ap', '0'))
                fill_qty = Decimal(order_data.get('z', '0'))
                commission = Decimal(order_data.get('fc', '0'))
                commission_asset = order_data.get('fs', 'USDC')
                
                if self.position:
                    if self.sl_order['side'] == 'SELL':  # 多单止损
                        pnl = (fill_price - self.position['entry_price']) * fill_qty - commission
                    else:  # 空单止损
                        pnl = (self.position['entry_price'] - fill_price) * fill_qty - commission
                    
                    self.log_manager.system.debug(f"[止损单成交] 止损单已成交！") if self.log_manager else None
                    self.log_manager.system.debug(f"  成交价：{fill_price}, 成交量：{fill_qty}") if self.log_manager else None
                    self.log_manager.system.debug(f"  手续费：{commission} {commission_asset}") if self.log_manager else None
                    self.log_manager.system.debug(f"[平仓盈亏] PnL: {pnl:+.6f} USDT") if self.log_manager else None
                    self._add_action("止损成交", f"PnL: {pnl:+.6f} USDT")

                    # 刷新历史持仓
                    asyncio.create_task(self._refresh_history_delayed())

                    # 记录信号结果
                    if self.logger:
                        duration = (datetime.now() - self.position['time']).total_seconds() if 'time' in self.position else 0
                        self.logger.update_signal_result('SL', float(pnl), duration)

                    # 先同步余额再记录，确保余额是最新的
                    self.sync_account()

                    # 记录平仓后账户余额（用于复合收益率计算）
                    if self.logger:
                        self.logger.log_balance('position_closed', self.available_balance, {
                            'reason': 'SL',
                            'pnl': float(pnl),
                            'entry_price': float(self.position['entry_price']),
                            'close_price': float(fill_price)
                        })

                self._cancel_other_orders(exclude='sl')
                self.sl_order = None
            elif order_status in ['CANCELED', 'EXPIRED']:
                self.log_manager.system.debug(f"[止损单取消] 止损单已取消/过期") if self.log_manager else None
                self.sl_order = None
        
        # 保底止损成交 → 撤销止盈单和止损单
        elif self.stop_market_order and self.stop_market_order.get('algoId') == order_id:
            if order_status == 'FILLED':
                # 计算真实 PnL
                fill_price = Decimal(order_data.get('ap', '0'))
                fill_qty = Decimal(order_data.get('z', '0'))
                commission = Decimal(order_data.get('fc', '0'))
                commission_asset = order_data.get('fs', 'USDC')
                
                if self.position:
                    if self.stop_market_order['side'] == 'SELL':  # 多单保底止损
                        pnl = (fill_price - self.position['entry_price']) * fill_qty - commission
                    else:  # 空单保底止损
                        pnl = (self.position['entry_price'] - fill_price) * fill_qty - commission
                    
                    self.log_manager.system.debug(f"[保底止损成交] 保底止损已成交！") if self.log_manager else None
                    self.log_manager.system.debug(f"  成交价：{fill_price}, 成交量：{fill_qty}") if self.log_manager else None
                    self.log_manager.system.debug(f"  手续费：{commission} {commission_asset}") if self.log_manager else None
                    self.log_manager.system.debug(f"[平仓盈亏] PnL: {pnl:+.6f} USDT") if self.log_manager else None
                    self._add_action("保底止损成交", f"PnL: {pnl:+.6f} USDT")

                    # 刷新历史持仓
                    asyncio.create_task(self._refresh_history_delayed())

                    # 记录信号结果
                    if self.logger:
                        duration = (datetime.now() - self.position['time']).total_seconds() if 'time' in self.position else 0
                        self.logger.update_signal_result('STOP_MARKET', float(pnl), duration)

                    # 先同步余额再记录，确保余额是最新的
                    self.sync_account()

                    # 记录平仓后账户余额（用于复合收益率计算）
                    if self.logger:
                        self.logger.log_balance('position_closed', self.available_balance, {
                            'reason': 'STOP_MARKET',
                            'pnl': float(pnl),
                            'entry_price': float(self.position['entry_price']),
                            'close_price': float(fill_price)
                        })

                self._cancel_other_orders(exclude='stop_market')
                self.stop_market_order = None
            elif order_status in ['CANCELED', 'EXPIRED']:
                self.log_manager.system.debug(f"[保底止损取消] 保底止损已取消/过期") if self.log_manager else None
                self.stop_market_order = None
        
        elif self.early_close_order:
            # 提前平仓单可能是 orderId 或 algoId，都检查一下
            early_close_id = self.early_close_order.get('orderId') or self.early_close_order.get('algoId')
            if early_close_id and early_close_id == order_id:
                if order_status == 'FILLED':
                    # 计算真实 PnL
                    fill_price = Decimal(order_data.get('ap', '0'))
                    fill_qty = Decimal(order_data.get('z', '0'))
                    commission = Decimal(order_data.get('fc', '0'))
                    commission_asset = order_data.get('fs', 'USDC')
                    
                    if self.position:
                        if self.early_close_order['side'] == 'SELL':  # 多单平仓
                            pnl = (fill_price - self.position['entry_price']) * fill_qty - commission
                        else:  # 空单平仓
                            pnl = (self.position['entry_price'] - fill_price) * fill_qty - commission
                        
                        self.log_manager.system.debug(f"[提前平仓成交] 提前平仓已成交！") if self.log_manager else None
                        self.log_manager.system.debug(f"  成交价：{fill_price}, 成交量：{fill_qty}") if self.log_manager else None
                        self.log_manager.system.debug(f"  手续费：{commission} {commission_asset}") if self.log_manager else None
                        self.log_manager.system.debug(f"[平仓盈亏] PnL: {pnl:+.6f} USDT") if self.log_manager else None
                        self._add_action("提前平仓成交", f"PnL: {pnl:+.6f} USDT")

                        # 刷新历史持仓
                        asyncio.create_task(self._refresh_history_delayed())

                        # 记录信号结果
                        if self.logger:
                            duration = (datetime.now() - self.position['time']).total_seconds() if 'time' in self.position else 0
                            self.logger.update_signal_result('MANUAL', float(pnl), duration)

                        # 先同步余额再记录，确保余额是最新的
                        self.sync_account()

                        # 记录平仓后账户余额（用于复合收益率计算）
                        if self.logger:
                            self.logger.log_balance('position_closed', self.available_balance, {
                                'reason': 'MANUAL',
                                'pnl': float(pnl),
                                'entry_price': float(self.position['entry_price']),
                                'close_price': float(fill_price)
                            })

                    # 撤销所有止盈止损单
                    self._cancel_other_orders(exclude='none')
                    self.early_close_order = None
                elif order_status in ['CANCELED', 'EXPIRED']:
                    self.log_manager.system.debug(f"[提前平仓取消] 提前平仓已取消/过期") if self.log_manager else None
                    self.early_close_order = None
        
        # Fallback: 未知订单成交，但持仓被清空 → 可能是止损单触发的限价单成交
        elif order_status == 'FILLED' and self.position:
            # 检查是否是止损方向的订单
            order_side = order_data.get('S', '')
            self.log_manager.system.debug(f"[平仓检测-Fallback] FILLED 订单方向={order_side}, 持仓方向={self.position['side']}") if self.log_manager else None
            if (self.position['side'] == 'LONG' and order_side == 'SELL') or \
               (self.position['side'] == 'SHORT' and order_side == 'BUY'):
                fill_price = Decimal(order_data.get('ap', '0'))
                fill_qty = Decimal(order_data.get('z', '0'))
                commission = Decimal(order_data.get('fc', '0'))
                
                if self.position['side'] == 'LONG':
                    pnl = (fill_price - self.position['entry_price']) * fill_qty - commission
                else:
                    pnl = (self.position['entry_price'] - fill_price) * fill_qty - commission
                
                duration = (datetime.now() - self.position['time']).total_seconds() if 'time' in self.position else 0
                
                self.log_manager.system.debug(f"[订单成交] 未知订单成交（可能是止损触发）！") if self.log_manager else None
                self.log_manager.system.debug(f"  成交价：{fill_price}, 成交量：{fill_qty}") if self.log_manager else None
                self.log_manager.system.debug(f"[平仓盈亏] PnL: {pnl:+.6f} USDT") if self.log_manager else None
                self._add_action("止损成交", f"PnL: {pnl:+.6f} USDT")

                # 刷新历史持仓
                asyncio.create_task(self._refresh_history_delayed())

                # 记录信号结果（修复 Bug：之前这里缺失导致 signals.csv 没有记录）
                if self.logger:
                    self.logger.update_signal_result('SL', float(pnl), duration)

                # 先同步余额再记录，确保余额是最新的
                self.sync_account()

                # 记录平仓后账户余额（用于复合收益率计算）
                if self.logger:
                    self.logger.log_balance('position_closed', self.available_balance, {
                        'reason': 'SL_FALLBACK',
                        'pnl': float(pnl),
                        'entry_price': float(self.position['entry_price']),
                        'close_price': float(fill_price)
                    })

                # 清空订单状态
                self._cancel_other_orders(exclude='none')
        
        # 移动止损订单成交（algo 单不在 tp/sl/stop_market 中，需单独处理）
        elif self.trailing_stop_manager and order_id in self.trailing_stop_manager.active_orders.values():
            if order_status == 'FILLED':
                fill_price = Decimal(order_data.get('ap', '0'))
                fill_qty = Decimal(order_data.get('z', '0'))
                commission = Decimal(order_data.get('fc', '0'))

                if self.position:
                    entry_price = self.position['entry_price']
                    size = self.position['size']
                    if self.position['side'] == 'LONG':
                        pnl = (fill_price - entry_price) * fill_qty - commission
                    else:
                        pnl = (entry_price - fill_price) * fill_qty - commission

                    duration = (datetime.now() - self.position['time']).total_seconds() if 'time' in self.position else 0

                    self.log_manager.system.debug(f"[移动止损成交] 移动止损单已成交！") if self.log_manager else None
                    self.log_manager.system.debug(f"  成交价：{fill_price}, 成交量：{fill_qty}") if self.log_manager else None
                    self.log_manager.system.debug(f"[平仓盈亏] PnL: {pnl:+.6f} USDT") if self.log_manager else None
                    self._add_action("移动止损成交", f"PnL: {pnl:+.6f} USDT")

                    # 刷新历史持仓
                    asyncio.create_task(self._refresh_history_delayed())

                    if self.logger:
                        self.logger.update_signal_result('TS', float(pnl), duration)

                    # 先同步余额再记录，确保余额是最新的
                    self.sync_account()

                    self.logger.log_balance('position_closed', self.available_balance, {
                        'reason': 'TS',
                        'pnl': float(pnl),
                        'entry_price': float(entry_price),
                        'close_price': float(fill_price)
                    })

                # 清理所有订单和持仓状态
                self._cancel_other_orders(exclude='none')

        # 任何订单成交（全部或部分）→ 触发持仓同步
        if order_status in ['FILLED', 'PARTIALLY_FILLED']:
            asyncio.create_task(self._sync_position())

    def _route_batch_order_update(self, order_data: dict, order_status: str, order_id: int):
        """v1.9.0：分批模式订单事件路由"""
        if order_status == 'FILLED':
            if order_id in self._batch_order_map:
                self._on_batch_event('BATCH_FILLED', order_data, 'WS')
            elif order_id in self._batch_tp_map:
                self._on_batch_event('TP_FILLED', order_data, 'WS')
            elif order_id == self.batch_state.get('sl_order_id'):
                self._on_batch_event('SL_FILLED', order_data, 'WS')
            elif order_id == self.batch_state.get('sm_order_id'):
                self._on_batch_event('SM_FILLED', order_data, 'WS')
            elif order_id == self.batch_state.get('early_close_order_id'):
                self._handle_batch_early_close_fill(order_data)
        elif order_status in ('EXPIRED', 'CANCELED', 'REJECTED'):
            if order_id in self._batch_order_map:
                self._on_batch_event('ORDER_EXPIRED', order_data, 'WS')
            elif order_id in self._batch_tp_map:
                # 止盈单被取消 → 从 map 中移除
                self._batch_tp_map.pop(order_id, None)

    def _handle_batch_early_close_fill(self, order_data: dict):
        """v1.9.0：分批模式提前平仓单成交"""
        bs = self.batch_state
        if not bs or bs.get('round_closed'):
            return
        fill_price = Decimal(str(order_data.get('ap', '0')))
        fill_qty = Decimal(str(order_data.get('z', '0')))
        commission = Decimal(str(order_data.get('fc', '0')))
        side = bs['side']
        avg_entry = bs['weighted_avg_entry']

        if side == 'LONG':
            pnl = (fill_price - avg_entry) * fill_qty - commission
        else:
            pnl = (avg_entry - fill_price) * fill_qty - commission

        self._add_action("提前平仓成交", f"{side} @ {fill_price} | PnL: {pnl:+.6f} USDT")
        self.sync_account()
        asyncio.create_task(self._cleanup_batch_state(reason="提前平仓"))

    async def _sync_position(self):
        """查询并同步实际持仓状态"""
        # v1.9.0：分批模式下跳过，避免覆盖 batch_state
        if self.batch_state and self.batch_state.get('enabled'):
            return
        await asyncio.sleep(0.3)  # 等待 0.3 秒让币安更新

        try:
            positions = self.api.get_position(self.symbol)
            
            # 汇总所有持仓方向的总量（双向持仓模式）
            total_position_amt = Decimal('0')
            entry_price = Decimal('0')
            unrealized_pnl = Decimal('0')
            
            for pos in positions:
                if pos['symbol'] == self.symbol:
                    position_amt = Decimal(pos['positionAmt'])
                    total_position_amt += position_amt
                    if position_amt != 0:
                        entry_price = Decimal(pos['entryPrice']) if pos['entryPrice'] else Decimal('0')
                        unrealized_pnl = Decimal(pos.get('unRealizedProfit', '0'))
            
            # 判断是否有持仓（汇总所有方向）
            if total_position_amt == 0:
                # 无持仓 → 清空本地状态 + 撤销所有挂单
                if self.position:
                    self.log_manager.system.debug(f"[持仓同步-_sync] 交易所无持仓，清理本地状态（持仓方向={self.position.get('side')}）") if self.log_manager else None
                    self.sync_account()
                    self._add_action("持仓同步成交", "持仓已清空，撤销所有挂单")

                    # 批量撤销所有订单（普通订单 + 条件单）
                    try:
                        self.api.cancel_all_open_orders(self.symbol)
                        self.log_manager.system.debug(f"[批量撤销] 已撤销所有订单") if self.log_manager else None
                    except Exception as e:
                        self.log_manager.system.debug(f"[批量撤销失败] {e}") if self.log_manager else None

                    # 清空本地状态
                    self.position = None
                    self.tp_order = None
                    self.sl_order = None
                    self.stop_market_order = None
                    if self.trailing_stop_manager:
                        asyncio.create_task(self.trailing_stop_manager.on_position_closed())
                    if self.loss_protection_manager:
                        self.loss_protection_manager.on_position_closed()

                    # 刷新历史持仓（延迟等 Binance 同步）
                    asyncio.create_task(self._refresh_history_delayed())
            
            else:
                # 有持仓 → 更新状态
                side = 'LONG' if total_position_amt > 0 else 'SHORT'
                actual_size = abs(total_position_amt)
                
                # 检查是否有变化
                if self.position:
                    if self.position['side'] != side or self.position['size'] != actual_size:
                        self.log_manager.system.debug(f"[持仓同步] 更新持仓：{side} {actual_size} @ {entry_price}") if self.log_manager else None
                        self.log_manager.system.debug(f"[浮动盈亏] PnL: {unrealized_pnl:+.2f} USDT") if self.log_manager else None
                        self._add_action("持仓更新", f"{side} {actual_size} @ {entry_price} | PnL: {unrealized_pnl:+.2f}")
                
                # 保留原始开仓时间，不被同步覆盖
                old_time = self.position.get('time') if self.position else None
                self.position = {
                    'side': side,
                    'entry_price': entry_price,
                    'size': actual_size,
                    'time': old_time or datetime.now()
                }
        
        except Exception as e:
            self.log_manager.system.debug(f"[持仓同步失败] {e}") if self.log_manager else None
    
    def _cancel_other_orders(self, exclude: str):
        """
        撤销其他订单（批量撤销）
        
        Args:
            exclude: 保留的订单类型 ('tp', 'sl', 'stop_market')
        """
        try:
            # 批量撤销所有订单（普通订单 + 条件单）
            self.log_manager.system.debug(f"[批量撤销] 撤销所有订单...") if self.log_manager else None
            self.api.cancel_all_open_orders(self.symbol)

            # 主动刷新账户余额
            self.sync_account()

            # 清空本地状态
            if exclude != 'tp':
                self.tp_order = None
            if exclude != 'sl':
                self.sl_order = None
            if exclude != 'stop_market':
                self.stop_market_order = None

            self.early_close_order = None
            self.position = None
            self._key_prices = []
            self._key_price_triggered.clear()

            # v1.5.0 新增：清理移动止损和浮亏保护状态
            if self.trailing_stop_manager:
                asyncio.create_task(self.trailing_stop_manager.on_position_closed())
            if self.loss_protection_manager:
                self.loss_protection_manager.on_position_closed()

            self.log_manager.system.debug("✓ 其他订单已撤销，持仓已清空") if self.log_manager else None
        
        except Exception as e:
            self.log_manager.system.debug(f"✗ 撤销订单失败：{e}") if self.log_manager else None
    
    def _add_action(self, action: str, details: str):
        """添加操作日志（同时写入文件）"""
        # 根据 action 触发音效
        self._trigger_sound(action)

        # 平仓动作：附加最大浮盈浮亏
        close_actions = ('止盈成交', '止损成交', '保底止损成交', '提前平仓成交',
                         '移动止损成交', '手动平仓成交', '持仓同步成交', '浮亏保护成交')
        is_close = any(kw in action for kw in close_actions)
        if is_close:
            max_pnl = getattr(self, '_max_float_pnl', None)
            max_pnl_price = getattr(self, '_max_float_pnl_price', None)
            min_pnl = getattr(self, '_min_float_pnl', None)
            min_pnl_price = getattr(self, '_min_float_pnl_price', None)
            extras = []
            if max_pnl is not None and max_pnl_price is not None:
                extras.append(f"最大浮盈 {max_pnl:+.6f}U @ {max_pnl_price:.2f}")
            if min_pnl is not None and min_pnl_price is not None:
                extras.append(f"最大浮亏 {min_pnl:+.6f}U @ {min_pnl_price:.2f}")
            if extras:
                details = f"{details} | {' | '.join(extras)}"

        # 内存日志（用于 TUI 显示）
        self.action_log.append({'time': datetime.now(), 'action': action, 'details': details})
        if len(self.action_log) > 20:
            self.action_log = self.action_log[-20:]
        
        # 写入文件日志
        if self.logger:
            # 根据 action 类型写入不同的日志文件
            if '成交' in action:
                if '开仓' in action:
                    # 解析开仓信息： "LONG @ 2151.12 x 0.011 | 手续费 0 USDC"
                    try:
                        parts = details.split()
                        side = parts[0] if len(parts) > 0 else ''
                        # 找到 @ 符号的位置
                        at_index = parts.index('@') if '@' in parts else -1
                        if at_index >= 0 and at_index + 1 < len(parts):
                            price = float(parts[at_index + 1])
                        else:
                            price = 0.0
                        # 找到 x 符号的位置
                        x_index = parts.index('x') if 'x' in parts else -1
                        if x_index >= 0 and x_index + 1 < len(parts):
                            size = float(parts[x_index + 1])
                        else:
                            size = 0.0
                        
                        self.logger.log_trade('开仓成交', {'details': details})
                        self.logger.log_position(side, price, size, 0)
                        # 新版 TradingLogger
                        if self.log_manager:
                            self.log_manager.trading.log_position_open(side, price, size)
                    except Exception as e:
                        # 解析失败，记录原始日志
                        self.logger.log_trade('开仓成交', {'details': details, 'error': str(e)})
                elif '止盈' in action:
                    pnl = float(details.split('PnL:')[1].strip().split(' ')[0]) if 'PnL:' in details else 0
                    self.logger.log_trade('止盈成交', {'details': details, 'pnl': pnl})
                    self.logger.log_pnl('止盈成交', pnl)
                    self.logger.log_position('CLOSE', 0, 0, pnl)
                elif '止损' in action:
                    pnl = float(details.split('PnL:')[1].strip().split(' ')[0]) if 'PnL:' in details else 0
                    self.logger.log_trade('止损成交', {'details': details, 'pnl': pnl})
                    self.logger.log_pnl('止损成交', pnl)
                    self.logger.log_position('CLOSE', 0, 0, pnl)
                elif '平仓' in action or '保护' in action:
                    pnl = float(details.split('PnL:')[1].strip().split(' ')[0]) if 'PnL:' in details else 0
                    self.logger.log_trade('平仓成交', {'details': details, 'pnl': pnl})
                    self.logger.log_pnl('平仓成交', pnl)
                    self.logger.log_position('CLOSE', 0, 0, pnl)
                else:
                    # 其他成交类型（持仓同步成交等）
                    pnl = float(details.split('PnL:')[1].strip().split(' ')[0]) if 'PnL:' in details else 0
                    self.logger.log_trade(action, {'details': details, 'pnl': pnl})
                    self.logger.log_pnl(action, pnl)
                    self.logger.log_position('CLOSE', 0, 0, pnl)
                # 新版 TradingLogger — 统一下平仓日志
                if is_close and self.log_manager:
                    self._log_close_to_trading_logger(action, details)
            elif '挂单' in action or '已下' in action:
                self.logger.log_order(action, {'details': details})
            else:
                # 其他操作日志（持仓超时、撤销挂单、开仓撤销 等）
                self.logger.log_trade(action, {'details': details})
    
    def _log_close_to_trading_logger(self, action: str, details: str):
        """将平仓事件写入新版 TradingLogger"""
        try:
            import re as _re
            pos = self.position
            if not pos:
                return
            side = pos.get('side', '')
            entry_price = float(pos.get('entry_price', 0))
            size = float(pos.get('size', 0))
            pnl = 0.0
            m = _re.search(r'PnL:\s*([+-]?[\d.]+)', details)
            if m:
                pnl = float(m.group(1))
            exit_price = 0.0
            m = _re.search(r'平仓均价\s*([\d.]+)', details)
            if m:
                exit_price = float(m.group(1))
            duration_sec = None
            pos_time = pos.get('time')
            if pos_time:
                duration_sec = (datetime.now() - pos_time).total_seconds()
            pnl_pct = (pnl / (entry_price * size) * 100) if entry_price and size else 0.0

            reason_map = {
                '止盈': 'TP',
                '止损': 'SL',
                '保底止损': 'STOP_MARKET',
                '提前平仓': 'MANUAL',
                '移动止损': 'TS',
                '手动平仓': 'MANUAL',
                '持仓同步': 'SYNC',
                '浮亏保护': 'LOSS_PROTECTION',
            }
            reason = 'UNKNOWN'
            for kw, mapped in reason_map.items():
                if kw in action:
                    reason = mapped
                    break

            max_pnl = getattr(self, '_max_float_pnl', None)
            max_pnl_price = getattr(self, '_max_float_pnl_price', None)
            min_pnl = getattr(self, '_min_float_pnl', None)
            min_pnl_price = getattr(self, '_min_float_pnl_price', None)

            self.log_manager.trading.log_position_close(
                side=side, exit_price=exit_price, size=size,
                pnl=pnl, pnl_pct=pnl_pct, reason=reason,
                entry_price=entry_price, duration_sec=duration_sec,
                max_float_pnl=float(max_pnl) if max_pnl is not None else None,
                max_float_pnl_price=float(max_pnl_price) if max_pnl_price is not None else None,
                min_float_pnl=float(min_pnl) if min_pnl is not None else None,
                min_float_pnl_price=float(min_pnl_price) if min_pnl_price is not None else None,
            )
        except Exception:
            pass

    async def _safe_place_tp_sl_orders(self):
        """安全地下止盈止损单（包装异常处理）"""
        try:
            await self.place_tp_sl_orders()
        except Exception as e:
            self.log_manager.system.debug(f"\n[止盈止损异常] {e}") if self.log_manager else None
            self._add_action("止盈止损异常", str(e))
    
    async def place_tp_sl_orders(self):
        """开仓成功后，放置止盈止损单（使用 config_manager 配置）"""
        if not self.position:
            self.log_manager.system.debug("✗ 止盈止损失败：无持仓") if self.log_manager else None
            return
        
        entry_price = self.position['entry_price']
        side = self.position['side']
        size = self.position['size']
        
        self.log_manager.system.debug(f"\n[止盈止损] 开始下单...") if self.log_manager else None
        self.log_manager.system.debug(f"  持仓：{side}, 开仓价={entry_price}, 数量={size}") if self.log_manager else None
        self._add_action("止盈止损开始", f"{side} @ {entry_price} x {size}")
        
        try:
            # 1. 获取强平价（快速重试 3 次，每次间隔 0.3 秒）
            liquidation_price = None
            for retry in range(3):
                self.log_manager.system.debug(f"[1/3] 获取强平价... (尝试 {retry + 1}/3)") if self.log_manager else None
                positions = self.api.get_position(self.symbol)
                
                for pos in positions:
                    if pos['symbol'] == self.symbol and Decimal(pos['positionAmt']) != 0:
                        liquidation_price = Decimal(pos['liquidationPrice'])
                        break
                
                if liquidation_price and liquidation_price != Decimal('0'):
                    self.log_manager.system.debug(f"  ✓ 强平价有效：{liquidation_price}") if self.log_manager else None
                    break
                else:
                    if retry < 2:  # 最后一次不等待
                        self.log_manager.system.debug(f"  ⚠ 强平价无效，等待 0.3 秒后重试...") if self.log_manager else None
                        await asyncio.sleep(0.3)
            
            # 2. 从 config_manager 获取止盈止损配置
            if self.config_manager:
                # 获取 ATR(14) 值（用于 ATR14 模式）
                atr = None
                if self.indicators:
                    atr = self.indicators.volatility.get_atr(14)
                tp_price = self.config_manager.get_take_profit_price(entry_price, side, atr)
                sl_trigger, sl_algo_params = self.config_manager.get_stop_loss_params(self.symbol, entry_price, side, size, atr)
                
                # 计算保底止损价格（基于用户配置的最大损失比例）
                # 使用总权益 = 开仓后可用余额 + 开仓保证金（因为开仓后 position_margin 可能还没更新）
                # 开仓保证金 = 仓位价值 / API 杠杆（Binance 按 API 杠杆冻结保证金）
                api_leverage, _ = self.config_manager.get_leverage_config()
                position_value = entry_price * size
                position_margin_required = position_value / Decimal(api_leverage)
                total_equity = self.available_balance + position_margin_required
                self.log_manager.system.debug(f"\n[保底止损计算] 总权益={total_equity:.6f} USDT (可用={self.available_balance:.6f} + 本单保证金={position_margin_required:.6f})") if self.log_manager else None
                sm_price = self.config_manager.get_stop_market_price(
                    entry_price, side, size, total_equity, liquidation_price or Decimal('0')
                )
                self.log_manager.system.debug(f"[保底止损计算] 保底价格={sm_price:.2f}, 强平价={liquidation_price or 'N/A'}") if self.log_manager else None
                
                # 如果强平价有效，和强平价 +1 比较，取更安全的
                if liquidation_price and liquidation_price != Decimal('0'):
                    if side == 'LONG':
                        # 多单：保底价格不能低于强平价 +1
                        liquidation_floor = liquidation_price + Decimal('1')
                        if sm_price < liquidation_floor:
                            self.log_manager.system.debug(f"  保底价格 {sm_price:.2f} 低于强平价 +1 ({liquidation_floor:.2f})，使用强平价 +1") if self.log_manager else None
                            sm_price = liquidation_floor
                    else:
                        # 空单：保底价格不能高于强平价 -1
                        liquidation_floor = liquidation_price - Decimal('1')
                        if sm_price > liquidation_floor:
                            self.log_manager.system.debug(f"  保底价格 {sm_price:.2f} 高于强平价 -1 ({liquidation_floor:.2f})，使用强平价 -1") if self.log_manager else None
                            sm_price = liquidation_floor
                
                self.log_manager.system.debug(f"  配置止盈：{tp_price}, 止损触发：{sl_trigger}, 保底：{sm_price} (强平价={liquidation_price or 'N/A'})") if self.log_manager else None
            else:
                # 兼容模式：使用硬编码值
                tp_price = entry_price + Decimal('1')
                sl_trigger = entry_price - Decimal('3')
                sm_price = liquidation_price + Decimal('1') if liquidation_price else Decimal('0')
            
            # 3. 止盈单（LIMIT 限价单，保证 Maker）
            self.log_manager.system.debug(f"[2/3] 下止盈单...") if self.log_manager else None
            if side == 'LONG':
                tp_side = 'SELL'
            else:
                tp_side = 'BUY'
            
            # 检查是否会变成 Taker
            use_queue = False
            if tp_side == 'SELL':
                # 卖出限价单：如果止盈价 <= 买一价，会立即被吃掉
                if self.orderbook.get('bids') and tp_price <= self.orderbook['bids'][0][0]:
                    use_queue = True
            else:
                # 买入限价单：如果止盈价 >= 卖一价，会立即被吃掉
                if self.orderbook.get('asks') and tp_price >= self.orderbook['asks'][0][0]:
                    use_queue = True
            
            if use_queue:
                # 会立即成交，改用 QUEUE
                tp_order = self.api.place_order(
                    symbol=self.symbol,
                    side=tp_side,
                    type='LIMIT',
                    priceMatch='QUEUE',  # 同向价 1，保证 Maker
                    quantity=str(size),
                    timeInForce='GTC',
                    positionSide=side
                )
                
                # QUEUE 模式下，保存实际挂单价格（从订单响应中获取）
                actual_price = Decimal(tp_order.get('price', '0'))
                self._add_action("止盈单已下", f"{tp_side} QUEUE @ {actual_price:.2f}")
                self.log_manager.system.debug(f"✓ 止盈单已下：{tp_side} QUEUE @ {actual_price:.2f}（目标价 {tp_price} 会被吃）") if self.log_manager else None
            else:
                # 不会立即成交，用目标价
                tp_order = self.api.place_order(
                    symbol=self.symbol,
                    side=tp_side,
                    type='LIMIT',
                    price=str(tp_price),
                    quantity=str(size),
                    timeInForce='GTC',
                    positionSide=side
                )
                self._add_action("止盈单已下", f"{tp_side} @ {tp_price}")
                self.log_manager.system.debug(f"✓ 止盈单已下：{tp_side} @ {tp_price}") if self.log_manager else None
                actual_price = tp_price
            
            self.tp_order = {
                'orderId': tp_order['orderId'],
                'side': tp_side,
                'price': actual_price,  # 保存实际挂单价格
                'type': 'LIMIT'
            }
            
            # 3. 止损单（使用 config_manager 配置）
            self.log_manager.system.debug(f"[3/3] 下止损单...") if self.log_manager else None
            sl_order = None  # 初始化变量
            
            if self.config_manager and sl_algo_params:
                # 从配置读取止损参数
                self.log_manager.system.debug(f"  止损参数：{sl_algo_params}") if self.log_manager else None
                try:
                    sl_order = self.api.place_algo_order(**sl_algo_params)
                    actual_price = Decimal(sl_order.get('price', '0'))
                    sl_trigger_display = sl_algo_params.get('triggerPrice', sl_trigger)
                    self.log_manager.system.debug(f"  ✓ 止损单已下：algoId={sl_order.get('algoId')}") if self.log_manager else None
                except Exception as e:
                    self.log_manager.system.debug(f"  ✗ 止损单失败：{e}") if self.log_manager else None
                    sl_trigger_display = sl_trigger
                    actual_price = Decimal('0')
            else:
                # 兼容模式：硬编码
                if side == 'LONG':
                    sl_trigger = entry_price - Decimal('3')
                    sl_side = 'SELL'
                else:
                    sl_trigger = entry_price + Decimal('3')
                    sl_side = 'BUY'
                
                try:
                    sl_order = self.api.place_algo_order(
                        symbol=self.symbol,
                        side=sl_side,
                        type='STOP',
                        triggerPrice=str(sl_trigger),
                        priceMatch='QUEUE',
                        quantity=str(size),
                        timeInForce='GTC',
                        workingType='CONTRACT_PRICE',
                        positionSide=side
                    )
                    actual_price = Decimal(sl_order.get('price', '0'))
                    sl_trigger_display = sl_trigger
                    self.log_manager.system.debug(f"  ✓ 止损单已下：algoId={sl_order.get('algoId')}") if self.log_manager else None
                except Exception as e:
                    self.log_manager.system.debug(f"  ✗ 止损单失败：{e}") if self.log_manager else None
                    sl_trigger_display = sl_trigger
                    actual_price = Decimal('0')
            
            # 只有止损单成功才记录
            if sl_order:
                # 确定止损单方向
                if self.config_manager and sl_algo_params:
                    sl_side = sl_algo_params.get('side', 'SELL' if side == 'LONG' else 'BUY')
                else:
                    sl_side = 'SELL' if side == 'LONG' else 'BUY'
                
                self.sl_order = {
                    'algoId': sl_order['algoId'],
                    'side': sl_side,
                    'trigger': sl_trigger,
                    'price': actual_price,
                    'type': 'STOP'
                }
                
                self._add_action("止损单已下", f"触发={sl_trigger_display}, 限价={actual_price:.2f}")
                self.log_manager.system.debug(f"✓ 止损单已下：触发={sl_trigger_display}, 限价={actual_price:.2f}, algoId={sl_order['algoId']}") if self.log_manager else None
            else:
                self._add_action("止损单失败", "跳过记录")
            
            # 4. 保底止损（使用 config_manager 配置）
            # print(f"\n=== 调试：止损单之后，准备下保底止损 ===")
            # print(f"=== sm_price={sm_price}, type={type(sm_price)}")
            # print(f"[4/3] 下保底止损...")
            # print(f"  side={side}, sm_price={sm_price}, size={size}")
            # print(f"  available_balance={self.available_balance}, liquidation_price={liquidation_price}")
            
            if side == 'LONG':
                sm_side = 'SELL'
            else:
                sm_side = 'BUY'
            
            # 保底止损参数（价格保留 2 位小数）
            sm_params = {
                'symbol': self.symbol,
                'side': sm_side,
                'type': 'STOP_MARKET',
                'triggerPrice': str(sm_price.quantize(Decimal('0.01'))),  # 保留 2 位小数
                'quantity': str(size.quantize(Decimal('0.001'))),  # 保留 3 位小数
                'workingType': 'MARK_PRICE',
                'positionSide': side
            }
            
            # print(f"  保底止损参数：{sm_params}")
            
            try:
                # print(f"  调用 place_algo_order...")
                stop_order = self.api.place_algo_order(**sm_params)
                # print(f"  API 返回：{stop_order}")
                self.log_manager.system.debug(f"  ✓ 保底止损已下：algoId={stop_order.get('algoId')}") if self.log_manager else None
                
                self.stop_market_order = {
                    'algoId': stop_order['algoId'],
                    'side': sm_side,
                    'trigger': sm_price,
                    'liquidation': liquidation_price or Decimal('0'),
                    'type': 'STOP_MARKET'
                }
                
                liq_display = f"{liquidation_price:.2f}" if liquidation_price else "N/A"
                self._add_action("保底止损已下", f"强平价={liq_display}, 触发={sm_price:.2f}")
                self.log_manager.system.debug(f"✓ 保底止损已下：强平价={liq_display}, 触发={sm_price}, algoId={stop_order['algoId']}") if self.log_manager else None
            except Exception as e:
                self.log_manager.system.debug(f"  ✗ 保底止损失败：{type(e).__name__}: {e}") if self.log_manager else None
            
            self.log_manager.system.debug("\n✓ 止盈止损单全部下达完成") if self.log_manager else None

            # 重建关键价格表
            self._rebuild_key_prices()

            # v1.5.0 新增：初始化移动止损和浮亏保护
            if self.trailing_stop_manager:
                await self.trailing_stop_manager.on_position_opened(
                    entry_price=entry_price,
                    take_profit_price=tp_price,
                    side=side,
                    position_size=size
                )
            
            if self.loss_protection_manager:
                self.loss_protection_manager.set_entry_info(
                    entry_price=entry_price,
                    side=side,
                    tp_price=tp_price,
                    sl_price=sl_trigger if 'sl_trigger' in locals() else None,
                    tp_order_id=self.tp_order['orderId'] if self.tp_order else None
                )
        
        except BinanceAPIError as e:
            self.log_manager.system.debug(f"\n✗ 止盈止损下单失败：[{e.code}] {e.msg}") if self.log_manager else None
            self.log_manager.system.debug(f"  错误详情：{e}") if self.log_manager else None
            self._add_action("止盈止损错误", f"[{e.code}] {e.msg}")
            raise  # 重新抛出异常
        except Exception as e:
            self.log_manager.system.debug(f"\n✗ 止盈止损错误：{e}") if self.log_manager else None
            self.log_manager.system.debug(f"  错误详情：{type(e).__name__}: {e}") if self.log_manager else None
            self._add_action("止盈止损错误", str(e))
            raise  # 重新抛出异常
    
    async def place_take_profit_with_retry(self, side: str, size: Decimal, entry_price: Decimal, max_retries: int = 3):
        """止盈挂单（带重试）"""
        # 第一次：挂开仓价±1 点
        if side == 'LONG':
            tp_price = entry_price + Decimal('1')
            tp_side = 'SELL'
        else:
            tp_price = entry_price - Decimal('1')
            tp_side = 'BUY'
        
        try:
            tp_order = self.api.place_order(
                symbol=self.symbol,
                side=tp_side,
                type='LIMIT',
                price=str(tp_price),
                quantity=str(size),
                timeInForce='GTC',
                postOnly=True,
                positionSide=side
            )
            
            self.tp_order = {
                'orderId': tp_order['orderId'],
                'side': tp_side,
                'price': tp_price,
                'type': 'LIMIT'
            }
            
            self._add_action("止盈单已下", f"价格={tp_price}")
            self.log_manager.system.debug(f"✓ 止盈单已下：价格={tp_price}, ID={tp_order['orderId']}") if self.log_manager else None
            return True
        
        except BinanceAPIError as e:
            if e.code == -1128:
                self.log_manager.system.debug(f"[止盈重试] Post-Only 被拒，调整到盘口价重试...") if self.log_manager else None
                return await self._place_tp_at_market_price(side, size, max_retries)
            else:
                raise
    
    async def _place_tp_at_market_price(self, side: str, size: Decimal, max_retries: int = 3):
        """止盈重试：挂当前盘口价"""
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                if side == 'LONG':
                    if not self.orderbook.get('asks'):
                        await asyncio.sleep(0.1)
                        continue
                    tp_price = self.orderbook['asks'][0][0]
                    tp_side = 'SELL'
                else:
                    if not self.orderbook.get('bids'):
                        await asyncio.sleep(0.1)
                        continue
                    tp_price = self.orderbook['bids'][0][0]
                    tp_side = 'BUY'
                
                tp_order = self.api.place_order(
                    symbol=self.symbol,
                    side=tp_side,
                    type='LIMIT',
                    price=str(tp_price),
                    quantity=str(size),
                    timeInForce='GTC',
                    postOnly=True,
                    positionSide=side
                )
                
                self.tp_order = {
                    'orderId': tp_order['orderId'],
                    'side': tp_side,
                    'price': tp_price,
                    'type': 'LIMIT'
                }
                
                self._add_action("止盈单已下", f"价格={tp_price}（盘口价）")
                self.log_manager.system.debug(f"✓ 止盈单已下：价格={tp_price}（盘口价）, ID={tp_order['orderId']}") if self.log_manager else None
                return True
            
            except BinanceAPIError as e:
                if e.code == -1128:
                    retry_count += 1
                    self.log_manager.system.debug(f"[止盈重试] 第{retry_count}次重试...") if self.log_manager else None
                    await asyncio.sleep(0.1)
                else:
                    raise
        
        self.log_manager.system.debug("✗ 止盈单重试失败") if self.log_manager else None
        self._add_action("止盈失败", "Post-Only 重试超过最大次数")
        return False

    async def _open_position_batch(self, side: str) -> bool:
        """v1.9.0：分批模式开仓/补单入口"""
        if self.last_price is None:
            if self.log_manager:
                self.log_manager.system.debug("✗ 暂无价格数据")
            return False

        # 已有持仓 → 补单
        if self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed'):
            # 检查方向
            if self.batch_state['side'] != side:
                if self.log_manager:
                    self.log_manager.system.debug(f"✗ 方向不匹配：当前 {self.batch_state['side']}，拒绝 {side}")
                return False
            await self._supplement_batch_orders()
            return True

        # 全新开仓
        MIN_NOTIONAL = Decimal('20')
        contract_value = self.available_balance * self.actual_leverage
        total_size = (contract_value / self.last_price).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
        notional_value = total_size * self.last_price

        if total_size <= 0 or notional_value < MIN_NOTIONAL:
            if self.log_manager:
                self.log_manager.system.debug(f"✗ 名义价值不足（最小 20 USDC）")
            return False

        self._init_batch_state(side)
        bs = self.batch_state
        bs['target_notional'] = notional_value

        # 计算阶梯价格
        base_price = self.last_price
        prices = self._calculate_batch_prices(base_price)
        sizes = self._calculate_batch_sizes(total_size)

        # 构建批次列表
        for i in range(bs['total_count']):
            bs['batches'].append({
                'index': i,
                'order_id': None,
                'client_order_id': '',
                'price': prices[i],
                'size': sizes[i],
                'status': 'pending',
                'fill_time': None,
                'tp_order_id': None,
                'tp_price': None,
                'gtx_rejected': False,
            })

        self._add_action("分批开仓", f"{side} {bs['total_count']} 笔，总量 {total_size} ETH")
        await self._place_batch_orders()
        return True

    async def open_position(self, side: str) -> bool:
        """开仓（v1.9.0：分批模式分流）"""
        # v1.9.0：分批模式
        if self.config_manager and self.config_manager.is_batch_mode_enabled():
            return await self._open_position_batch(side)

        if self.position is not None or self.pending_order is not None:
            self.log_manager.system.debug("✗ 已有持仓或挂单") if self.log_manager else None
            return False

        if self.last_price is None:
            self.log_manager.system.debug("✗ 暂无价格数据") if self.log_manager else None
            return False

        try:
            # 币安要求：最小名义价值 20 USDC
            MIN_NOTIONAL = Decimal('20')
            
            # 计算仓位（全仓进出）
            contract_value = self.available_balance * self.actual_leverage
            size = (contract_value / self.last_price).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
            
            if size <= 0:
                self.log_manager.system.debug("✗ 余额不足") if self.log_manager else None
                return False
            
            # 验证名义价值（仓位价值 = 数量 × 价格）
            notional_value = size * self.last_price
            if notional_value < MIN_NOTIONAL:
                self.log_manager.system.debug(f"✗ 名义价值不足（最小 20 USDC，计算值{notional_value:.2f} USDC）") if self.log_manager else None
                self.log_manager.system.debug(f"  当前：保证金{self.available_balance:.2f}U × {self.actual_leverage}x = {notional_value:.2f}U") if self.log_manager else None
                self.log_manager.system.debug(f"  提示：提高杠杆倍数 或 增加保证金") if self.log_manager else None
                return False
            
            if side == 'LONG':
                if not self.orderbook.get('bids'):
                    return False
                # 多单挂买一价（QUEUE 自动选择）
            
            position_side = 'LONG' if side == 'LONG' else 'SHORT'
            
            order = self.api.place_order(
                symbol=self.symbol,
                side='BUY' if side == 'LONG' else 'SELL',
                type='LIMIT',
                priceMatch='QUEUE',  # 同向价 1，保证 Maker
                quantity=str(size),
                timeInForce='GTC',
                positionSide=position_side
            )
            
            # 获取实际挂单价格
            actual_price = Decimal(order.get('price', '0'))
            
            self.pending_order = {
                'orderId': order['orderId'],
                'side': side,
                'price': actual_price,
                'size': Decimal(order['origQty']),
                'status': order['status']
            }
            self._order_placed_at = time.time()
            self._fill_check_done = False
            
            self.log_manager.system.debug(f"✓ 挂单成功：{side} QUEUE @ {actual_price:.2f}, 订单 ID: {order['orderId']}") if self.log_manager else None
            self._add_action("开仓挂单", f"{side} {size} QUEUE @ {actual_price:.2f}")
            return True
        
        except BinanceAPIError as e:
            if e.code == -1128:
                self.log_manager.system.debug("[Post-Only 被拒] 价格穿透，调整价格重新挂单") if self.log_manager else None
                return await self.open_position(side)
            else:
                self.log_manager.system.debug(f"✗ 开仓失败：[{e.code}] {e.msg}") if self.log_manager else None
                self._add_action("开仓错误", f"[{e.code}] {e.msg}")
                return False
        except Exception as e:
            self.log_manager.system.debug(f"✗ 开仓错误：{e}") if self.log_manager else None
            self._add_action("开仓错误", str(e))
            return False
    
    def cancel_open_order(self) -> bool:
        """撤销开仓挂单（v1.9.0：分批模式分流）"""
        # v1.9.0：分批模式
        if self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed'):
            bs = self.batch_state
            if bs.get('state') == 'early_close' and bs.get('early_close_order_id'):
                return self._cancel_batch_early_close()
            # 否则撤销所有未成交批次
            asyncio.create_task(self._cancel_all_pending_batches())
            return True

        if not self.pending_order:
            return False
        try:
            self.api.cancel_order(self.symbol, self.pending_order['orderId'])
            self.pending_order = None
            # 撤单后保证金释放，立即同步余额以便后续下单
            self.sync_account()
            self.log_manager.system.debug("✓ 已撤销开仓挂单") if self.log_manager else None
            self._add_action("撤销挂单", "撤销开仓挂单")
            return True
        except:
            return False
    
    async def close_position_market(self) -> bool:
        """市价全平仓位（Z 键，v1.9.0：分批模式分流）"""
        # v1.9.0：分批模式
        if self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed'):
            return await self._close_position_market_batch()

        if not self.position:
            self.log_manager.system.debug("✗ 无持仓，无法平仓") if self.log_manager else None
            return False

        side = self.position['side']
        size = self.position['size']
        entry_price = self.position['entry_price']
        
        self.log_manager.system.debug(f"\n[Z 键平仓] 市价全平 {side} {size}...") if self.log_manager else None
        self._add_action("Z 键平仓", f"市价全平 {side} {size}")
        
        try:
            # 1. 撤销所有挂单（普通订单 + Algo Order）
            self.log_manager.system.debug("  撤销所有挂单...") if self.log_manager else None
            self.api.cancel_all_orders(self.symbol)
            # 撤销所有 Algo Order（止损单、保底止损单）
            self.api.cancel_all_open_orders(self.symbol)
            self.log_manager.system.debug("  ✓ 已撤销所有挂单") if self.log_manager else None
            
            # 2. 市价全平
            close_side = 'SELL' if side == 'LONG' else 'BUY'
            self.log_manager.system.debug(f"  市价 {close_side} {size}...") if self.log_manager else None
            
            close_order = self.api.place_order(
                symbol=self.symbol,
                side=close_side,
                type='MARKET',
                quantity=str(size),
                positionSide=side
            )
            
            # 获取成交价
            close_price = Decimal(close_order.get('avgPrice', '0'))
            if close_price == 0:
                close_price = Decimal(close_order.get('price', '0'))
            
            # 3. 获取手续费（从订单响应中获取）
            # 币安 API 返回：commission 和 commissionAsset
            fills = close_order.get('fills', [])
            commission = Decimal('0')
            commission_asset = 'USDC'
            
            # 调试：打印订单响应
            self.log_manager.system.debug(f"  [DEBUG] 订单响应：{close_order}") if self.log_manager else None
            
            if fills:
                # 从成交明细中累加手续费
                for fill in fills:
                    comm = Decimal(fill.get('commission', '0'))
                    asset = fill.get('commissionAsset', 'USDC')
                    self.log_manager.system.debug(f"  [DEBUG] 手续费：{comm} {asset}") if self.log_manager else None
                    if asset == 'USDC':
                        commission += comm
                        commission_asset = asset
            else:
                # 没有 fills，尝试直接从订单响应获取
                commission = Decimal(close_order.get('commission', '0'))
                commission_asset = close_order.get('commissionAsset', 'USDC')
            
            # 4. 计算 PnL（实现盈亏 - 手续费）
            if side == 'LONG':
                pnl = (close_price - entry_price) * size - commission
            else:
                pnl = (entry_price - close_price) * size - commission
            
            pnl_str = f"{pnl:+.6f} USDT"
            self.log_manager.system.debug(f"  ✓ 已市价全平：{close_side} {size} @ {close_price}") if self.log_manager else None
            self.log_manager.system.debug(f"  手续费：{commission:.6f} {commission_asset}") if self.log_manager else None
            self.log_manager.system.debug(f"  PnL: {pnl_str}") if self.log_manager else None
            
            self._add_action("手动平仓成交", f"{close_side} {size} @ {close_price} | 手续费 {commission:.6f} {commission_asset} | PnL: {pnl_str}")

            # 市价单成交后立即同步余额
            self.sync_account()

            # 记录平仓后账户余额（用于复合收益率计算）
            if self.logger:
                self.logger.log_balance('position_closed', self.available_balance, {
                    'reason': 'MARKET_CLOSE',
                    'pnl': float(pnl),
                    'entry_price': float(entry_price),
                    'close_price': float(close_price),
                    'commission': float(commission)
                })

            # 清除持仓状态
            self.position = None
            self.tp_order = None
            self.sl_order = None
            self.stop_market_order = None
            self.early_close_order = None
            self.pending_order = None
            self._key_prices = []
            self._key_price_triggered.clear()

            if self.trailing_stop_manager:
                asyncio.create_task(self.trailing_stop_manager.on_position_closed())
            if self.loss_protection_manager:
                self.loss_protection_manager.on_position_closed()

            # 刷新历史持仓（延迟等 Binance 同步）
            asyncio.create_task(self._refresh_history_delayed())

            return True
        except Exception as e:
            self.log_manager.system.debug(f"  ✗ 市价全平失败：{e}") if self.log_manager else None
            self._add_action("市价全平失败", str(e))
            return False
    
    async def close_position_early(self, retry_count: int = 0) -> bool:
        """提前平仓（带重试，v1.9.0：分批模式分流）"""
        # v1.9.0：分批模式
        if self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed'):
            await self._batch_early_close()
            return True

        if not self.position:
            self.log_manager.system.debug("✗ 无持仓") if self.log_manager else None
            return False

        if self.early_close_order:
            self.log_manager.system.debug("✗ 已有提前平仓挂单") if self.log_manager else None
            return False

        try:
            self.tp_order_backup = self.tp_order
            self.sl_order_backup = self.sl_order
            
            # 只撤销止盈单（因为要平仓了，止盈单没用了）
            # 止损单保留：如果提前平仓单没成交，止损单还能保护
            if self.tp_order:
                tp_id = self.tp_order.get('orderId') or self.tp_order.get('algoId')
                if tp_id:
                    try:
                        self.api.cancel_order(self.symbol, tp_id)
                        self.tp_order = None
                        self.log_manager.system.debug(f"[提前平仓] 已撤销止盈单") if self.log_manager else None
                    except BinanceAPIError as e:
                        # -2011: 订单不存在（可能已被成交、撤销或状态未同步）
                        if e.code == -2011:
                            self.log_manager.system.debug(f"[提前平仓] 止盈单已不存在（[{e.code}] {e.msg}），跳过撤单") if self.log_manager else None
                            self.tp_order = None
                        else:
                            raise
            
            # 止损单不撤销，保留保护
            
            side = self.position['side']
            if side == 'LONG':
                if not self.orderbook.get('asks'):
                    return False
                order_side = 'SELL'
            else:
                if not self.orderbook.get('bids'):
                    return False
                order_side = 'BUY'
            
            order = self.api.place_order(
                symbol=self.symbol,
                side=order_side,
                type='LIMIT',
                priceMatch='QUEUE',  # 同向价 1，保证 Maker
                quantity=str(self.position['size']),
                timeInForce='GTC',
                positionSide=side
            )
            
            self.early_close_order = {
                'orderId': order['orderId'],
                'side': order_side,
                'price': Decimal(order.get('price', '0')),  # 保存实际挂单价格
                'type': 'LIMIT'
            }
            
            actual_price = Decimal(order.get('price', '0'))
            self.log_manager.system.debug(f"✓ 提前平仓挂单：{order_side} QUEUE @ {actual_price:.2f}") if self.log_manager else None
            self._add_action("提前平仓挂单", f"{order_side} QUEUE @ {actual_price:.2f}")
            self._rebuild_key_prices()
            return True
        
        except BinanceAPIError as e:
            if e.code == -1128 and retry_count < 3:
                self.log_manager.system.debug(f"[提前平仓重试] 订单被拒，第{retry_count+1}次重试...") if self.log_manager else None
                await asyncio.sleep(0.1)
                return await self.close_position_early(retry_count + 1)
            else:
                self.log_manager.system.debug(f"✗ 提前平仓失败：[{e.code}] {e.msg}") if self.log_manager else None
                self._add_action("提前平仓错误", f"[{e.code}] {e.msg}")
                return False
        except Exception as e:
            self.log_manager.system.debug(f"✗ 提前平仓错误：{e}") if self.log_manager else None
            return False
    
    def cancel_early_close(self) -> bool:
        """撤销提前平仓，恢复原止盈单（v1.9.0：分批模式分流）"""
        # v1.9.0：分批模式
        if self.batch_state and self.batch_state.get('enabled'):
            return self._cancel_batch_early_close()

        if not self.early_close_order:
            return False
        try:
            # 撤销提前平仓单
            order_id = self.early_close_order.get('orderId') or self.early_close_order.get('algoId')
            if order_id:
                self.api.cancel_order(self.symbol, order_id)
                self.early_close_order = None
                self.log_manager.system.debug("✓ 已撤销提前平仓挂单") if self.log_manager else None
            
            # 恢复原止盈单
            if self.tp_order_backup:
                self.log_manager.system.debug(f"[恢复止盈] 重新挂止盈单...") if self.log_manager else None
                tp_price = self.tp_order_backup.get('price')
                tp_side = self.tp_order_backup.get('side')
                size = self.position['size'] if self.position else Decimal('0')
                
                if tp_price and tp_side and size:
                    tp_order = self.api.place_order(
                        symbol=self.symbol,
                        side=tp_side,
                        type='LIMIT',
                        price=str(tp_price),
                        quantity=str(size),
                        timeInForce='GTC',
                        positionSide=self.position['side']
                    )
                    
                    self.tp_order = {
                        'orderId': tp_order['orderId'],
                        'side': tp_side,
                        'price': tp_price,
                        'type': 'LIMIT'
                    }
                    
                    self.log_manager.system.debug(f"✓ 已恢复止盈单：{tp_side} @ {tp_price}") if self.log_manager else None
                    self._add_action("恢复止盈单", f"{tp_side} @ {tp_price}")
            
            self.tp_order_backup = None
            self.sl_order_backup = None
            self._rebuild_key_prices()

            return True
        except Exception as e:
            self.log_manager.system.debug(f"✗ 撤销提前平仓失败：{e}") if self.log_manager else None
            return False
    
    def update_price(self, price: Decimal):
        """更新最新价格（v1.9.0：含分批模式浮盈追踪）"""
        self.last_price = price
        # 持仓期间追踪最大浮盈/浮亏
        entry = None
        size = None
        side = None

        if self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed'):
            bs = self.batch_state
            entry = bs.get('weighted_avg_entry')
            size = bs.get('total_filled_size')
            side = bs.get('side')
        elif self.position:
            entry = self.position['entry_price']
            size = self.position['size']
            side = self.position['side']

        if entry and size and side and self.last_price:
            if side == 'LONG':
                pnl = (price - entry) * size
            else:
                pnl = (entry - price) * size
            if not hasattr(self, '_max_float_pnl') or self._max_float_pnl is None or pnl > self._max_float_pnl:
                self._max_float_pnl = pnl
                self._max_float_pnl_price = price
            if not hasattr(self, '_min_float_pnl') or self._min_float_pnl is None or pnl < self._min_float_pnl:
                self._min_float_pnl = pnl
                self._min_float_pnl_price = price
    
    def update_orderbook(self, bids: List, asks: List):
        """更新深度数据"""
        self.orderbook = {
            'bids': [[Decimal(p), Decimal(q)] for p, q in bids[:10]],
            'asks': [[Decimal(p), Decimal(q)] for p, q in asks[:10]]
        }


    def _rebuild_key_prices(self):
        """根据当前所有挂单重建关键价格表（v1.9.0：含分批模式）"""
        self._key_prices = []

        # v1.9.0：分批模式
        if self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed'):
            bs = self.batch_state
            side = bs['side']
            for b in bs.get('batches', []):
                if b['status'] == 'pending' and b.get('order_id'):
                    self._key_prices.append({
                        'type': 'BATCH_OPEN',
                        'price': b['price'],
                        'order_id': b['order_id'],
                        'order_category': 'LIMIT',
                    })
                elif b['status'] == 'tp_placed' and b.get('tp_price') and b.get('tp_order_id'):
                    self._key_prices.append({
                        'type': 'BATCH_TP',
                        'price': b['tp_price'],
                        'order_id': b['tp_order_id'],
                        'order_category': 'LIMIT',
                    })
            if bs.get('sl_order_id'):
                sl_price = self.batch_state.get('weighted_avg_entry', Decimal('0'))
                if sl_price > 0:
                    self._key_prices.append({
                        'type': 'BATCH_SL',
                        'price': sl_price,
                        'order_id': bs['sl_order_id'],
                        'order_category': 'ALGO',
                    })
            if bs.get('sm_order_id'):
                sm_price = Decimal('0')  # SM 价格由 config 计算
                self._key_prices.append({
                    'type': 'BATCH_SM',
                    'price': sm_price,
                    'order_id': bs['sm_order_id'],
                    'order_category': 'ALGO',
                })
            if bs.get('early_close_order_id'):
                ec_price = self.last_price or Decimal('0')
                self._key_prices.append({
                    'type': 'BATCH_EARLY_CLOSE',
                    'price': ec_price,
                    'order_id': bs['early_close_order_id'],
                    'order_category': 'LIMIT',
                })
            return

        if not self.position:
            return

        side = self.position['side']

        # TP 止盈单
        if self.tp_order:
            tp_price = Decimal(str(self.tp_order.get('price', 0)))
            if tp_price > 0:
                self._key_prices.append({
                    'type': 'TP',
                    'price': tp_price,
                    'order_id': self.tp_order.get('orderId') or self.tp_order.get('algoId'),
                    'order_category': 'LIMIT',
                })

        # SL 止损单（algo order）
        if self.sl_order:
            sl_trigger = Decimal(str(self.sl_order.get('stopPrice', 0)))
            if sl_trigger > 0:
                self._key_prices.append({
                    'type': 'SL',
                    'price': sl_trigger,
                    'order_id': self.sl_order.get('algoId'),
                    'order_category': 'ALGO',
                })

        # 保底止损（algo order）
        if self.stop_market_order:
            sm_trigger = Decimal(str(self.stop_market_order.get('stopPrice', 0)))
            if sm_trigger > 0:
                self._key_prices.append({
                    'type': 'STOP_MARKET',
                    'price': sm_trigger,
                    'order_id': self.stop_market_order.get('algoId'),
                    'order_category': 'ALGO',
                })

        # 提前平仓单
        if self.early_close_order:
            ec_price = Decimal(str(self.early_close_order.get('price', 0)))
            if ec_price > 0:
                self._key_prices.append({
                    'type': 'EARLY_CLOSE',
                    'price': ec_price,
                    'order_id': self.early_close_order.get('orderId') or self.early_close_order.get('algoId'),
                    'order_category': 'LIMIT',
                })

        # 移动止损（trailing stop manager 的活跃订单）
        if self.trailing_stop_manager:
            for level, algo_id in self.trailing_stop_manager.active_orders.items():
                trigger_price = self.trailing_stop_manager.get_trigger_price_for_level(level)
                if trigger_price is not None:
                    self._key_prices.append({
                        'type': 'TRAILING_STOP',
                        'price': trigger_price,
                        'order_id': algo_id,
                        'order_category': 'ALGO',
                    })

        # 浮亏保护 STOP 订单
        if self.loss_protection_manager:
            lp = self.loss_protection_manager
            if hasattr(lp, '_breakeven_stop_id') and lp._breakeven_stop_id:
                self._key_prices.append({
                    'type': 'LOSS_PROTECTION',
                    'price': self.position['entry_price'],  # 保护价 = 开仓价
                    'order_id': lp._breakeven_stop_id,
                    'order_category': 'ALGO',
                })
            if hasattr(lp, '_grid1_stop_id') and lp._grid1_stop_id and self.trailing_stop_manager:
                grid1_price = self.trailing_stop_manager.grid_prices[0] if self.trailing_stop_manager.grid_prices else None
                if grid1_price:
                    self._key_prices.append({
                        'type': 'LOSS_PROTECTION',
                        'price': grid1_price,
                        'order_id': lp._grid1_stop_id,
                        'order_category': 'ALGO',
                    })

    async def _check_key_prices(self):
        """每帧调用：订单簿 vs 关键价格比对，穿透时 REST 确认平仓（v1.9.0：含分批模式）"""
        has_position = self.position is not None
        has_batch = self.batch_state and self.batch_state.get('enabled') and not self.batch_state.get('round_closed')
        if (not has_position and not has_batch) or not self._key_prices:
            return

        orderbook = self.orderbook
        if not orderbook.get('bids') or not orderbook.get('asks'):
            return

        best_bid = orderbook['bids'][0][0]
        best_ask = orderbook['asks'][0][0]
        side = self.position['side'] if has_position else (self.batch_state['side'] if has_batch else '')

        for kp in self._key_prices:
            oid = kp['order_id']
            if oid is None or oid in self._key_price_triggered:
                continue

            price = kp['price']
            kp_type = kp['type']
            triggered = False

            # 根据持仓方向和订单类型判断是否穿透
            if side == 'LONG':
                if kp_type in ('TP', 'EARLY_CLOSE', 'BATCH_TP', 'BATCH_EARLY_CLOSE', 'BATCH_OPEN'):
                    if best_bid >= price:
                        triggered = True
                elif kp_type in ('SL', 'STOP_MARKET', 'TRAILING_STOP', 'LOSS_PROTECTION', 'BATCH_SL', 'BATCH_SM'):
                    if best_bid <= price:
                        triggered = True
            else:  # SHORT
                if kp_type in ('TP', 'EARLY_CLOSE', 'BATCH_TP', 'BATCH_EARLY_CLOSE', 'BATCH_OPEN'):
                    if best_ask <= price:
                        triggered = True
                elif kp_type in ('SL', 'STOP_MARKET', 'TRAILING_STOP', 'LOSS_PROTECTION', 'BATCH_SL', 'BATCH_SM'):
                    if best_ask >= price:
                        triggered = True

            if not triggered:
                continue

            # 标记为已触发，防止重复查 REST
            self._key_price_triggered.add(oid)

            # REST 确认：查交易所持仓
            try:
                positions = self.api.get_position(self.symbol)
                has_position = False
                for pos in positions:
                    if Decimal(pos.get('positionAmt', 0)) != 0:
                        has_position = True
                        break

                if has_position:
                    # 持仓还在，订单可能未成交，重置触发标记
                    if self.log_manager:
                        self.log_manager.system.debug(
                            f"[关键价格] {kp_type} 价格 {price} 穿透，但持仓仍在，订单可能未成交"
                        )
                    self._key_price_triggered.discard(oid)
                else:
                    # 持仓已消失 → 确认平仓
                    if self.log_manager:
                        self.log_manager.system.debug(
                            f"[关键价格] {kp_type} 价格 {price} 穿透，持仓已平仓"
                        )
                    await self._on_key_price_close(kp_type)
                    return  # 已平仓，不再继续检查

            except Exception as e:
                if self.log_manager:
                    self.log_manager.system.debug(f"[关键价格] REST 确认失败：{e}")
                self._key_price_triggered.discard(oid)

    async def _on_key_price_close(self, trigger_type: str):
        """关键价格触发平仓后的处理：算 PnL、日志、清理挂单（v1.9.0：含分批模式）"""
        # v1.9.0：分批模式
        if self.batch_state and self.batch_state.get('enabled'):
            entry_price = self.batch_state.get('weighted_avg_entry', Decimal('0'))
            size = self.batch_state.get('total_filled_size', Decimal('0'))
            side = self.batch_state.get('side', '')
            pos_time = None
            for b in self.batch_state.get('batches', []):
                if b.get('fill_time'):
                    pos_time = b['fill_time']
                    break
        else:
            entry_price = self.position['entry_price']
            size = self.position['size']
            side = self.position['side']
            pos_time = self.position.get('time') if self.position else None

        # 查最近成交获取平仓价和手续费
        exit_price = Decimal('0')
        commission = Decimal('0')
        try:
            start_ms = int(pos_time.timestamp() * 1000) - 2000 if pos_time else None
            fills = self.api.get_fills(self.symbol, limit=5, startTime=start_ms)
            total_fill_qty = Decimal('0')
            for fill in fills:
                fill_side = fill.get('side', '')
                fill_qty = Decimal(fill.get('qty', '0'))
                fill_price = Decimal(fill.get('price', '0'))
                fill_comm = Decimal(fill.get('commission', '0'))
                expected_side = 'SELL' if side == 'LONG' else 'BUY'
                if fill_side == expected_side:
                    total_fill_qty += fill_qty
                    if exit_price == 0:
                        exit_price = fill_price
                    else:
                        exit_price = (exit_price * (total_fill_qty - fill_qty) + fill_price * fill_qty) / total_fill_qty
                    commission += fill_comm
                    if total_fill_qty >= size:
                        break
        except Exception as e:
            if self.log_manager:
                self.log_manager.system.debug(f"[关键价格] 获取成交记录失败：{e}")

        # 计算 PnL
        if exit_price > 0:
            if side == 'LONG':
                pnl = (exit_price - entry_price) * size - commission
            else:
                pnl = (entry_price - exit_price) * size - commission
        else:
            pnl = Decimal('0')

        pnl_str = f"{pnl:+.6f} USDT"

        # 撤销交易所所有剩余挂单
        try:
            self.api.cancel_all_orders(self.symbol)
            self.api.cancel_all_open_orders(self.symbol)
            if self.log_manager:
                self.log_manager.system.debug("[关键价格] 已撤销交易所所有挂单")
        except Exception as e:
            if self.log_manager:
                self.log_manager.system.debug(f"[关键价格] 撤单失败：{e}")

        # 日志和状态清理
        action_name = {
            'TP': '止盈成交',
            'SL': '止损成交',
            'STOP_MARKET': '保底止损成交',
            'EARLY_CLOSE': '提前平仓成交',
            'TRAILING_STOP': '移动止损成交',
            'LOSS_PROTECTION': '浮亏保护成交',
        }.get(trigger_type, '平仓成交')

        self._add_action(action_name,
            f"PnL: {pnl_str} | 平仓均价 {exit_price:.2f}" if exit_price > 0 else f"PnL: {pnl_str}")
        self.sync_account()

        if self.logger:
            self.logger.log_balance('position_closed', self.available_balance, {
                'reason': f'KEY_PRICE_{trigger_type}',
                'pnl': float(pnl),
                'entry_price': float(entry_price),
                'close_price': float(exit_price),
                'commission': float(commission),
            })

        # 清空所有状态
        if self.batch_state:
            asyncio.create_task(self._cleanup_batch_state(reason=f"关键价格_{trigger_type}"))
        self.position = None
        self.tp_order = None
        self.sl_order = None
        self.stop_market_order = None
        self.early_close_order = None
        self.pending_order = None
        self._key_prices = []
        self._key_price_triggered.clear()

        if self.trailing_stop_manager:
            asyncio.create_task(self.trailing_stop_manager.on_position_closed())
        if self.loss_protection_manager:
            self.loss_protection_manager.on_position_closed()

        asyncio.create_task(self._refresh_history_delayed())

    async def check_pending_order_filled(self) -> bool:
        """
        v1.5.5 整合方案：bookTicker 穿透检测 + REST 确认 + 成交超时

        主循环每帧调用。逻辑：
        1. bookTicker 价格穿透 → 立即 REST 查订单状态确认
        2. 未穿透 → 不做任何 REST 请求（零权重消耗）
        3. 超时后按已成交量处理（部分成交也处理）

        Returns: True 如果检测到成交并触发了后续动作
        """
        if self.pending_order is None or self._fill_check_done:
            return False

        side = self.pending_order['side']
        order_price = self.pending_order['price']
        order_id = self.pending_order['orderId']
        now = time.time()

        # 记录挂单时间
        if self._order_placed_at is None:
            self._order_placed_at = now

        # 获取配置中的超时时间
        if self.config_manager:
            timeout_seconds = self.config_manager.get_order_timeout()
        else:
            timeout_seconds = 2.0

        elapsed = now - self._order_placed_at

        # 1. bookTicker 穿透检测
        price_penetrated = False
        if side == 'LONG':
            # 多单挂买：卖一价 <= 挂单价 → 穿透
            if self.orderbook.get('asks') and self.orderbook['asks'][0][0] <= order_price:
                price_penetrated = True
        else:
            # 空单挂卖：买一价 >= 挂单价 → 穿透
            if self.orderbook.get('bids') and self.orderbook['bids'][0][0] >= order_price:
                price_penetrated = True

        # 2. 穿透或超时 → REST 确认
        should_check = price_penetrated or elapsed >= timeout_seconds

        if not should_check:
            return False

        try:
            order_status = self.api.get_order(self.symbol, order_id)
            status = order_status.get('status', '')
            filled_qty = Decimal(order_status.get('executedQty', '0'))
            avg_price = Decimal(order_status.get('avgPrice', '0'))
            cum_quote = Decimal(order_status.get('cumQuote', '0'))
            commission = Decimal(order_status.get('cumCommission', '0'))

            if status == 'FILLED':
                original_size = self.pending_order.get('size', Decimal('0'))
                self._fill_check_done = True
                commission_asset = order_status.get('commissionAsset', 'USDC')

                # 校验成交量：FILLED 但成交量 < 挂单量，说明部分成交后取消
                if filled_qty < original_size and filled_qty > 0:
                    self.log_manager.system.debug(f"[成交检测] 部分成交：挂单 {original_size}，实际成交 {filled_qty}，剩余 {(original_size - filled_qty)} 已取消") if self.log_manager else None
                    self.sync_account()
                    self._add_action("部分成交确认", f"{side} @ {avg_price} x {filled_qty}/{original_size}")
                self._on_pending_filled(side, avg_price, filled_qty, commission, commission_asset)
                return True

            elif status == 'PARTIALLY_FILLED' and filled_qty > 0:
                original_size = self.pending_order.get('size', Decimal('0'))
                remaining = original_size - filled_qty
                # 部分成交
                if elapsed >= timeout_seconds:
                    # 超时了，撤销剩余，按已成交量处理
                    self.log_manager.system.debug(f"[超时撤单] 超时 {timeout_seconds}s，撤销剩余 {remaining}，保留已成交 {filled_qty}") if self.log_manager else None
                    self._fill_check_done = True
                    commission_asset = order_status.get('commissionAsset', 'USDC')
                    try:
                        self.api.cancel_order(self.symbol, order_id)
                        self._add_action("超时撤单", f"{side} 挂单 {original_size}，成交 {filled_qty}，撤销剩余 {remaining}")
                        self.sync_account()
                    except:
                        self._add_action("超时撤单失败", f"撤销失败，已成交 {filled_qty}")
                    self._on_pending_filled(side, avg_price, filled_qty, commission, commission_asset)
                    return True
                else:
                    # 还没超时，继续等待
                    return False

            elif status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                # 订单被取消/过期/拒绝
                self._fill_check_done = True
                if filled_qty > 0:
                    # 取消前有部分成交
                    commission_asset = order_status.get('commissionAsset', 'USDC')
                    self._on_pending_filled(side, avg_price, filled_qty, commission, commission_asset)
                else:
                    self._add_action("挂单取消", f"未成交 ({status})")
                    self.pending_order = None
                return True

            # 状态还是 NEW 或 PENDING，继续等待
            return False

        except Exception as e:
            # REST 查询失败，不打断流程
            self._add_action("成交检测失败", str(e))
            return False

    def _on_pending_filled(self, side: str, entry_price: Decimal,
                           filled_qty: Decimal, commission: Decimal,
                           commission_asset: str):
        """开仓挂单成交回调"""
        if entry_price == 0:
            return

        self._add_action("开仓成交", f"{side} @ {entry_price:.2f} x {filled_qty} | 手续费 {commission:.4f} {commission_asset}")

        self.sync_account()
        self.position = {
            'side': side,
            'entry_price': entry_price,
            'size': filled_qty,
            'time': datetime.now()
        }
        self.pending_order = None
        self._order_placed_at = None
        # 重置最大浮盈/浮亏追踪
        self._max_float_pnl = None
        self._max_float_pnl_price = None
        self._min_float_pnl = None
        self._min_float_pnl_price = None

        # 记录信号特征
        if self.logger:
            self.logger.record_signal(side, entry_price, self.orderbook)

        # 立即下止盈止损单
        asyncio.create_task(self._safe_place_tp_sl_orders())

        # 刷新历史持仓（延迟等 Binance 同步）
        asyncio.create_task(self._refresh_history_delayed())
    
    def sync_account(self):
        """同步账户信息（REST 调用，应在主循环中节流使用）"""
        try:
            account = self.api.get_account()
            for asset in account.get('assets', []):
                if asset['asset'] == 'USDC':
                    self.available_balance = Decimal(asset['availableBalance'])
                    # 兜底：如果 initialize 中未设置基数，此处设置
                    if not self._privacy_baseline_set and self.available_balance > 0:
                        self._privacy_baseline = self.available_balance
                        self._privacy_baseline_set = True
                    break
            self.position_margin = Decimal(account.get('totalPositionInitialMargin', 0))
            self.order_margin = Decimal(account.get('totalOpenOrderInitialMargin', 0))
        except:
            pass

    async def _ensure_bnb_prices(self):
        """增量更新 BNB 价格缓存（在线程池执行，避免阻塞事件循环）"""
        now_ms = int(datetime.now().timestamp() * 1000)
        if self._bnb_last_fetch_ms == 0:
            # 首次拉满 1500 根 1m K线
            try:
                klines = await asyncio.to_thread(self.api.get_klines, 'BNBUSDT', '1m', limit=1500)
                for k in klines:
                    ts = k[0] // 1000
                    self._bnb_prices[ts] = Decimal(str(k[4]))
                self._bnb_last_fetch_ms = max(k[0] for k in klines) if klines else now_ms
            except Exception as e:
                self.log_manager.system.warning(f"[BNB费率] K线拉取失败：{e}") if self.log_manager else None
        else:
            # 增量拉取：从上次最后时间到现在
            try:
                klines = await asyncio.to_thread(
                    self.api.get_klines, 'BNBUSDT', '1m',
                    startTime=self._bnb_last_fetch_ms, limit=500
                )
                for k in klines:
                    ts = k[0] // 1000
                    self._bnb_prices[ts] = Decimal(str(k[4]))
                if klines:
                    self._bnb_last_fetch_ms = max(k[0] for k in klines)
            except Exception as e:
                self.log_manager.system.debug(f"[BNB费率] 增量拉取失败：{e}") if self.log_manager else None

    async def _refresh_history_delayed(self, delay: float = 5.0):
        """延迟刷新历史持仓（等 Binance 同步 trade 记录）"""
        await asyncio.sleep(delay)
        await self.fetch_position_history()
        # 通知 Web UI 刷新历史列表
        ws = getattr(self, 'web_server', None)
        if ws:
            ws.push_event('history_updated', 'position_history_refreshed')

    async def fetch_position_history(self, days: int = 7):
        """从币安拉取成交记录，配对生成历史持仓（API 调用在线程池执行）"""
        try:
            import time as _time
            now_ms = int(_time.time() * 1000)
            start_ms = now_ms - days * 86400000

            # 1. 分页拉取所有成交（线程池，不阻塞事件循环）
            all_fills = []
            from_id = None
            while True:
                fills = await asyncio.to_thread(
                    self.api.get_fills, self.symbol, limit=1000,
                    startTime=start_ms, endTime=now_ms, fromId=from_id
                )
                if not fills:
                    break
                all_fills.extend(fills)
                if len(fills) < 1000:
                    break
                from_id = fills[-1].get('id') + 1
                if len(all_fills) > 10000:
                    break

            if not all_fills:
                return

            # 2. 拉取资费记录（线程池）
            funding = await asyncio.to_thread(
                self.api.get_income_history, self.symbol, incomeType='FUNDING_FEE',
                startTime=start_ms, endTime=now_ms, limit=1000
            )

            # 3. 按 positionSide 分组
            long_fills = [f for f in all_fills if f.get('positionSide') == 'LONG']
            short_fills = [f for f in all_fills if f.get('positionSide') == 'SHORT']

            # 4. 仅当存在 BNB 抵扣手续费时才拉取 BNB 价格缓存
            if any(f.get('commissionAsset') == 'BNB' for f in all_fills):
                await self._ensure_bnb_prices()

            # 5. 配对
            history = []
            history.extend(self._pair_positions('LONG', long_fills, funding))
            history.extend(self._pair_positions('SHORT', short_fills, funding))

            # 6. 排序
            STATUS_ORDER = {'未平仓': 0, '部分平仓': 1, '完全平仓': 2}
            history.sort(key=lambda x: (
                STATUS_ORDER.get(x.get('status', '完全平仓'), 2),
                -x.get('last_action_time_ms', 0),
            ))
            self.position_history.clear()
            self.position_history.extend(history[:500])  # v1.10.0: deque maxlen=500

            # 7. 更新 24h 交易统计（复用已拉取的 fills，避免重复 API 调用）
            self._update_trade_stats_24h(all_fills)

        except Exception as e:
            if self.log_manager:
                self.log_manager.system.error(f"[持仓历史] 拉取失败：{e}", exc_info=True)
            else:
                import traceback
                traceback.print_exc()

    def _get_bnb_price_for_time(self, trade_time_ms: int) -> Decimal:
        """根据成交时间戳获取 BNB 价格（缓存K线优先，失败回退实时价）"""
        ts = trade_time_ms // 1000
        if self._bnb_prices:
            minute_ts = (ts // 60) * 60
            if minute_ts in self._bnb_prices:
                return self._bnb_prices[minute_ts]
            for offset in range(1, 3):
                for candidate in (minute_ts - offset * 60, minute_ts + offset * 60):
                    if candidate in self._bnb_prices:
                        return self._bnb_prices[candidate]
        # 回退缓存实时价（60s 内不重复请求）
        now = time.time()
        if now - self._bnb_ticker_ts < 60 and self._bnb_ticker_price > 0:
            return self._bnb_ticker_price
        try:
            self._bnb_ticker_price = Decimal(str(self.api.get_ticker_price('BNBUSDT')))
            self._bnb_ticker_ts = now
            return self._bnb_ticker_price
        except Exception:
            return None

    def _convert_fee_usd(self, fill: dict) -> Decimal:
        """将单笔成交的手续费换算为 USD 等值（兼容 BNB 抵扣）"""
        fee = Decimal(str(fill.get('commission', '0')))
        fee_asset = fill.get('commissionAsset', 'USDC')
        if fee_asset == 'BNB':
            bnb_p = self._get_bnb_price_for_time(fill.get('time', 0))
            if bnb_p and bnb_p > 0:
                return fee * bnb_p
            # 所有价格查找都失败时保留原始 BNB 数量，不置零
            return fee
        if fee_asset not in ('USDC', 'USDT'):
            return Decimal('0')
        return fee

    def _pair_positions(self, side: str, fills: list, funding: list) -> list:
        """将同一方向的成交配对成持仓记录（一个开仓周期只生成一条最终记录）"""
        if not fills:
            return []

        positions = []
        current = None

        # 时间字段用 'time'（不是 tradeTime）
        for fill in sorted(fills, key=lambda x: x.get('time', 0)):
            price = Decimal(str(fill['price']))
            qty = Decimal(str(fill['qty']))
            fee_usd = self._convert_fee_usd(fill)
            realized_pnl = Decimal(str(fill.get('realizedPnl', '0')))
            trade_time_ms = fill.get('time', 0)
            trade_time = datetime.fromtimestamp(trade_time_ms / 1000)
            is_buyer = fill.get('buyer', False)

            # 判断开仓还是平仓
            is_open = (side == 'LONG' and is_buyer) or (side == 'SHORT' and not is_buyer)

            if is_open:
                if current is None:
                    current = {
                        'side': side,
                        'total_opened_qty': qty,
                        'total_close_qty': Decimal('0'),
                        'total_open_cost': price * qty,
                        'total_close_cost': Decimal('0'),
                        'total_fee': fee_usd,
                        'realized_pnl_sum': Decimal('0'),
                        'open_time': trade_time,
                        'open_time_ms': trade_time_ms,
                        'close_time': None,
                        'close_time_ms': None,
                    }
                else:
                    # 加仓
                    current['total_opened_qty'] += qty
                    current['total_open_cost'] += price * qty
                    current['total_fee'] += fee_usd
            else:
                if current:
                    current['total_close_qty'] += qty
                    current['total_close_cost'] += price * qty
                    current['realized_pnl_sum'] += realized_pnl
                    current['total_fee'] += fee_usd
                    current['close_time'] = trade_time
                    current['close_time_ms'] = trade_time_ms

                    if current['total_close_qty'] >= current['total_opened_qty']:
                        # 完全平仓：生成记录并结束当前持仓
                        open_avg = current['total_open_cost'] / current['total_opened_qty']
                        close_avg = current['total_close_cost'] / current['total_close_qty']
                        pos_funding = self._calc_funding(funding, current['open_time_ms'], current['close_time_ms'])
                        pnl = current['realized_pnl_sum'] - current['total_fee'] + pos_funding

                        duration_sec = (current['close_time_ms'] - current['open_time_ms']) / 1000
                        positions.append({
                            'side': side,
                            'status': '完全平仓',
                            'max_size': current['total_opened_qty'],
                            'closed_size': current['total_close_qty'],
                            'open_avg_price': open_avg,
                            'close_avg_price': close_avg,
                            'pnl': pnl,
                            'total_fee': current['total_fee'],
                            'funding_fee': pos_funding,
                            'open_time': current['open_time'],
                            'close_time': current['close_time'],
                            'last_action_time_ms': current['close_time_ms'],
                            'duration': duration_sec,
                        })
                        current = None
                    # 部分平仓：不在循环中生成记录，等遍历结束后统一生成

        # 遍历结束还有未平仓的 → 生成"未平仓"或"部分平仓"记录
        if current:
            open_avg = current['total_open_cost'] / current['total_opened_qty']

            # 计算从开仓到现在的资费
            now_ms = int(time.time() * 1000)
            pos_funding = self._calc_funding(funding, current['open_time_ms'], now_ms)

            pnl = current['realized_pnl_sum'] - current['total_fee'] + pos_funding

            if current['total_close_qty'] > 0:
                # 有部分平仓但还没平完
                status = '部分平仓'
                close_avg = current['total_close_cost'] / current['total_close_qty']
                close_time = current['close_time']
            else:
                # 完全没有平仓
                status = '未平仓'
                close_avg = None
                close_time = None

            end_ms = current['close_time_ms'] if current['close_time_ms'] else now_ms
            duration_sec = (end_ms - current['open_time_ms']) / 1000
            positions.append({
                'side': side,
                'status': status,
                'max_size': current['total_opened_qty'],
                'closed_size': current['total_close_qty'],
                'open_avg_price': open_avg,
                'close_avg_price': close_avg,
                'pnl': pnl,
                'total_fee': current['total_fee'],
                'funding_fee': pos_funding,
                'open_time': current['open_time'],
                'close_time': close_time,
                'last_action_time_ms': current['close_time_ms'] if current['close_time_ms'] else current['open_time_ms'],
                'duration': duration_sec,
            })

        return positions

    def _calc_funding(self, funding: list, open_ms: int, close_ms: int) -> Decimal:
        """计算持仓期间的资费"""
        pos_funding = Decimal('0')
        for f in funding:
            f_time = f.get('time', 0)
            if open_ms <= f_time <= close_ms:
                pos_funding += Decimal(str(f.get('income', '0')))
        return pos_funding

    def _update_trade_stats_24h(self, all_fills: list = None):
        """计算交易统计（按配置周期筛选 -- 近24小时或自然日）"""
        import time as _time
        now_ms = int(_time.time() * 1000)
        day_ms = 86400000

        # 读取统计周期配置
        if self.config_manager:
            stats_period = self.config_manager.get('stats_period', {})
            mode = stats_period.get('mode', '24h')
        else:
            mode = '24h'

        # 如果外部已传入 fills（来自 fetch_position_history），直接复用避免重复 API 调用
        if all_fills is None:
            pull_window_ms = 7 * day_ms
            try:
                all_fills = []
                from_id = None
                while True:
                    fills = self.api.get_fills(self.symbol, limit=1000, startTime=now_ms - pull_window_ms, endTime=now_ms, fromId=from_id)
                    if not fills:
                        break
                    all_fills.extend(fills)
                    if len(fills) < 1000:
                        break
                    from_id = fills[-1].get('id') + 1
                    if len(all_fills) > 10000:
                        break
            except Exception:
                self.trade_stats_24h = {'error': True}
                return

        if not all_fills:
            self.trade_stats_24h = {
                'open_count': 0, 'close_count': 0, 'win_count': 0,
                'win_rate': 0, 'total_volume': Decimal('0'),
                'total_pnl': Decimal('0'), 'avg_pnl_ratio': 0,
                'avg_hold_time': '--', 'expected_value': 0
            }
            return

        # 统计完整持仓轮次（每笔开仓 → 完全平仓算1轮）
        # 先用 7 天数据配对，再用平仓时间筛选 24 小时内
        if mode == 'calendar_day':
            tz_str = self.config_manager.get('stats_period.timezone', '+8') if self.config_manager else '+8'
            cutoff_24h = self._get_calendar_day_cutoff_ms(tz_str, now_ms)
        else:
            cutoff_24h = now_ms - day_ms
        completed_rounds = 0
        total_volume = Decimal('0')
        total_pnl = Decimal('0')
        win_count = 0
        loss_count = 0
        total_win_usd = Decimal('0')  # 累计盈利金额
        total_loss_usd = Decimal('0')  # 累计亏损金额
        hold_times = []

        # 按 direction 分组配对
        for side in ['LONG', 'SHORT']:
            side_fills = [f for f in all_fills if f.get('positionSide') == side]
            current_open = None
            for fill in sorted(side_fills, key=lambda x: x.get('time', 0)):
                price = Decimal(str(fill['price']))
                qty = Decimal(str(fill['qty']))
                realized_pnl = Decimal(str(fill.get('realizedPnl', '0')))
                fee_usd = self._convert_fee_usd(fill)
                is_buyer = fill.get('buyer', False)
                is_open = (side == 'LONG' and is_buyer) or (side == 'SHORT' and not is_buyer)
                trade_time_ms = fill.get('time', 0)

                if is_open:
                    if current_open is None:
                        current_open = {
                            'open_time_ms': trade_time_ms,
                            'total_qty': qty,
                            'opened_qty': qty,
                            'total_cost': price * qty,
                            'realized_pnl': Decimal('0'),
                            'total_fee': Decimal('0'),
                        }
                    else:
                        current_open['total_qty'] += qty
                        current_open['opened_qty'] += qty
                        current_open['total_cost'] += price * qty
                else:
                    if current_open:
                        current_open['total_qty'] -= qty
                        current_open['realized_pnl'] += realized_pnl
                        current_open['total_fee'] += fee_usd
                        current_open['close_time_ms'] = trade_time_ms

                        if current_open['total_qty'] <= 0:
                            close_ms = current_open.get('close_time_ms', trade_time_ms)
                            # 按最后平仓时间筛选：只统计 24 小时内完全平仓的轮次
                            if close_ms >= cutoff_24h:
                                hold_times.append(close_ms - current_open['open_time_ms'])
                                completed_rounds += 1
                                pnl = current_open['realized_pnl'] - current_open['total_fee']
                                total_pnl += pnl
                                total_volume += current_open['opened_qty']
                                if pnl > 0:
                                    win_count += 1
                                    total_win_usd += pnl
                                elif pnl < 0:
                                    loss_count += 1
                                    total_loss_usd += abs(pnl)
                            current_open = None

        closed_rounds = completed_rounds
        win_rate = (win_count / closed_rounds * 100) if closed_rounds > 0 else 0

        # 盈亏比 = 平均盈利 / 平均亏损
        avg_win = total_win_usd / win_count if win_count > 0 else Decimal('0')
        avg_loss = total_loss_usd / loss_count if loss_count > 0 else Decimal('0')
        if avg_loss > 0 and avg_win > 0:
            avg_pnl_ratio = float(avg_win / avg_loss)
        elif win_count > 0:
            avg_pnl_ratio = float('inf')
        elif loss_count > 0:
            avg_pnl_ratio = 0
        else:
            avg_pnl_ratio = 0

        # 盈亏期望 = 胜率 × 平均盈利 - 败率 × 平均亏损
        if closed_rounds > 0 and win_count > 0 and loss_count > 0:
            wr = win_count / closed_rounds
            expected_value = wr * float(avg_win) - (1 - wr) * float(avg_loss)
        elif closed_rounds > 0 and win_count == closed_rounds:
            expected_value = float(avg_win)  # 全胜
        elif closed_rounds > 0 and loss_count == closed_rounds:
            expected_value = -float(avg_loss)  # 全败
        else:
            expected_value = 0

        # 平均持仓时间 HH:MM:SS
        if hold_times:
            avg_hold_ms = sum(hold_times) / len(hold_times)
            total_seconds = int(avg_hold_ms / 1000)
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            secs = total_seconds % 60
            avg_hold_str = f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            avg_hold_str = '--'

        self.trade_stats_24h = {
            'round_count': completed_rounds,
            'win_count': win_count,
            'win_rate': win_rate,
            'total_volume': total_volume,
            'total_pnl': total_pnl,
            'avg_pnl_ratio': avg_pnl_ratio,
            'avg_hold_time': avg_hold_str,
            'expected_value': expected_value,
        }

    # ==================== v1.9.0: WS 健康 + REST 兜底 ====================

    def _update_ws_health(self):
        """同步 WS 健康状态（每帧调用）"""
        if self.user_stream_ws:
            self._ws_health['user_stream_connected'] = self.user_stream_ws.connected
            self._ws_health['user_stream_last_msg_ts'] = self.user_stream_ws.last_msg_ts
            self._ws_health['user_stream_msg_count'] = self.user_stream_ws.msg_count
            self._ws_health['user_stream_restart_count'] = self.user_stream_ws.restart_count

        # 超过 60s 未收到消息 → 激活 REST 兜底
        if self._ws_health['user_stream_connected']:
            age = time.time() - self._ws_health['user_stream_last_msg_ts']
            if age > 60 and not self._ws_health['fallback_active']:
                self._ws_health['fallback_active'] = True
                self._add_action("WebSocket 异常，已切换 REST 轮询模式", "")
        elif self._ws_health['fallback_active']:
            # WS 恢复 → 关闭兜底
            self._ws_health['fallback_active'] = False
            if self._rest_fallback_task:
                self._rest_fallback_task.cancel()
            self._add_action("WebSocket 已恢复", "")

    @property
    def ws_status_indicator(self) -> str:
        """TUI WS 状态指示灯"""
        if not self._ws_health['user_stream_connected']:
            return 'red'      # 🔴 WS 断开
        age = time.time() - self._ws_health['user_stream_last_msg_ts']
        if age > 120:
            return 'red'      # 🔴 超时
        elif age > 60:
            return 'yellow'   # 🟡 空闲
        return 'green'        # 🟢 正常

    def _on_batch_event(self, event_type: str, payload: dict, source: str = 'WS'):
        """v1.9.0：统一事件入口（幂等去重）

        event_type: 'BATCH_FILLED' | 'TP_FILLED' | 'SL_FILLED' | 'SM_FILLED' |
                    'ORDER_EXPIRED' | 'ORDER_CANCELED' | 'BALANCE_CHANGE'
        source: 'WS' | 'REST_POLL'
        """
        if not self.batch_state or not self.batch_state.get('enabled'):
            return

        order_id = payload.get('order_id') or payload.get('orderId')
        order_status = payload.get('status') or payload.get('X', '')

        # 幂等去重：同一 orderId 的终态只处理一次
        if order_id and event_type in ('BATCH_FILLED', 'TP_FILLED', 'SL_FILLED', 'SM_FILLED', 'ORDER_EXPIRED'):
            for batch in self.batch_state.get('batches', []):
                if batch.get('order_id') == order_id and batch.get('status') in ('tp_closed', 'closed', 'failed'):
                    return  # 已处理过
                if batch.get('tp_order_id') == order_id and batch.get('status') == 'tp_closed':
                    return  # 止盈单已处理过

        if event_type == 'BATCH_FILLED':
            self._handle_batch_fill(payload, source)
        elif event_type == 'TP_FILLED':
            self._handle_batch_tp_fill(payload, source)
        elif event_type == 'ORDER_EXPIRED':
            self._handle_batch_order_expired(payload, source)
        elif event_type in ('SL_FILLED', 'SM_FILLED'):
            self._handle_batch_sl_sm_fill(payload, source)

    async def _start_rest_fallback(self):
        """启动 REST 兜底轮询"""
        if self._rest_fallback_task and not self._rest_fallback_task.done():
            return
        self._rest_fallback_task = asyncio.create_task(self._rest_fallback_loop())

    async def _rest_fallback_loop(self):
        """REST 兜底轮询循环"""
        self._add_action("REST 兜底轮询已启动", "")
        last_trade_time = 0
        while self.running and self._ws_health['fallback_active']:
            try:
                # 并行拉取（在线程池执行，不阻塞事件循环）
                open_orders, account, trades = await asyncio.gather(
                    asyncio.to_thread(self.api.get_open_orders, self.symbol),
                    asyncio.to_thread(self.api.get_account),
                    asyncio.to_thread(self.api.get_user_trades, self.symbol, limit=10),
                )

                # 检测新成交
                new_fills = [t for t in trades if t.get('time', 0) > last_trade_time]
                if new_fills:
                    last_trade_time = max(t.get('time', 0) for t in new_fills)
                    for trade in new_fills:
                        self._on_batch_event('BATCH_FILLED', trade, source='REST_POLL')

                # 检测订单过期/取消
                all_orders = open_orders or []
                for batch in self.batch_state.get('batches', []):
                    oid = batch.get('order_id')
                    if oid and batch.get('status') == 'pending':
                        if not any(o.get('orderId') == oid for o in all_orders):
                            # 订单消失 → 检查是否是成交还是过期
                            await asyncio.to_thread(
                                self._check_disappeared_order, oid, batch
                            )

                # 同步账户余额
                if account:
                    for bal in account.get('assets', []):
                        if bal.get('asset') == 'USDC':
                            self.available_balance = Decimal(str(bal.get('availableBalance', '0')))
                            break

            except Exception:
                pass

            await asyncio.sleep(self._rest_fallback_interval)

    def _check_disappeared_order(self, order_id: int, batch: dict):
        """检查消失的订单是成交还是过期"""
        try:
            order = self.api.get_order(self.symbol, orderId=order_id)
            status = order.get('status', '')
            if status == 'FILLED':
                self._on_batch_event('BATCH_FILLED', order, source='REST_POLL')
            elif status in ('EXPIRED', 'CANCELED'):
                self._on_batch_event('ORDER_EXPIRED', order, source='REST_POLL')
        except Exception:
            pass

    # ==================== v1.9.0: 分批建仓引擎 ====================

    # --- 初始化与状态管理 ---

    def _init_batch_state(self, side: str):
        """初始化分批建仓状态"""
        config = self.config_manager.get_batch_config()
        count = max(2, min(50, int(config.get('count', 5))))
        distribution = config.get('distribution', 'equal')
        ladder_mode = config.get('ladder_mode', 'fixed')
        ladder_min = Decimal(str(config.get('ladder_min', 1.00)))
        ladder_max = Decimal(str(config.get('ladder_max', 10.00)))

        # 确保 ladder_max > ladder_min
        if ladder_max <= ladder_min:
            ladder_max = ladder_min + Decimal('1')

        self.batch_state = {
            'enabled': True,
            'side': side,
            'total_count': count,
            'distribution': distribution,
            'ladder_mode': ladder_mode,
            'ladder_min': ladder_min,
            'ladder_max': ladder_max,
            'target_notional': Decimal('0'),
            'batches': [],
            'sl_order_id': None,
            'sm_order_id': None,
            'weighted_avg_entry': Decimal('0'),
            'total_filled_size': Decimal('0'),
            'tp_backup': [],
            'early_close_order_id': None,
            'cancelled_batch_indices': [],
            'last_sl_update_ts': 0.0,
            'round_closed': False,
            'supplement_blocked': False,
            'max_position_size': Decimal('0'),
            'state': 'pending_only',
        }

    # --- 价格与数量计算 ---

    def _calculate_batch_prices(self, base_price: Decimal) -> list:
        """计算阶梯价格（ladder_min 到 ladder_max 等距插值）"""
        bs = self.batch_state
        count = bs['total_count']
        ladder_min = bs['ladder_min']
        ladder_max = bs['ladder_max']
        side = bs['side']
        tick = Decimal('0.01')

        if count == 1:
            step = Decimal('0')
        else:
            step = (ladder_max - ladder_min) / (count - 1)

        prices = []
        for i in range(count):
            offset = ladder_min + step * i  # 第 i 档偏离
            if side == 'LONG':
                price = base_price - offset
            else:
                price = base_price + offset
            prices.append(price.quantize(tick, rounding=ROUND_DOWN))

        # 最后一笔强制对齐到 ladder_max
        if side == 'LONG':
            last = (base_price - ladder_max).quantize(tick, rounding=ROUND_DOWN)
        else:
            last = (base_price + ladder_max).quantize(tick, rounding=ROUND_DOWN)
        prices[-1] = last

        return prices

    def _calculate_batch_sizes(self, total_size: Decimal) -> list:
        """计算数量分配"""
        bs = self.batch_state
        count = bs['total_count']
        distribution = bs['distribution']
        tick_qty = Decimal('0.001')

        if distribution == 'equal':
            base = (total_size / count).quantize(tick_qty, rounding=ROUND_DOWN)
            sizes = [base] * count
            # 尾差补到最后一批
            remainder = total_size - sum(sizes)
            sizes[-1] = (sizes[-1] + remainder).quantize(tick_qty, rounding=ROUND_DOWN)

        elif distribution == 'increase':
            # 递增：权重 1:2:3:...:N
            weights = list(range(1, count + 1))
            total_weight = sum(weights)
            sizes = []
            allocated = Decimal('0')
            for i, w in enumerate(weights):
                if i == count - 1:
                    s = (total_size - allocated).quantize(tick_qty, rounding=ROUND_DOWN)
                else:
                    s = (total_size * w / total_weight).quantize(tick_qty, rounding=ROUND_DOWN)
                sizes.append(s)
                allocated += s

        elif distribution == 'decrease':
            # 递减：权重 N:N-1:...:1
            weights = list(range(count, 0, -1))
            total_weight = sum(weights)
            sizes = []
            allocated = Decimal('0')
            for i, w in enumerate(weights):
                if i == count - 1:
                    s = (total_size - allocated).quantize(tick_qty, rounding=ROUND_DOWN)
                else:
                    s = (total_size * w / total_weight).quantize(tick_qty, rounding=ROUND_DOWN)
                sizes.append(s)
                allocated += s

        else:  # random
            import random as _random
            base = (total_size / count).quantize(tick_qty, rounding=ROUND_DOWN)
            sizes = []
            allocated = Decimal('0')
            for i in range(count - 1):
                factor = Decimal(str(round(_random.uniform(0.95, 1.05), 4)))
                s = (base * factor).quantize(tick_qty, rounding=ROUND_DOWN)
                sizes.append(s)
                allocated += s
            sizes.append((total_size - allocated).quantize(tick_qty, rounding=ROUND_DOWN))

        return sizes

    # --- 批量下单 ---

    async def _place_batch_orders(self):
        """通过 batchOrders 接口批量挂出分批订单（GTX 优先）"""
        bs = self.batch_state
        batches = bs['batches']
        total = len(batches)
        gtx_success = 0
        gtx_failed = 0

        # 每 5 笔一组提交
        for chunk_start in range(0, total, 5):
            chunk = batches[chunk_start:chunk_start + 5]
            batch_orders = []
            for b in chunk:
                order = {
                    'symbol': self.symbol,
                    'side': 'BUY' if bs['side'] == 'LONG' else 'SELL',
                    'type': 'LIMIT',
                    'timeInForce': 'GTX',
                    'quantity': str(b['size']),
                    'price': str(b['price']),
                    'positionSide': bs['side'],
                    'newClientOrderId': f"batch_{bs['side']}_{b['index']}_{int(time.time()*1000)}",
                }
                batch_orders.append(order)

            try:
                results = await asyncio.to_thread(
                    self.api.place_batch_orders,
                    self.symbol, batch_orders
                )
                for j, result in enumerate(results):
                    batch_idx = chunk_start + j
                    if 'code' in result and result['code'] == -5022:
                        # GTX 被拒
                        gtx_failed += 1
                    elif 'orderId' in result:
                        gtx_success += 1
                        batches[batch_idx]['order_id'] = result['orderId']
                        batches[batch_idx]['client_order_id'] = result.get('clientOrderId', '')
                        self._batch_order_map[result['orderId']] = batch_idx
            except Exception as e:
                # 整批调用失败
                gtx_failed += len(chunk)
                if self.log_manager:
                    self.log_manager.system.warning(f"[分批下单] batchOrders 调用失败：{e}")

        # 只保留成功挂出的批次
        bs['batches'] = [b for b in batches if b.get('order_id')]
        bs['total_count'] = len(bs['batches'])

        if gtx_success > 0:
            bs['max_position_size'] = sum(
                b['size'] for b in bs['batches']
            )
            self._add_action("分批开仓", f"{gtx_success} 笔 GTX 挂单成功")
        if gtx_failed > 0:
            self._add_action("分批开仓", f"{gtx_failed} 笔 GTX 失败，等待手动补单")

        # 如果没有成功挂出任何批次 → 清空状态
        if gtx_success == 0:
            self._add_action("分批开仓失败", "所有 GTX 订单被拒")
            await self._cleanup_batch_state(reason="开仓失败")
            return

    # --- 成交处理 ---

    def _handle_batch_fill(self, payload: dict, source: str = 'WS'):
        """处理批次成交"""
        if not self.batch_state or self.batch_state.get('round_closed'):
            return

        bs = self.batch_state
        order_id = payload.get('order_id') or payload.get('orderId') or payload.get('i')
        fill_price = Decimal(str(payload.get('price') or payload.get('L') or payload.get('avgPrice', '0')))
        fill_qty = Decimal(str(payload.get('size') or payload.get('l') or payload.get('qty', '0')))

        # 查找对应批次
        batch = None
        for b in bs['batches']:
            if b.get('order_id') == order_id:
                batch = b
                break

        if not batch:
            return

        if batch['status'] != 'pending':
            return  # 已处理

        # ① 标记已成交
        batch['status'] = 'filled'
        batch['fill_time'] = datetime.now()
        self._log_batch_action(f"批次 {batch['index']+1} 成交 @ {fill_price}")

        # ② 更新状态
        bs['state'] = 'partial_filled' if bs['state'] == 'pending_only' else bs['state']

        # ③ 异步挂独立止盈单
        asyncio.create_task(self._place_batch_tp(batch))

        # ④ 重算加权均价
        self._recalc_weighted_avg()

        # ⑤ 节流更新 SL/SM
        self._schedule_sl_sm_update()

        # ⑥ 检查是否全部成交
        pending = [b for b in bs['batches'] if b['status'] == 'pending']
        if not pending:
            bs['state'] = 'all_filled'
            self._add_action("全部成交", f"{bs['total_count']} 笔全部成交，进入持仓状态")

    async def _place_batch_tp(self, batch: dict):
        """为单个批次挂独立止盈单（GTX 优先，失败降级 GTC BBO）"""
        bs = self.batch_state
        entry_price = batch['price']
        batch_size = batch['size']
        side = bs['side']

        # 计算止盈价
        tp_price = self.config_manager.get_take_profit_price(entry_price, side)
        tp_side = 'SELL' if side == 'LONG' else 'BUY'

        # 尝试 GTX
        try:
            result = await asyncio.to_thread(
                self.api.place_order,
                symbol=self.symbol,
                side=tp_side,
                type='LIMIT',
                timeInForce='GTX',
                quantity=str(batch_size),
                price=str(tp_price),
                positionSide=side,
                reduceOnly=True,
            )
            if 'orderId' in result:
                batch['tp_order_id'] = result['orderId']
                batch['tp_price'] = tp_price
                batch['status'] = 'tp_placed'
                self._batch_tp_map[result['orderId']] = batch['index']
                return
        except Exception:
            pass

        # GTX 失败 → 降级 GTC BBO
        try:
            bbo = self.last_price or entry_price
            result = await asyncio.to_thread(
                self.api.place_order,
                symbol=self.symbol,
                side=tp_side,
                type='LIMIT',
                timeInForce='GTC',
                quantity=str(batch_size),
                price=str(tp_price),
                positionSide=side,
                reduceOnly=True,
            )
            if 'orderId' in result:
                batch['tp_order_id'] = result['orderId']
                batch['tp_price'] = tp_price
                batch['status'] = 'tp_placed'
                self._batch_tp_map[result['orderId']] = batch['index']
        except Exception as e:
            self._add_action("止盈挂单失败", f"批次 {batch['index']+1}：{e}")

    def _recalc_weighted_avg(self):
        """重算已成交批次加权均价（仅基于 filled + tp_placed 状态的批次）"""
        bs = self.batch_state
        active = [b for b in bs['batches'] if b['status'] in ('filled', 'tp_placed')]
        if not active:
            return

        total_value = sum(b['price'] * b['size'] for b in active)
        total_size = sum(b['size'] for b in active)
        if total_size > 0:
            bs['weighted_avg_entry'] = (total_value / total_size).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            bs['total_filled_size'] = total_size

    def _schedule_sl_sm_update(self):
        """节流更新 SL/SM（≥3 秒间隔）"""
        bs = self.batch_state
        now = time.time()
        if now - bs['last_sl_update_ts'] < 3.0:
            # 标记待更新，由回调处理
            bs['_pending_sl_update'] = True
            return

        bs['last_sl_update_ts'] = now
        bs['_pending_sl_update'] = False
        asyncio.create_task(self._update_batch_sl_sm())

    async def _update_batch_sl_sm(self):
        """更新统一 SL/SM 算法订单"""
        bs = self.batch_state
        if bs['round_closed'] or bs['total_filled_size'] <= 0:
            return

        avg_entry = bs['weighted_avg_entry']
        total_size = bs['total_filled_size']
        side = bs['side']

        # 撤销旧 SL
        if bs['sl_order_id']:
            try:
                await asyncio.to_thread(
                    self.api.cancel_algo_order, self.symbol, bs['sl_order_id']
                )
            except Exception:
                pass
            bs['sl_order_id'] = None

        # 撤销旧 SM
        if bs['sm_order_id']:
            try:
                await asyncio.to_thread(
                    self.api.cancel_algo_order, self.symbol, bs['sm_order_id']
                )
            except Exception:
                pass
            bs['sm_order_id'] = None

        # 挂新 SL（STOP 限价止损）
        sl_side = 'SELL' if side == 'LONG' else 'BUY'
        _, sl_params = self.config_manager.get_stop_loss_params(
            self.symbol, avg_entry, side, total_size
        )
        try:
            sl_result = await asyncio.to_thread(
                self.api.place_algo_order, **sl_params
            )
            if sl_result.get('algoId'):
                bs['sl_order_id'] = sl_result['algoId']
        except Exception as e:
            self._add_action("SL 更新失败", str(e))

        # 挂新 SM（STOP_MARKET 保底止损）
        sm_price = self.config_manager.get_stop_market_price(
            avg_entry, side, total_size,
            self.available_balance,
            Decimal('0')  # liquidation_price — 简化处理
        )
        sm_side = 'SELL' if side == 'LONG' else 'BUY'
        try:
            sm_result = await asyncio.to_thread(
                self.api.place_algo_order,
                symbol=self.symbol,
                side=sm_side,
                type='STOP_MARKET',
                stopPrice=str(sm_price),
                quantity=str(total_size),
                workingType='CONTRACT_PRICE',
                positionSide=side,
                reduceOnly=True,
            )
            if sm_result.get('algoId'):
                bs['sm_order_id'] = sm_result['algoId']
        except Exception as e:
            self._add_action("SM 更新失败", str(e))

        self._add_action("止损已更新", f"SL {avg_entry} / SM {sm_price}")

    # --- 止盈处理 ---

    def _handle_batch_tp_fill(self, payload: dict, source: str = 'WS'):
        """处理止盈成交"""
        bs = self.batch_state
        if not bs or bs.get('round_closed'):
            return

        tp_order_id = payload.get('order_id') or payload.get('orderId') or payload.get('i')
        batch = None
        for b in bs['batches']:
            if b.get('tp_order_id') == tp_order_id:
                batch = b
                break

        if not batch or batch['status'] != 'tp_placed':
            return

        batch['status'] = 'tp_closed'
        self._log_batch_action(f"批次 {batch['index']+1} 止盈成交 @ {batch.get('tp_price')}")

        # 检查是否所有止盈单都已成交
        active_tp = [b for b in bs['batches'] if b['status'] == 'tp_placed']
        if not active_tp:
            # 轮次结束
            self._add_action("轮次结束", "全部止盈成交")
            asyncio.create_task(self._cleanup_batch_state(reason="全部止盈"))
            return

        # 更新 SL/SM（减少数量）
        self._recalc_weighted_avg()
        bs['last_sl_update_ts'] = 0  # 强制立即更新
        self._schedule_sl_sm_update()

    # --- 止损/保底处理 ---

    def _handle_batch_sl_sm_fill(self, payload: dict, source: str = 'WS'):
        """处理止损/保底成交（依赖币安条件单执行）"""
        bs = self.batch_state
        if not bs or bs.get('round_closed'):
            return

        algo_id = payload.get('order_id') or payload.get('orderId') or payload.get('i')
        if algo_id not in (bs.get('sl_order_id'), bs.get('sm_order_id')):
            return

        is_sm = (algo_id == bs.get('sm_order_id'))
        label = "保底止损" if is_sm else "止损"
        self._add_action(f"{label}触发", "")

        # 撤销剩余未成交开仓挂单
        pending_batches = [b for b in bs['batches'] if b['status'] in ('pending',)]
        if pending_batches:
            asyncio.create_task(self._cancel_pending_batches(pending_batches))

        # 保底止损 SM 不撤（is_sm 时跳过）
        # 已挂止盈单由币安自动取消

        asyncio.create_task(self._cleanup_batch_state(reason=label))

    # --- 补单 ---

    async def _supplement_batch_orders(self):
        """补单：max_position_size - 未止盈仓位 - 挂单中仓位"""
        bs = self.batch_state
        if not bs or bs.get('round_closed') or bs.get('supplement_blocked'):
            return

        max_size = bs['max_position_size']
        unfilled = sum(
            b['size'] for b in bs['batches']
            if b['status'] in ('filled', 'tp_placed')
        )
        pending = sum(
            b['size'] for b in bs['batches']
            if b['status'] == 'pending'
        )
        supplement_size = max_size - unfilled - pending

        if supplement_size <= Decimal('0.001'):
            self._add_action("补单", "仓位已满，无需补单")
            return

        # 基于当前行情重新计算阶梯
        base_price = self.last_price
        if not base_price:
            self._add_action("补单失败", "无行情数据")
            return

        # 计算新批次数量和价格
        avg_size = max_size / bs['total_count'] if bs['total_count'] > 0 else supplement_size
        new_count = max(1, int(supplement_size / avg_size))
        new_count = min(new_count, 50 - len(bs['batches']))

        bs['ladder_min'] = bs.get('ladder_min', Decimal('1'))
        bs['ladder_max'] = bs.get('ladder_max', Decimal('10'))
        self._calculate_batch_prices(base_price)

        new_batches = []
        prices = self._calculate_batch_prices(base_price)[:new_count]
        sizes = self._calculate_batch_sizes(supplement_size)[:new_count]

        for i in range(new_count):
            new_batches.append({
                'index': len(bs['batches']),
                'order_id': None,
                'client_order_id': '',
                'price': prices[i] if i < len(prices) else base_price,
                'size': sizes[i] if i < len(sizes) else avg_size,
                'status': 'pending',
                'fill_time': None,
                'tp_order_id': None,
                'tp_price': None,
                'gtx_rejected': False,
            })

        bs['batches'].extend(new_batches)
        bs['total_count'] = len(bs['batches'])

        # 批量下单
        await self._place_batch_orders()
        self._add_action("补单", f"已挂出 {len(new_batches)} 笔新批次")

    # --- 撤销 ---

    async def _cancel_pending_batches(self, batches: list):
        """批量撤销未成交批次"""
        bs = self.batch_state
        for chunk_start in range(0, len(batches), 5):
            chunk = batches[chunk_start:chunk_start + 5]
            order_ids = [b['order_id'] for b in chunk if b.get('order_id')]
            if not order_ids:
                continue
            try:
                await asyncio.to_thread(
                    self.api.cancel_batch_orders,
                    self.symbol, order_ids
                )
                for b in chunk:
                    if b.get('order_id') in order_ids:
                        b['status'] = 'cancelled'
                        bs['cancelled_batch_indices'].append(b['index'])
            except Exception:
                # 逐笔撤销兜底
                for b in chunk:
                    if b.get('order_id'):
                        try:
                            await asyncio.to_thread(
                                self.api.cancel_order, self.symbol, order_id=b['order_id']
                            )
                            b['status'] = 'cancelled'
                            bs['cancelled_batch_indices'].append(b['index'])
                        except Exception:
                            pass

    async def _cancel_all_pending_batches(self):
        """撤销所有未成交批次"""
        if not self.batch_state:
            return
        pending = [b for b in self.batch_state['batches'] if b['status'] == 'pending']
        if pending:
            await self._cancel_pending_batches(pending)
            self._add_action("撤单", f"已撤销 {len(pending)} 笔未成交挂单")

    # --- 订单过期处理 ---

    def _handle_batch_order_expired(self, payload: dict, source: str = 'WS'):
        """处理订单过期（保证金不足等原因）"""
        bs = self.batch_state
        if not bs or bs.get('round_closed'):
            return

        order_id = payload.get('order_id') or payload.get('orderId') or payload.get('i')
        gtd = payload.get('gtd', 0)

        for b in bs['batches']:
            if b.get('order_id') == order_id and b['status'] == 'pending':
                b['status'] = 'failed'
                self._add_action("订单过期", f"批次 {b['index']+1} @ {b['price']}（原因码 {gtd}）")
                return

    # --- 早期平仓 ---

    async def _batch_early_close(self):
        """分批模式提前平仓（→ 键）"""
        bs = self.batch_state
        if not bs or bs.get('round_closed'):
            return

        total_size = bs['total_filled_size']
        if total_size <= 0:
            return

        side = bs['side']
        close_side = 'SELL' if side == 'LONG' else 'BUY'

        # 备份止盈单
        bs['tp_backup'] = [
            {'index': b['index'], 'tp_order_id': b['tp_order_id'], 'tp_price': b['tp_price']}
            for b in bs['batches'] if b['status'] == 'tp_placed'
        ]

        # 撤销止盈单
        for b in bs['batches']:
            if b['status'] == 'tp_placed' and b.get('tp_order_id'):
                try:
                    await asyncio.to_thread(
                        self.api.cancel_order, self.symbol, order_id=b['tp_order_id']
                    )
                except Exception:
                    pass
                b['status'] = 'filled'

        # 挂提前平仓单
        bbo_price = self.last_price
        try:
            result = await asyncio.to_thread(
                self.api.place_order,
                symbol=self.symbol,
                side=close_side,
                type='LIMIT',
                timeInForce='GTC',
                quantity=str(total_size),
                price=str(bbo_price),
                positionSide=side,
                reduceOnly=True,
            )
            if result.get('orderId'):
                bs['early_close_order_id'] = result['orderId']
                bs['state'] = 'early_close'
                self._add_action("提前平仓", f"挂单 @ {bbo_price} x {total_size} ETH")
        except Exception as e:
            self._add_action("提前平仓失败", str(e))

    def _cancel_batch_early_close(self) -> bool:
        """分批模式：撤销提前平仓，恢复止盈单"""
        bs = self.batch_state
        if not bs or bs.get('state') != 'early_close':
            return False
        if not bs.get('early_close_order_id'):
            return False

        try:
            self.api.cancel_order(self.symbol, order_id=bs['early_close_order_id'])
            bs['early_close_order_id'] = None
        except Exception:
            pass

        # 恢复止盈单
        for backup in bs.get('tp_backup', []):
            batch = next((b for b in bs['batches'] if b['index'] == backup['index']), None)
            if not batch or batch['status'] != 'filled':
                continue
            try:
                tp_side = 'SELL' if bs['side'] == 'LONG' else 'BUY'
                result = self.api.place_order(
                    symbol=self.symbol,
                    side=tp_side,
                    type='LIMIT',
                    timeInForce='GTC',
                    quantity=str(batch['size']),
                    price=str(backup['tp_price']),
                    positionSide=bs['side'],
                    reduceOnly=True,
                )
                if result.get('orderId'):
                    batch['tp_order_id'] = result['orderId']
                    batch['tp_price'] = backup['tp_price']
                    batch['status'] = 'tp_placed'
                    self._batch_tp_map[result['orderId']] = batch['index']
            except Exception:
                pass

        bs['tp_backup'] = []
        bs['state'] = 'partial_filled'
        self._add_action("恢复止盈单", f"已撤销提前平仓")
        return True

    async def _close_position_market_batch(self) -> bool:
        """分批模式：市价全平所有已成交仓位（Z 键）"""
        bs = self.batch_state
        if not bs or bs.get('round_closed'):
            return False

        total_size = bs['total_filled_size']
        if total_size <= 0:
            if self.log_manager:
                self.log_manager.system.debug("✗ 无已成交仓位")
            return False

        side = bs['side']
        close_side = 'SELL' if side == 'LONG' else 'BUY'
        self._add_action("Z 键平仓", f"市价全平 {side} {total_size} ETH")

        try:
            # 先清理所有挂单
            await self._cleanup_batch_state(reason="Z 键平仓")

            # 市价平仓
            result = await asyncio.to_thread(
                self.api.place_order,
                symbol=self.symbol,
                side=close_side,
                type='MARKET',
                quantity=str(total_size),
                positionSide=side,
            )
            close_price = Decimal(str(result.get('avgPrice', '0')))
            commission = Decimal(str(result.get('commission', '0')))

            avg_entry = bs['weighted_avg_entry']
            if side == 'LONG':
                pnl = (close_price - avg_entry) * total_size - commission
            else:
                pnl = (avg_entry - close_price) * total_size - commission

            self.sync_account()
            self._add_action("手动平仓成交", f"{close_side} {total_size} @ {close_price} | PnL: {pnl:+.6f} USDT")
            return True
        except Exception as e:
            self._add_action("市价全平失败", str(e))
            return False

    # --- 状态清理 ---

    async def _cleanup_batch_state(self, reason: str = ""):
        """清理分批状态，撤销所有相关订单"""
        bs = self.batch_state
        if not bs:
            return

        bs['round_closed'] = True

        # 撤销所有未成交批次
        pending = [b for b in bs['batches'] if b['status'] == 'pending']
        if pending:
            await self._cancel_pending_batches(pending)

        # 撤销止盈单
        for b in bs['batches']:
            if b['status'] == 'tp_placed' and b.get('tp_order_id'):
                try:
                    await asyncio.to_thread(
                        self.api.cancel_order, self.symbol, order_id=b['tp_order_id']
                    )
                except Exception:
                    pass

        # 撤销 SL
        if bs.get('sl_order_id'):
            try:
                await asyncio.to_thread(
                    self.api.cancel_algo_order, self.symbol, bs['sl_order_id']
                )
            except Exception:
                pass

        # 撤销 SM（除非是保底止损触发的清理）
        if bs.get('sm_order_id') and reason not in ('保底止损',):
            try:
                await asyncio.to_thread(
                    self.api.cancel_algo_order, self.symbol, bs['sm_order_id']
                )
            except Exception:
                pass

        # 撤销提前平仓单
        if bs.get('early_close_order_id'):
            try:
                await asyncio.to_thread(
                    self.api.cancel_order, self.symbol, order_id=bs['early_close_order_id']
                )
            except Exception:
                pass

        # 清理映射表
        self._batch_order_map.clear()
        self._batch_tp_map.clear()

        # 重置浮盈/浮亏追踪
        self._max_float_pnl = None
        self._max_float_pnl_price = None
        self._min_float_pnl = None
        self._min_float_pnl_price = None

        self.batch_state = None
        self._key_prices = []
        self._key_price_triggered.clear()

        if reason:
            self._add_action("轮次结束", reason)

    # --- 浮亏保护 ---

    def _check_batch_loss_protection(self):
        """分批模式浮亏保护检测（由 loss_protection_manager 调用入口替换）"""
        bs = self.batch_state
        if not bs or bs.get('round_closed') or bs.get('supplement_blocked'):
            return

        trigger_minutes = self.config_manager.get_loss_protection_config().get('trigger_minutes', 5)
        # 首笔成交时间
        first_fill = None
        for b in bs['batches']:
            if b.get('fill_time'):
                first_fill = b['fill_time']
                break

        if not first_fill:
            return

        elapsed = (datetime.now() - first_fill).total_seconds() / 60.0
        if elapsed < trigger_minutes:
            return

        # 触发
        bs['supplement_blocked'] = True
        avg_entry = bs['weighted_avg_entry']
        current_price = self.last_price
        side = bs['side']

        self._add_action("浮亏保护触发", f"当前价 {current_price} vs 均价 {avg_entry}")

        # 撤销未成交挂单
        pending = [b for b in bs['batches'] if b['status'] == 'pending']
        if pending:
            asyncio.create_task(self._cancel_pending_batches(pending))

        total_size = bs['total_filled_size']
        if side == 'LONG':
            price_above = current_price and current_price > avg_entry
        else:
            price_above = current_price and current_price < avg_entry

        if price_above:
            # 当前价在均价上方 → 挂 STOP 条件限价止损
            stop_side = 'SELL' if side == 'LONG' else 'BUY'
            asyncio.create_task(self._place_loss_protection_stop(avg_entry, stop_side, total_size))
        else:
            # 当前价在均价下方 → 挂均价限价平仓单
            close_side = 'SELL' if side == 'LONG' else 'BUY'
            asyncio.create_task(self._place_loss_protection_limit(avg_entry, close_side, total_size))

    async def _place_loss_protection_stop(self, price: Decimal, side: str, size: Decimal):
        """浮亏保护：挂 STOP 条件限价止损"""
        try:
            result = await asyncio.to_thread(
                self.api.place_algo_order,
                symbol=self.symbol,
                side=side,
                type='STOP',
                triggerPrice=str(price),
                quantity=str(size),
                price=str(price),
                workingType='CONTRACT_PRICE',
                positionSide=self.batch_state['side'],
                reduceOnly=True,
                timeInForce='GTC',
            )
            if result.get('algoId'):
                self._add_action("浮亏保护", f"STOP 止损已挂 @ {price}")
        except Exception as e:
            self._add_action("浮亏保护失败", str(e))

    async def _place_loss_protection_limit(self, price: Decimal, side: str, size: Decimal):
        """浮亏保护：挂均价限价平仓单"""
        try:
            result = await asyncio.to_thread(
                self.api.place_order,
                symbol=self.symbol,
                side=side,
                type='LIMIT',
                timeInForce='GTC',
                quantity=str(size),
                price=str(price),
                positionSide=self.batch_state['side'],
                reduceOnly=True,
            )
            if result.get('orderId'):
                self._add_action("浮亏保护", f"限价平仓单已挂 @ {price}")
        except Exception as e:
            self._add_action("浮亏保护失败", str(e))

    # --- 辅助 ---

    def _log_batch_action(self, msg: str):
        """记录分批操作日志"""
        if self.log_manager:
            self.log_manager.trading.info(msg)

    # ==================== v1.9.0 结束 ====================

    async def cleanup(self):
        """清理资源"""
        self.log_manager.system.debug("\n清理交易器资源...") if self.log_manager else None
        self.running = False

        # 清理分批状态
        if self.batch_state:
            await self._cleanup_batch_state(reason="程序退出")

        # 取消 REST 兜底任务
        if self._rest_fallback_task and not self._rest_fallback_task.done():
            self._rest_fallback_task.cancel()
            try:
                await self._rest_fallback_task
            except asyncio.CancelledError:
                pass

        # 1. 关闭用户数据流 WebSocket
        if self.user_stream_ws:
            try:
                self.user_stream_ws.running = False
                if hasattr(self.user_stream_ws, 'websocket') and self.user_stream_ws.websocket:
                    await self.user_stream_ws.websocket.close()
                self.log_manager.system.debug("✓ 用户数据流 WebSocket 已关闭") if self.log_manager else None
            except Exception as e:
                self.log_manager.system.debug(f"关闭用户数据流失败：{e}") if self.log_manager else None
        
        # 2. 等待用户数据流任务结束
        if hasattr(self, 'user_stream_task') and self.user_stream_task:
            try:
                await asyncio.wait_for(self.user_stream_task, timeout=1.0)
                self.log_manager.system.debug("✓ 用户数据流任务已结束") if self.log_manager else None
            except asyncio.TimeoutError:
                self.log_manager.system.debug("⚠ 用户数据流任务超时，强制结束") if self.log_manager else None
            except Exception as e:
                self.log_manager.system.debug(f"等待用户数据流任务失败：{e}") if self.log_manager else None
        
        # 3. 撤销所有挂单（避免遗留订单）
        try:
            self.log_manager.system.debug("撤销所有挂单...") if self.log_manager else None
            self.api.cancel_all_orders(self.symbol)
            self.log_manager.system.debug("✓ 所有挂单已撤销") if self.log_manager else None
        except Exception as e:
            self.log_manager.system.debug(f"撤销挂单失败：{e}") if self.log_manager else None
        
        # 4. 等待一小段时间，确保 API 请求完成
        await asyncio.sleep(0.5)
        
        self.log_manager.system.debug("✓ 实盘交易器已清理") if self.log_manager else None
