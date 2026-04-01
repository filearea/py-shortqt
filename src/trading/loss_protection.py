# -*- coding: utf-8 -*-
"""
浮亏保护管理器 - v1.5.0

功能：
- 开仓后经过指定时间，检测持仓是否浮亏
- 如果浮亏，将止盈单下移到开仓价（保本出场）
- 如果浮盈，不做操作
"""

from decimal import Decimal
from typing import Optional, Dict
from datetime import datetime, timedelta


class LossProtectionManager:
    """浮亏保护管理器"""
    
    def __init__(self, trader, symbol_info, config):
        """
        初始化浮亏保护管理器
        
        Args:
            trader: LiveTrader 实例
            symbol_info: SymbolInfo 实例
            config: 浮亏保护配置 {enabled: bool, trigger_minutes: int}
        """
        self.trader = trader
        self.symbol_info = symbol_info
        self.config = config
        self.enabled = config.get('enabled', False)
        self.trigger_minutes = max(1, min(60, config.get('trigger_minutes', 5)))  # 1-60 分钟
        
        # 状态
        self.entry_time: Optional[datetime] = None  # 开仓时间
        self.entry_price: Optional[Decimal] = None  # 开仓价
        self.side: Optional[str] = None  # LONG/SHORT
        self.original_tp_price: Optional[Decimal] = None  # 原始止盈价
        self.original_sl_price: Optional[Decimal] = None  # 原始止损价
        self.tp_order_id: Optional[int] = None  # 止盈单 ID
        
        # 保护状态
        self.protected = False  # 是否已执行保护
        self.protection_time: Optional[datetime] = None  # 保护执行时间
        self.last_check_time: Optional[datetime] = None  # 最后检测时间
    
    def set_entry_info(self, entry_price: Decimal, side: str, 
                       tp_price: Optional[Decimal] = None, 
                       sl_price: Optional[Decimal] = None,
                       tp_order_id: Optional[int] = None):
        """
        设置开仓信息
        
        Args:
            entry_price: 开仓价
            side: LONG/SHORT
            tp_price: 止盈价
            sl_price: 止损价
            tp_order_id: 止盈单 ID
        """
        if not self.enabled:
            return
        
        self.entry_time = datetime.now()
        self.entry_price = entry_price
        self.side = side
        self.original_tp_price = tp_price
        self.original_sl_price = sl_price
        self.tp_order_id = tp_order_id
        
        # 重置保护状态
        self.protected = False
        self.protection_time = None
        self.last_check_time = None
    
    async def check_and_protect(self, current_price: Decimal, unrealized_pnl: Decimal):
        """
        检测并执行保护
        
        Args:
            current_price: 当前价格
            unrealized_pnl: 未实现盈亏（USDT）
        """
        if not self.enabled:
            return
        
        if not self.entry_time or not self.entry_price:
            return
        
        # 检查是否已执行保护
        if self.protected:
            return
        
        # 检查是否达到触发时间
        now = datetime.now()
        elapsed = (now - self.entry_time).total_seconds() / 60.0  # 分钟
        
        if elapsed < self.trigger_minutes:
            return
        
        # 记录最后检测时间
        self.last_check_time = now
        
        # 检查是否浮亏
        if unrealized_pnl >= 0:
            # 浮盈，不触发保护
            return
        
        # 浮亏，执行保护
        await self._execute_protection(current_price)
    
    async def _execute_protection(self, current_price: Decimal):
        """
        执行保护动作：将止盈单下移到开仓价
        
        Args:
            current_price: 当前价格
        """
        try:
            # 计算保本止盈价（开仓价）
            protection_price = self.entry_price
            
            # 撤销原止盈单
            if self.tp_order_id:
                try:
                    await self.trader.api.cancel_algo_order(self.symbol, self.tp_order_id)
                except Exception as e:
                    # 撤销失败，可能是已成交或已撤销
                    if self.trader.log_manager:
                        self.trader.log_manager.system.debug(f"[浮亏保护] 撤销原止盈单失败：{e}")
            
            # 创建新的止盈单 @ 开仓价
            if self.side == 'LONG':
                side = 'SELL'
            else:  # SHORT
                side = 'BUY'
            
            # 获取仓位数量
            position = self.trader.position
            if not position or position.get('size', Decimal('0')) == 0:
                return
            
            position_size = position['size']
            
            # 创建止盈单
            order = await self.trader.api.create_algo_order(
                symbol=self.symbol,
                side=side,
                type='TAKE_PROFIT',
                trigger_price=str(protection_price),
                quantity=str(position_size),
                price_match='OPPONENT',  # 对手价，确保快速成交
                position_side=self.side,
                working_type='CONTRACT_PRICE'
            )
            
            # 更新止盈单 ID
            self.tp_order_id = order['orderId']
            self.protected = True
            self.protection_time = datetime.now()
            
            # 记录日志
            if self.trader.log_manager:
                self.trader.log_manager.system.info(
                    f"[浮亏保护] 已触发！止盈单下移至开仓价 {protection_price} (order_id={self.tp_order_id})"
                )
        
        except Exception as e:
            # 执行失败，记录日志
            if self.trader.log_manager:
                self.trader.log_manager.system.debug(f"[浮亏保护] 执行保护失败：{e}")
    
    def on_position_closed(self):
        """平仓回调 - 清理状态"""
        self.entry_time = None
        self.entry_price = None
        self.side = None
        self.original_tp_price = None
        self.original_sl_price = None
        self.tp_order_id = None
        self.protected = False
        self.protection_time = None
        self.last_check_time = None
    
    def get_status(self) -> Dict:
        """获取状态信息"""
        if not self.enabled:
            return {'status': '未启用'}
        
        if not self.entry_time:
            return {'status': '等待开仓'}
        
        if self.protected:
            return {
                'status': '已保护',
                'protection_time': self.protection_time.strftime('%H:%M:%S'),
            }
        
        # 计算剩余时间
        now = datetime.now()
        elapsed = (now - self.entry_time).total_seconds()
        remaining_seconds = max(0, self.trigger_minutes * 60 - elapsed)
        
        # 格式化为 MM:SS
        minutes = int(remaining_seconds // 60)
        seconds = int(remaining_seconds % 60)
        time_str = f"{minutes:02d}:{seconds:02d}"
        
        # 检查当前盈亏状态
        position = self.trader.position
        if position and self.trader.last_price:
            # 计算未实现盈亏
            if position['side'] == 'LONG':
                pnl = (self.trader.last_price - position['entry_price']) * position['size']
            else:
                pnl = (position['entry_price'] - self.trader.last_price) * position['size']
            pnl_status = '浮亏' if pnl < 0 else '浮盈'
        else:
            pnl_status = '无持仓'
        
        return {
            'status': '检测中',
            'remaining_time': time_str,
            'pnl_status': pnl_status,
        }
