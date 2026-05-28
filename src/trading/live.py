# -*- coding: utf-8 -*-
"""
实盘交易模块 - 币安 Futures 实盘交易
v1.5.0 - 新增移动止损 + 浮亏保护
"""

import asyncio
import time
from pathlib import Path
from decimal import Decimal, ROUND_DOWN
from datetime import datetime
from typing import Optional, Dict, List

from src.api.binance_client import BinanceClient, BinanceAPIError
from src.api.user_stream_ws import UserStreamWebSocket
from src.logger import TradeLogger
from src.trading.trailing_stop import TrailingStopManager
from src.trading.loss_protection import LossProtectionManager


class LiveTrader:
    """实盘交易器"""
    
    def __init__(self, api_key: str, api_secret: str, symbol: str, 
                 leverage_limit: int = 100, actual_leverage: int = 25,
                 testnet: bool = False, logger: TradeLogger = None,
                 config_manager=None, log_manager=None):
        self.symbol = symbol
        self.leverage_limit = leverage_limit
        self.actual_leverage = actual_leverage
        self.testnet = testnet
        self.logger = logger
        self.config_manager = config_manager
        self.log_manager: LogManager = log_manager  # 由 main_live.py 传入
        
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
        self.position_history: list = []

        # 24 小时交易统计
        self.trade_stats_24h: dict = {}

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
        
        # 操作日志
        self.action_log: List[Dict] = []
        
        # v1.5.0 新增：移动止损和浮亏保护管理器
        self.trailing_stop_manager: Optional[TrailingStopManager] = None
        self.loss_protection_manager: Optional[LossProtectionManager] = None
        
        self.running = False
        self.connected = False

    def play_ding(self, count: int = 1):
        """播放提示音（count 控制响几声）"""
        if not self._ding_path:
            return
        # 检查音效开关
        if self.config_manager and not self.config_manager.is_sound_enabled():
            return
        try:
            import winsound
            for _ in range(count):
                winsound.PlaySound(self._ding_path, winsound.SND_FILENAME)
        except Exception:
            pass

    def _trigger_sound(self, action: str):
        """根据 action 名称自动触发对应音效"""
        if '开仓成交' in action or '开仓成交（部分）' in action:
            self.play_ding(1)  # 开仓响一声
        elif '持仓超时' in action:
            self.play_ding(4)  # 持仓超时响四声
        elif any(kw in action for kw in [
            '止盈成交', '止损成交', '保底止损成交',
            '提前平仓成交', '移动止损成交',
            '市价全平', '开仓成交（部分）',
            '持仓同步'
        ]):
            self.play_ding(3)  # 平仓响三声

    async def initialize(self) -> bool:
        """初始化"""
        self.log_manager.system.debug("\n初始化实盘交易...") if self.log_manager else None
        self.log_manager.system.debug("=" * 70) if self.log_manager else None
        
        try:
            server_time = self.api.get_server_time()
            self.log_manager.system.debug(f"✓ API 连接成功，服务器时间：{datetime.fromtimestamp(server_time/1000)}") if self.log_manager else None
            
            account = self.api.get_account()
            for asset in account.get('assets', []):
                if asset['asset'] == 'USDC':
                    self.available_balance = Decimal(asset['availableBalance'])
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
            self.log_manager.system.debug(f"✗ API 错误：[{e.code}] {e.msg}") if self.log_manager else None
            return False
        except Exception as e:
            self.log_manager.system.debug(f"✗ 初始化失败：{e}") if self.log_manager else None
            return False
    
    async def _start_user_stream(self):
        """启动用户数据流 WebSocket（可选功能，失败不影响主程序）"""
        try:
            self.listen_key = self.api.get_listen_key()
            self.log_manager.system.info(f"listenKey已获取：{self.listen_key[:16]}...") if self.log_manager else None
            self.user_stream_ws = UserStreamWebSocket(
                self.listen_key,
                api_client=self.api,
                testnet=self.testnet,
                log_func=lambda msg: self.log_manager.system.info(msg) if self.log_manager else None
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
                self.log_manager.system.info(f"[持仓同步] 持仓已平仓（程序未感知）") if self.log_manager else None
                # 先写 action 触发音效（持仓同步=外部平仓，响三声）
                self._add_action("持仓同步", "持仓已清除")
                # 主动刷新账户余额后再记录
                self.sync_account()
                # 记录平仓后账户余额（用于复合收益率计算）
                if self.logger:
                    self.logger.log_balance('position_closed', self.available_balance, {
                        'reason': 'sync_detected',
                        'last_position': self.position.get('side'),
                        'last_entry': float(self.position.get('entry_price', 0))
                    })
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
        self.log_manager.system.info(f"[订单更新] 状态={order_status}, 类型={order_type}, ID={order_id}, 方向={order_data.get('S', '?')}") if self.log_manager else None
        self.log_manager.system.debug(f"  成交数量：{order_data.get('z', 0)}, 成交均价：{order_data.get('ap', 0)}") if self.log_manager else None
        self.log_manager.system.debug(f"  手续费：{order_data.get('fc', 0)} {order_data.get('fs', 'USDC')}") if self.log_manager else None
        
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
            self.log_manager.system.info(f"[平仓检测-Fallback] FILLED 订单方向={order_side}, 持仓方向={self.position['side']}") if self.log_manager else None
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
    
    async def _sync_position(self):
        """查询并同步实际持仓状态"""
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
                    self.log_manager.system.info(f"[持仓同步-_sync] 交易所无持仓，清理本地状态（持仓方向={self.position.get('side')}）") if self.log_manager else None
                    self.sync_account()
                    self._add_action("持仓同步", "持仓已清空，撤销所有挂单")

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
                
                self.position = {
                    'side': side,
                    'entry_price': entry_price,
                    'size': actual_size,
                    'time': datetime.now()
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

            self.position = None

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
                elif '平仓' in action:
                    pnl = float(details.split('PnL:')[1].strip().split(' ')[0]) if 'PnL:' in details else 0
                    self.logger.log_trade('平仓成交', {'details': details, 'pnl': pnl})
                    self.logger.log_pnl('平仓成交', pnl)
                    self.logger.log_position('CLOSE', 0, 0, pnl)
            elif '挂单' in action or '已下' in action:
                self.logger.log_order(action, {'details': details})
    
    async def _safe_place_tp_sl_orders(self):
        """安全地下止盈止损单（包装异常处理）"""
        try:
            await self.place_tp_sl_orders()
        except Exception as e:
            self.log_manager.system.debug(f"\n[止盈止损异常] {e}") if self.log_manager else None
            import traceback
            traceback.print_exc()
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
                tp_price = self.config_manager.get_take_profit_price(entry_price, side)
                sl_trigger, sl_algo_params = self.config_manager.get_stop_loss_params(self.symbol, entry_price, side, size)
                
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
                    import traceback
                    traceback.print_exc()
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
                    import traceback
                    traceback.print_exc()
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
                import traceback
                traceback.print_exc()
            
            self.log_manager.system.debug("\n✓ 止盈止损单全部下达完成") if self.log_manager else None
            
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
    
    async def open_position(self, side: str) -> bool:
        """开仓"""
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
        """撤销开仓挂单"""
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
        """市价全平仓位（Z 键）"""
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
            
            self._add_action("市价全平", f"{close_side} {size} @ {close_price} | 手续费 {commission:.6f} {commission_asset} | PnL: {pnl_str}")

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
        """提前平仓（带重试）"""
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
        """撤销提前平仓，恢复原止盈单"""
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
            
            return True
        except Exception as e:
            self.log_manager.system.debug(f"✗ 撤销提前平仓失败：{e}") if self.log_manager else None
            return False
    
    def update_price(self, price: Decimal):
        """更新最新价格"""
        self.last_price = price
    
    def update_orderbook(self, bids: List, asks: List):
        """更新深度数据"""
        self.orderbook = {
            'bids': [[Decimal(p), Decimal(q)] for p, q in bids[:10]],
            'asks': [[Decimal(p), Decimal(q)] for p, q in asks[:10]]
        }

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
                    break
            self.position_margin = Decimal(account.get('totalPositionInitialMargin', 0))
            self.order_margin = Decimal(account.get('totalOpenOrderInitialMargin', 0))
        except:
            pass

    async def _refresh_history_delayed(self, delay: float = 5.0):
        """延迟刷新历史持仓（等 Binance 同步 trade 记录）"""
        await asyncio.sleep(delay)
        await self.fetch_position_history()

    async def fetch_position_history(self, days: int = 7):
        """从币安拉取成交记录，配对生成历史持仓"""
        try:
            import time
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - days * 86400000

            # 1. 分页拉取所有成交
            all_fills = []
            from_id = None
            while True:
                fills = self.api.get_fills(self.symbol, limit=1000, startTime=start_ms, endTime=now_ms, fromId=from_id)
                if not fills:
                    break
                all_fills.extend(fills)
                if len(fills) < 1000:
                    break
                from_id = fills[-1].get('id') + 1
                if len(all_fills) > 10000:  # 安全上限
                    break

            if not all_fills:
                return

            # 2. 拉取资费记录
            funding = self.api.get_income_history(self.symbol, incomeType='FUNDING_FEE', startTime=start_ms, endTime=now_ms, limit=1000)

            # 3. 按 positionSide 分组
            long_fills = [f for f in all_fills if f.get('positionSide') == 'LONG']
            short_fills = [f for f in all_fills if f.get('positionSide') == 'SHORT']

            # 4. 配对
            history = []
            history.extend(self._pair_positions('LONG', long_fills, funding))
            history.extend(self._pair_positions('SHORT', short_fills, funding))

            # 5. 排序：未平仓/部分平仓在最上面，按最后操作时间倒序
            STATUS_ORDER = {'未平仓': 0, '部分平仓': 1, '完全平仓': 2}
            history.sort(key=lambda x: (
                STATUS_ORDER.get(x.get('status', '完全平仓'), 2),
                -x.get('last_action_time_ms', 0),
            ))
            self.position_history = history[:10]  # 最多保留 10 条

            # 6. 更新 24h 交易统计
            self._update_trade_stats_24h()

        except Exception as e:
            self.log_manager.system.debug(f"[持仓历史] 拉取失败：{e}") if self.log_manager else None

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
            fee = Decimal(str(fill.get('commission', '0')))
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
                        'total_fee': fee,
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
                    current['total_fee'] += fee
            else:
                if current:
                    current['total_close_qty'] += qty
                    current['total_close_cost'] += price * qty
                    current['realized_pnl_sum'] += realized_pnl
                    current['total_fee'] += fee
                    current['close_time'] = trade_time
                    current['close_time_ms'] = trade_time_ms

                    if current['total_close_qty'] >= current['total_opened_qty']:
                        # 完全平仓：生成记录并结束当前持仓
                        open_avg = current['total_open_cost'] / current['total_opened_qty']
                        close_avg = current['total_close_cost'] / current['total_close_qty']
                        pos_funding = self._calc_funding(funding, current['open_time_ms'], current['close_time_ms'])
                        pnl = current['realized_pnl_sum'] - current['total_fee'] + pos_funding

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

    def _update_trade_stats_24h(self):
        """计算近 24 小时交易统计"""
        import time
        now_ms = int(time.time() * 1000)
        day_ms = 86400000

        # 拉取近 24 小时成交（全量，不限方向）
        try:
            all_fills = []
            from_id = None
            while True:
                fills = self.api.get_fills(self.symbol, limit=1000, startTime=now_ms - day_ms, endTime=now_ms, fromId=from_id)
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
                'avg_hold_time': '--'
            }
            return

        # 统计完整持仓轮次（每笔开仓 → 完全平仓算1轮）
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
                is_buyer = fill.get('buyer', False)
                is_open = (side == 'LONG' and is_buyer) or (side == 'SHORT' and not is_buyer)
                total_volume += qty
                trade_time_ms = fill.get('time', 0)

                if is_open:
                    if current_open is None:
                        current_open = {
                            'open_time_ms': trade_time_ms,
                            'total_qty': qty,
                            'total_cost': price * qty,
                            'realized_pnl': Decimal('0'),
                        }
                    else:
                        current_open['total_qty'] += qty
                        current_open['total_cost'] += price * qty
                else:
                    if current_open:
                        current_open['total_qty'] -= qty
                        current_open['realized_pnl'] += realized_pnl
                        hold_times.append(trade_time_ms - current_open['open_time_ms'])

                        if current_open['total_qty'] <= 0:
                            # 一轮持仓结束
                            completed_rounds += 1
                            pnl = current_open['realized_pnl']
                            total_pnl += pnl
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
        }

    async def cleanup(self):
        """清理资源"""
        self.log_manager.system.debug("\n清理交易器资源...") if self.log_manager else None
        self.running = False
        
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
