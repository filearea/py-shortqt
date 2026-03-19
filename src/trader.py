# -*- coding: utf-8 -*-
"""
交易核心逻辑 - 持仓管理、挂单、止盈止损
"""

import time
from datetime import datetime
from decimal import Decimal, ROUND_DOWN


class TradeState:
    """交易状态管理"""
    
    def __init__(self, logger, leverage: int, initial_balance: Decimal, 
                 take_profit: Decimal, stop_loss: Decimal):
        self.logger = logger
        self.leverage = leverage
        self.balance = initial_balance
        self.take_profit_points = take_profit
        self.stop_loss_points = stop_loss
        
        self.position = None
        self.pending_order = None
        self.open_time = None
        self.trades = []
        self.last_price = None
        self.last_price_change = None
        self.orderbook = {'bids': [], 'asks': []}
        self.action_log = []
        self.market_log_count = 0
        self.update_count = 0
        self.last_update_time = None
        self.updates_per_second = 0.0
        self._processing_order = False  # 防止重入标志
    
    def can_open_position(self, side: str, price: Decimal) -> tuple:
        """计算可开仓数量"""
        if self.position is not None or self.pending_order is not None:
            return None, 0
        contract_value = self.balance * self.leverage
        size = (contract_value / price).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
        if size <= 0:
            return None, 0
        return {'side': side, 'price': price, 'size': size, 'time': datetime.now()}, size
    
    def place_pending_order(self, order: dict):
        """下 Maker 挂单"""
        self.pending_order = order
        
        self.action_log.append({
            'time': datetime.now(), 'action': '挂单',
            'details': f"{'多' if order['side'] == 'LONG' else '空'} @ {order['price']:.2f} ({order['size']:.3f} ETH)"
        })
        if len(self.action_log) > 20:
            self.action_log = self.action_log[-20:]
        
        self.logger.log_action("ORDER_PLACED", {
            'side': 'LONG' if order['side'] == 'LONG' else 'SHORT',
            'price': order['price'],
            'size': order['size'],
            'balance': self.balance
        })
        
        # 记录开仓信号特征
        self.logger.record_signal(order['side'], order['price'], self.orderbook)
    
    def cancel_pending_order(self):
        """撤销挂单"""
        if self.pending_order:
            price = self.pending_order['price']
            self.action_log.append({
                'time': datetime.now(), 'action': '✗撤单',
                'details': f"@ {price:.2f}"
            })
            if len(self.action_log) > 20:
                self.action_log = self.action_log[-20:]
            self.logger.log_action("ORDER_CANCELLED", {'price': price})
            self.pending_order = None
            return True
        return False
    
    def check_pending_order_filled(self, latest_price: Decimal) -> bool:
        """检查挂单是否成交"""
        if self.pending_order is None or latest_price is None:
            return False
        
        order = self.pending_order
        filled = False
        
        if order['side'] == 'LONG':
            if latest_price <= order['price']:
                filled = True
        else:
            if latest_price >= order['price']:
                filled = True
        
        if filled:
            self.pending_order = None  # 立即清空
            
            if order.get('close_type') == 'EARLY':
                self._on_early_close_filled(order)
            else:
                self._on_order_filled(order)
            
            return True
        
        return False
    
    def _on_order_filled(self, order=None):
        """开仓挂单成交"""
        if order is None:
            order = self.pending_order
        
        if order is None:
            return
        
        entry_price = order['price']
        
        if self.position is not None:
            return
        
        if order['side'] == 'LONG':
            tp_price = entry_price + self.take_profit_points
            sl_price = entry_price - self.stop_loss_points
        else:
            tp_price = entry_price - self.take_profit_points
            sl_price = entry_price + self.stop_loss_points
        
        self.position = {
            'side': order['side'],
            'entry_price': entry_price,
            'size': order['size'],
            'tp_price': tp_price,
            'sl_price': sl_price,
            'time': datetime.now()
        }
        
        self.trades.append({
            'time': datetime.now(), 'type': 'OPEN', 'side': order['side'],
            'price': entry_price, 'size': order['size'], 'balance_before': self.balance
        })
        
        self.action_log.append({
            'time': datetime.now(), 'action': '✓成交',
            'details': f"挂单成交 @ {entry_price:.2f}"
        })
        if len(self.action_log) > 20:
            self.action_log = self.action_log[-20:]
        
        self.logger.log_action("ORDER_FILLED", {
            'price': entry_price,
            'size': order['size'],
            'balance': self.balance
        })
        
        self.pending_order = None
        self.open_time = time.time()
    
    def _on_early_close_filled(self, order=None):
        """提前平仓挂单成交"""
        if order is None:
            order = self.pending_order
        
        if order is None:
            return
        
        close_price = order['price']
        
        if self.position is None:
            return
        
        pos = self.position
        size = pos['size']
        entry_price = pos['entry_price']
        
        if pos['side'] == 'LONG':
            pnl = (close_price - entry_price) * size
        else:
            pnl = (entry_price - close_price) * size
        
        self.balance += pnl
        
        duration = time.time() - self.open_time if self.open_time else 0
        
        self.trades.append({
            'time': datetime.now(), 'type': 'EARLY',
            'side': 'SELL' if pos['side'] == 'LONG' else 'BUY',
            'price': close_price, 'size': size, 'pnl': pnl,
            'balance_after': self.balance, 'entry_price': entry_price
        })
        
        self.action_log.append({
            'time': datetime.now(), 'action': '✓提前平仓成交',
            'details': f"PnL: {pnl:+.2f} USDT"
        })
        if len(self.action_log) > 20:
            self.action_log = self.action_log[-20:]
        
        self.logger.log_action("EARLY_FILLED", {
            'price': close_price,
            'size': size,
            'pnl': pnl,
            'balance': self.balance + pnl
        })
        self.logger.log_action("BALANCE_CHANGE", {
            'change': pnl,
            'balance': self.balance + pnl
        })
        
        # 更新信号结果
        self.logger.update_signal_result("EARLY", float(pnl), duration)
        
        self.position = None
        self.pending_order = None
    
    def check_tp_sl(self, latest_price: Decimal) -> dict:
        """检查止盈止损"""
        if self.position is None or latest_price is None:
            return None
        
        pos = self.position
        if pos['side'] == 'LONG':
            if latest_price >= pos['tp_price']:
                return self.close_position('TP', latest_price)
            elif latest_price <= pos['sl_price']:
                return self.close_position('SL', latest_price)
        else:
            if latest_price <= pos['tp_price']:
                return self.close_position('TP', latest_price)
            elif latest_price >= pos['sl_price']:
                return self.close_position('SL', latest_price)
        return None
    
    def close_position(self, reason: str, close_price: Decimal) -> dict:
        """平仓（止盈/止损）"""
        if self.position is None:
            return None
        
        pos = self.position
        size = pos['size']
        entry_price = pos['entry_price']
        
        if pos['side'] == 'LONG':
            pnl = (close_price - entry_price) * size
        else:
            pnl = (entry_price - close_price) * size
        
        duration = time.time() - self.open_time if self.open_time else 0
        
        self.balance += pnl
        
        self.trades.append({
            'time': datetime.now(), 'type': reason,
            'side': 'SELL' if pos['side'] == 'LONG' else 'BUY',
            'price': close_price, 'size': size, 'pnl': pnl,
            'balance_after': self.balance, 'entry_price': entry_price
        })
        
        self.action_log.append({
            'time': datetime.now(), 'action': f"✓{reason}",
            'details': f"PnL: {pnl:+.2f} USDT"
        })
        if len(self.action_log) > 20:
            self.action_log = self.action_log[-20:]
        
        self.logger.log_action(f"{reason}_FILLED", {
            'price': close_price,
            'size': size,
            'pnl': pnl,
            'balance': self.balance + pnl  # 平仓后的余额
        })
        self.logger.log_action("BALANCE_CHANGE", {
            'change': pnl,
            'balance': self.balance + pnl
        })
        
        # 更新信号结果
        self.logger.update_signal_result(reason, float(pnl), duration)
        
        self.position = None
        return {'type': reason, 'side': pos['side'], 'entry_price': entry_price,
                'close_price': close_price, 'size': size, 'pnl': pnl}
    
    def close_position_early(self, side: str, price: Decimal):
        """提前平仓挂单（Maker 模式）"""
        if self.position is None or self.pending_order is not None:
            return
        
        pos = self.position
        
        # 多单持仓 → 挂卖单 @ 卖一价（Maker）
        # 空单持仓 → 挂买单 @ 买一价（Maker）
        if pos['side'] == 'LONG':
            order_side = 'SHORT'
        else:
            order_side = 'LONG'
        
        self.pending_order = {
            'side': order_side,
            'price': price,
            'size': pos['size'],
            'close_type': 'EARLY'
        }
        
        self.action_log.append({
            'time': datetime.now(), 'action': '提前平仓挂单',
            'details': f"{'多' if pos['side'] == 'LONG' else '空'} 挂@ {price:.2f} (Maker)"
        })
        
        self.logger.log_action("ORDER_PLACED", {
            'side': 'EARLY_CLOSE_' + pos['side'],
            'price': price,
            'size': pos['size']
        })
    
    def log_market_tick(self):
        """记录市场数据"""
        if self.last_price is None:
            return
        self.market_log_count += 1
        if self.market_log_count % 10 == 0:
            self.logger.log_market_data(self.last_price, self.orderbook)
