# -*- coding: utf-8 -*-
"""
实盘交易模块 - 币安 Futures 实盘交易
v1.1.1 - 完整止盈止损版本（修复数量计算）
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


class LiveTrader:
    """实盘交易器"""
    
    def __init__(self, api_key: str, api_secret: str, symbol: str, 
                 leverage_limit: int = 100, actual_leverage: int = 25,
                 testnet: bool = False, logger: TradeLogger = None,
                 config_manager=None):
        self.symbol = symbol
        self.leverage_limit = leverage_limit
        self.actual_leverage = actual_leverage
        self.testnet = testnet
        self.logger = logger
        self.config_manager = config_manager
        
        self.api = BinanceClient(api_key, api_secret, testnet)
        self.user_stream_ws: Optional[UserStreamWebSocket] = None
        self.listen_key: Optional[str] = None
        
        # 交易状态
        self.position: Optional[Dict] = None
        self.pending_order: Optional[Dict] = None
        self.tp_order: Optional[Dict] = None
        self.sl_order: Optional[Dict] = None
        self.stop_market_order: Optional[Dict] = None
        self.early_close_order: Optional[Dict] = None
        
        self.tp_order_backup: Optional[Dict] = None
        self.sl_order_backup: Optional[Dict] = None
        
        # 账户信息
        self.available_balance: Decimal = Decimal('0')
        self.position_margin: Decimal = Decimal('0')
        self.order_margin: Decimal = Decimal('0')
        
        # 行情数据
        self.last_price: Optional[Decimal] = None
        self.orderbook: Dict = {'bids': [], 'asks': []}
        
        # 操作日志
        self.action_log: List[Dict] = []
        
        self.running = False
        self.connected = False
    
    async def initialize(self) -> bool:
        """初始化"""
        print("\n初始化实盘交易...")
        print("=" * 70)
        
        try:
            server_time = self.api.get_server_time()
            print(f"✓ API 连接成功，服务器时间：{datetime.fromtimestamp(server_time/1000)}")
            
            account = self.api.get_account()
            for asset in account.get('assets', []):
                if asset['asset'] == 'USDC':
                    self.available_balance = Decimal(asset['availableBalance'])
                    break
            
            print(f"✓ 账户余额：{self.available_balance} USDC")
            self.api.set_leverage(self.symbol, self.leverage_limit)
            print(f"✓ 杠杆已设置：{self.leverage_limit}x")
            await self._start_user_stream()
            self.api.cancel_all_orders(self.symbol)
            print("✓ 已撤销所有遗留挂单")
            self._add_action("初始化", "实盘初始化成功")
            self.connected = True
            print("=" * 70)
            return True
        
        except BinanceAPIError as e:
            print(f"✗ API 错误：[{e.code}] {e.msg}")
            return False
        except Exception as e:
            print(f"✗ 初始化失败：{e}")
            return False
    
    async def _start_user_stream(self):
        """启动用户数据流 WebSocket（可选功能，失败不影响主程序）"""
        try:
            self.listen_key = self.api.get_listen_key()
            self.user_stream_ws = UserStreamWebSocket(self.listen_key)
            self.user_stream_ws.add_order_callback(self._on_order_update)
            self.user_stream_task = asyncio.create_task(self.user_stream_ws.connect())
            await asyncio.sleep(1)
            print("✓ 用户数据流已启动（用于订单状态更新）")
        except Exception as e:
            print(f"⚠ 用户数据流启动失败：{e}")
            print("  程序仍可正常运行，但订单状态更新可能有延迟")
            print("  建议：检查网络连接或防火墙设置")
    
    def _on_order_update(self, order_data: dict):
        """订单更新回调（同步函数）"""
        order_status = order_data.get('X')
        order_id = order_data.get('i')
        order_type = order_data.get('ot')
        
        # 记录详细订单信息
        print(f"\n[订单更新] 状态={order_status}, 类型={order_type}, ID={order_id}")
        print(f"  成交数量：{order_data.get('z', 0)}, 成交均价：{order_data.get('ap', 0)}")
        print(f"  手续费：{order_data.get('fc', 0)} {order_data.get('fs', 'USDC')}")
        
        # 开仓挂单成交
        if self.pending_order and self.pending_order.get('orderId') == order_id:
            if order_status == 'FILLED':
                side = self.pending_order.get('side', 'LONG')
                entry_price = Decimal(order_data.get('ap', '0'))
                filled_qty = Decimal(order_data.get('z', '0'))
                commission = Decimal(order_data.get('fc', '0'))
                commission_asset = order_data.get('fs', 'USDC')
                
                print(f"[开仓成交] 建立持仓，下止盈止损单...")
                print(f"  成交价：{entry_price}, 成交量：{filled_qty}")
                print(f"  手续费：{commission} {commission_asset}")
                self._add_action("开仓成交", f"{side} @ {entry_price} x {filled_qty} | 手续费 {commission} {commission_asset}")
                
                self.position = {
                    'side': side,
                    'entry_price': entry_price,
                    'size': filled_qty,
                    'time': datetime.now()
                }
                self.pending_order = None
                # 下止盈止损单（异步任务）
                asyncio.create_task(self._safe_place_tp_sl_orders())
            
            elif order_status == 'CANCELED':
                self._add_action("开仓撤销", "开仓挂单已撤销")
                self.pending_order = None
        
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
                    
                    print(f"[止盈单成交] 止盈单已成交！")
                    print(f"  成交价：{fill_price}, 成交量：{fill_qty}")
                    print(f"  手续费：{commission} {commission_asset}")
                    print(f"[平仓盈亏] PnL: {pnl:+.6f} USDT")
                    self._add_action("止盈成交", f"PnL: {pnl:+.6f} USDT")
                
                self._cancel_other_orders(exclude='tp')
                self.tp_order = None
            elif order_status in ['CANCELED', 'EXPIRED']:
                print(f"[止盈单取消] 止盈单已取消/过期")
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
                    
                    print(f"[止损单成交] 止损单已成交！")
                    print(f"  成交价：{fill_price}, 成交量：{fill_qty}")
                    print(f"  手续费：{commission} {commission_asset}")
                    print(f"[平仓盈亏] PnL: {pnl:+.6f} USDT")
                    self._add_action("止损成交", f"PnL: {pnl:+.6f} USDT")
                
                self._cancel_other_orders(exclude='sl')
                self.sl_order = None
            elif order_status in ['CANCELED', 'EXPIRED']:
                print(f"[止损单取消] 止损单已取消/过期")
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
                    
                    print(f"[保底止损成交] 保底止损已成交！")
                    print(f"  成交价：{fill_price}, 成交量：{fill_qty}")
                    print(f"  手续费：{commission} {commission_asset}")
                    print(f"[平仓盈亏] PnL: {pnl:+.6f} USDT")
                    self._add_action("保底止损成交", f"PnL: {pnl:+.6f} USDT")
                
                self._cancel_other_orders(exclude='stop_market')
                self.stop_market_order = None
            elif order_status in ['CANCELED', 'EXPIRED']:
                print(f"[保底止损取消] 保底止损已取消/过期")
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
                        
                        print(f"[提前平仓成交] 提前平仓已成交！")
                        print(f"  成交价：{fill_price}, 成交量：{fill_qty}")
                        print(f"  手续费：{commission} {commission_asset}")
                        print(f"[平仓盈亏] PnL: {pnl:+.6f} USDT")
                        self._add_action("提前平仓成交", f"PnL: {pnl:+.6f} USDT")
                    
                    self.early_close_order = None
                elif order_status in ['CANCELED', 'EXPIRED']:
                    print(f"[提前平仓取消] 提前平仓已取消/过期")
                    self.early_close_order = None
        
        # Fallback: 未知订单成交，但持仓被清空 → 可能是止损单触发的限价单成交
        elif order_status == 'FILLED' and self.position:
            # 检查是否是止损方向的订单
            order_side = order_data.get('S', '')
            if (self.position['side'] == 'LONG' and order_side == 'SELL') or \
               (self.position['side'] == 'SHORT' and order_side == 'BUY'):
                fill_price = Decimal(order_data.get('ap', '0'))
                fill_qty = Decimal(order_data.get('z', '0'))
                commission = Decimal(order_data.get('fc', '0'))
                
                if self.position['side'] == 'LONG':
                    pnl = (fill_price - self.position['entry_price']) * fill_qty - commission
                else:
                    pnl = (self.position['entry_price'] - fill_price) * fill_qty - commission
                
                print(f"[订单成交] 未知订单成交（可能是止损触发）！")
                print(f"  成交价：{fill_price}, 成交量：{fill_qty}")
                print(f"[平仓盈亏] PnL: {pnl:+.6f} USDT")
                self._add_action("订单成交", f"PnL: {pnl:+.6f} USDT")
        
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
                    # 订单成交时已经打印过 PnL，这里只清空状态
                    print(f"[持仓同步] 持仓已清空，撤销所有挂单...")
                    
                    # 批量撤销所有订单（普通订单 + 条件单）
                    try:
                        self.api.cancel_all_open_orders(self.symbol)
                        print(f"[批量撤销] 已撤销所有订单")
                    except Exception as e:
                        print(f"[批量撤销失败] {e}")
                    
                    # 清空本地状态
                    self.position = None
                    self.tp_order = None
                    self.sl_order = None
                    self.stop_market_order = None
            
            else:
                # 有持仓 → 更新状态
                side = 'LONG' if total_position_amt > 0 else 'SHORT'
                actual_size = abs(total_position_amt)
                
                # 检查是否有变化
                if self.position:
                    if self.position['side'] != side or self.position['size'] != actual_size:
                        print(f"[持仓同步] 更新持仓：{side} {actual_size} @ {entry_price}")
                        print(f"[浮动盈亏] PnL: {unrealized_pnl:+.2f} USDT")
                        self._add_action("持仓更新", f"{side} {actual_size} @ {entry_price} | PnL: {unrealized_pnl:+.2f}")
                
                self.position = {
                    'side': side,
                    'entry_price': entry_price,
                    'size': actual_size,
                    'time': datetime.now()
                }
        
        except Exception as e:
            print(f"[持仓同步失败] {e}")
    
    def _cancel_other_orders(self, exclude: str):
        """
        撤销其他订单（批量撤销）
        
        Args:
            exclude: 保留的订单类型 ('tp', 'sl', 'stop_market')
        """
        try:
            # 批量撤销所有订单（普通订单 + 条件单）
            print(f"[批量撤销] 撤销所有订单...")
            self.api.cancel_all_open_orders(self.symbol)
            
            # 清空本地状态
            if exclude != 'tp':
                self.tp_order = None
            if exclude != 'sl':
                self.sl_order = None
            if exclude != 'stop_market':
                self.stop_market_order = None
            
            self.position = None
            
            print("✓ 其他订单已撤销，持仓已清空")
        
        except Exception as e:
            print(f"✗ 撤销订单失败：{e}")
    
    def _add_action(self, action: str, details: str):
        """添加操作日志（同时写入文件）"""
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
            print(f"\n[止盈止损异常] {e}")
            import traceback
            traceback.print_exc()
            self._add_action("止盈止损异常", str(e))
    
    async def place_tp_sl_orders(self):
        """开仓成功后，放置止盈止损单（使用 config_manager 配置）"""
        if not self.position:
            print("✗ 止盈止损失败：无持仓")
            return
        
        entry_price = self.position['entry_price']
        side = self.position['side']
        size = self.position['size']
        
        print(f"\n[止盈止损] 开始下单...")
        print(f"  持仓：{side}, 开仓价={entry_price}, 数量={size}")
        self._add_action("止盈止损开始", f"{side} @ {entry_price} x {size}")
        
        try:
            # 1. 获取强平价（重试 3 次）
            liquidation_price = None
            for retry in range(3):
                print(f"[1/3] 获取强平价... (尝试 {retry + 1}/3)")
                positions = self.api.get_position(self.symbol)
                for pos in positions:
                    if pos['symbol'] == self.symbol and Decimal(pos['positionAmt']) != 0:
                        liquidation_price = Decimal(pos['liquidationPrice'])
                        break
                
                if liquidation_price and liquidation_price != Decimal('0'):
                    print(f"  ✓ 强平价有效：{liquidation_price}")
                    break
                else:
                    print(f"  ⚠ 强平价无效，等待 1 秒后重试...")
                    await asyncio.sleep(1)
            
            # 检查强平价是否有效
            if not liquidation_price or liquidation_price == Decimal('0'):
                print("✗ 保底止损：强平价无效，跳过")
            else:
                print(f"✓ 强平价有效：{liquidation_price}")
            
            # 2. 从 config_manager 获取止盈止损配置
            if self.config_manager:
                tp_price = self.config_manager.get_take_profit_price(entry_price)
                sl_trigger, sl_algo_params = self.config_manager.get_stop_loss_params(self.symbol, entry_price, side, size)
                sm_price = self.config_manager.get_stop_market_price(
                    entry_price, side, size, self.available_balance, liquidation_price or Decimal('0')
                )
                print(f"  配置止盈：{tp_price}, 止损触发：{sl_trigger}, 保底：{sm_price}")
            else:
                # 兼容模式：使用硬编码值
                tp_price = entry_price + Decimal('1')
                sl_trigger = entry_price - Decimal('3')
                sm_price = liquidation_price + Decimal('1') if liquidation_price else Decimal('0')
            
            # 3. 止盈单（LIMIT 限价单，保证 Maker）
            print(f"[2/3] 下止盈单...")
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
                print(f"✓ 止盈单已下：{tp_side} QUEUE @ {actual_price:.2f}（目标价 {tp_price} 会被吃）")
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
                print(f"✓ 止盈单已下：{tp_side} @ {tp_price}")
                actual_price = tp_price
            
            self.tp_order = {
                'orderId': tp_order['orderId'],
                'side': tp_side,
                'price': actual_price,  # 保存实际挂单价格
                'type': 'LIMIT'
            }
            
            # 3. 止损单（使用 config_manager 配置）
            print(f"[3/3] 下止损单...")
            if self.config_manager and sl_algo_params:
                # 从配置读取止损参数
                print(f"  止损参数：{sl_algo_params}")
                try:
                    sl_order = self.api.place_algo_order(**sl_algo_params)
                    actual_price = Decimal(sl_order.get('price', '0'))
                    sl_trigger_display = sl_algo_params.get('triggerPrice', sl_trigger)
                    print(f"  ✓ 止损单已下：algoId={sl_order.get('algoId')}")
                except Exception as e:
                    print(f"  ✗ 止损单失败：{e}")
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
            print(f"✓ 止损单已下：触发={sl_trigger_display}, 限价={actual_price:.2f}, algoId={sl_order['algoId']}")
            
            # 4. 保底止损（使用 config_manager 配置）
            print(f"[4/3] 下保底止损... (强平价={liquidation_price})")
            if liquidation_price and liquidation_price != Decimal('0'):
                liquidation_price = liquidation_price.quantize(Decimal('0.01'))
                
                if self.config_manager:
                    sm_price = self.config_manager.get_stop_market_price(
                        entry_price, side, size, self.available_balance, liquidation_price
                    )
                else:
                    # 兼容模式：硬编码
                    if side == 'LONG':
                        sm_price = liquidation_price + Decimal('1')
                    else:
                        sm_price = liquidation_price - Decimal('1')
                
                if side == 'LONG':
                    sm_side = 'SELL'
                else:
                    sm_side = 'BUY'
                
                # 保底止损参数
                sm_params = {
                    'symbol': self.symbol,
                    'side': sm_side,
                    'type': 'STOP_MARKET',
                    'triggerPrice': str(sm_price),
                    'quantity': str(size),
                    'workingType': 'MARK_PRICE',
                    'positionSide': side
                }
                
                print(f"  保底止损参数：{sm_params}")
                try:
                    stop_order = self.api.place_algo_order(**sm_params)
                    print(f"  ✓ 保底止损已下：algoId={stop_order.get('algoId')}")
                    
                    self.stop_market_order = {
                        'algoId': stop_order['algoId'],
                        'side': sm_side,
                        'trigger': sm_price,
                        'liquidation': liquidation_price,
                        'type': 'STOP_MARKET'
                    }
                    
                    self._add_action("保底止损已下", f"强平价={liquidation_price}, 触发={sm_price}")
                    print(f"✓ 保底止损已下：强平价={liquidation_price}, 触发={sm_price}, algoId={stop_order['algoId']}")
                except Exception as e:
                    print(f"  ✗ 保底止损失败：{e}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"  ✗ 跳过保底止损：强平价无效 ({liquidation_price})")
            else:
                print("✗ 保底止损：强平价无效")
            
            print("\n✓ 止盈止损单全部下达完成")
        
        except BinanceAPIError as e:
            print(f"\n✗ 止盈止损下单失败：[{e.code}] {e.msg}")
            print(f"  错误详情：{e}")
            self._add_action("止盈止损错误", f"[{e.code}] {e.msg}")
            raise  # 重新抛出异常
        except Exception as e:
            print(f"\n✗ 止盈止损错误：{e}")
            print(f"  错误详情：{type(e).__name__}: {e}")
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
            print(f"✓ 止盈单已下：价格={tp_price}, ID={tp_order['orderId']}")
            return True
        
        except BinanceAPIError as e:
            if e.code == -1128:
                print(f"[止盈重试] Post-Only 被拒，调整到盘口价重试...")
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
                print(f"✓ 止盈单已下：价格={tp_price}（盘口价）, ID={tp_order['orderId']}")
                return True
            
            except BinanceAPIError as e:
                if e.code == -1128:
                    retry_count += 1
                    print(f"[止盈重试] 第{retry_count}次重试...")
                    await asyncio.sleep(0.1)
                else:
                    raise
        
        print("✗ 止盈单重试失败")
        self._add_action("止盈失败", "Post-Only 重试超过最大次数")
        return False
    
    async def open_position(self, side: str) -> bool:
        """开仓"""
        if self.position is not None or self.pending_order is not None:
            print("✗ 已有持仓或挂单")
            return False
        
        if self.last_price is None:
            print("✗ 暂无价格数据")
            return False
        
        try:
            # 币安要求：最小名义价值 20 USDC
            MIN_NOTIONAL = Decimal('20')
            
            # 计算仓位（全仓进出）
            contract_value = self.available_balance * self.actual_leverage
            size = (contract_value / self.last_price).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
            
            if size <= 0:
                print("✗ 余额不足")
                return False
            
            # 验证名义价值（仓位价值 = 数量 × 价格）
            notional_value = size * self.last_price
            if notional_value < MIN_NOTIONAL:
                print(f"✗ 名义价值不足（最小 20 USDC，计算值{notional_value:.2f} USDC）")
                print(f"  当前：保证金{self.available_balance:.2f}U × {self.actual_leverage}x = {notional_value:.2f}U")
                print(f"  提示：提高杠杆倍数 或 增加保证金")
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
            
            print(f"✓ 挂单成功：{side} QUEUE @ {actual_price:.2f}, 订单 ID: {order['orderId']}")
            self._add_action("开仓挂单", f"{side} {size} QUEUE @ {actual_price:.2f}")
            return True
        
        except BinanceAPIError as e:
            if e.code == -1128:
                print("[Post-Only 被拒] 价格穿透，调整价格重新挂单")
                return await self.open_position(side)
            else:
                print(f"✗ 开仓失败：[{e.code}] {e.msg}")
                self._add_action("开仓错误", f"[{e.code}] {e.msg}")
                return False
        except Exception as e:
            print(f"✗ 开仓错误：{e}")
            self._add_action("开仓错误", str(e))
            return False
    
    def cancel_open_order(self) -> bool:
        """撤销开仓挂单"""
        if not self.pending_order:
            return False
        try:
            self.api.cancel_order(self.symbol, self.pending_order['orderId'])
            self.pending_order = None
            print("✓ 已撤销开仓挂单")
            self._add_action("撤销挂单", "撤销开仓挂单")
            return True
        except:
            return False
    
    async def close_position_early(self, retry_count: int = 0) -> bool:
        """提前平仓（带重试）"""
        if not self.position:
            print("✗ 无持仓")
            return False
        
        if self.early_close_order:
            print("✗ 已有提前平仓挂单")
            return False
        
        try:
            self.tp_order_backup = self.tp_order
            self.sl_order_backup = self.sl_order
            
            # 只撤销止盈单（因为要平仓了，止盈单没用了）
            # 止损单保留：如果提前平仓单没成交，止损单还能保护
            if self.tp_order:
                tp_id = self.tp_order.get('orderId') or self.tp_order.get('algoId')
                if tp_id:
                    self.api.cancel_order(self.symbol, tp_id)
                    self.tp_order = None
                    print(f"[提前平仓] 已撤销止盈单")
            
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
            print(f"✓ 提前平仓挂单：{order_side} QUEUE @ {actual_price:.2f}")
            self._add_action("提前平仓挂单", f"{order_side} QUEUE @ {actual_price:.2f}")
            return True
        
        except BinanceAPIError as e:
            if e.code == -1128 and retry_count < 3:
                print(f"[提前平仓重试] 订单被拒，第{retry_count+1}次重试...")
                await asyncio.sleep(0.1)
                return await self.close_position_early(retry_count + 1)
            else:
                print(f"✗ 提前平仓失败：[{e.code}] {e.msg}")
                self._add_action("提前平仓错误", f"[{e.code}] {e.msg}")
                return False
        except Exception as e:
            print(f"✗ 提前平仓错误：{e}")
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
                print("✓ 已撤销提前平仓挂单")
            
            # 恢复原止盈单
            if self.tp_order_backup:
                print(f"[恢复止盈] 重新挂止盈单...")
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
                    
                    print(f"✓ 已恢复止盈单：{tp_side} @ {tp_price}")
                    self._add_action("恢复止盈单", f"{tp_side} @ {tp_price}")
            
            self.tp_order_backup = None
            self.sl_order_backup = None
            
            return True
        except Exception as e:
            print(f"✗ 撤销提前平仓失败：{e}")
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
    
    def sync_account(self):
        """同步账户信息"""
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
    
    async def cleanup(self):
        """清理资源"""
        print("\n清理交易器资源...")
        self.running = False
        
        # 1. 关闭用户数据流 WebSocket
        if self.user_stream_ws:
            try:
                self.user_stream_ws.running = False
                if hasattr(self.user_stream_ws, 'websocket') and self.user_stream_ws.websocket:
                    await self.user_stream_ws.websocket.close()
                print("✓ 用户数据流 WebSocket 已关闭")
            except Exception as e:
                print(f"关闭用户数据流失败：{e}")
        
        # 2. 等待用户数据流任务结束
        if hasattr(self, 'user_stream_task') and self.user_stream_task:
            try:
                await asyncio.wait_for(self.user_stream_task, timeout=1.0)
                print("✓ 用户数据流任务已结束")
            except asyncio.TimeoutError:
                print("⚠ 用户数据流任务超时，强制结束")
            except Exception as e:
                print(f"等待用户数据流任务失败：{e}")
        
        # 3. 撤销所有挂单（避免遗留订单）
        try:
            print("撤销所有挂单...")
            self.api.cancel_all_orders(self.symbol)
            print("✓ 所有挂单已撤销")
        except Exception as e:
            print(f"撤销挂单失败：{e}")
        
        # 4. 等待一小段时间，确保 API 请求完成
        await asyncio.sleep(0.5)
        
        print("✓ 实盘交易器已清理")
