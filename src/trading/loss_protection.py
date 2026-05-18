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
        # v1.5.0 修复：支持小数分钟（例如 0.5 分钟 = 30 秒）
        self.trigger_minutes = max(0.5, min(60, float(config.get('trigger_minutes', 5))))  # 0.5-60 分钟
        self.symbol = trader.symbol  # v1.5.0 修复：保存 symbol
        
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

        # 持仓超时止损单（独立于移动止损，平仓时统一清理）
        self._breakeven_stop_id: Optional[int] = None  # 场景2：保本止损单
        self._grid1_stop_id: Optional[int] = None  # 场景3：网格1止损单
    
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
        # v1.5.0 修复：重新读取配置（支持 TUI 修改后生效）
        if self.trader.config_manager:
            config = self.trader.config_manager.get_config().get('loss_protection', {})
            self.trigger_minutes = max(0.5, min(60, float(config.get('trigger_minutes', 5))))
        
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
        # v1.5.0 修复：移除高频日志，只记录关键事件
        
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
        
        # 浮亏 → 场景1：保本止盈
        if unrealized_pnl < 0:
            await self._execute_loss_protection(current_price)
        else:
            # 浮盈 → 场景2：保本止损单 + 场景3：网格1止损单
            await self._execute_profit_protection(current_price)
    
    async def _execute_loss_protection(self, current_price: Decimal):
        """
        场景1：浮亏时执行保本止盈（将原限价止盈单改为开仓价）
        """
        if self.protected:
            return

        try:
            protection_price = self.entry_price

            # 撤销原止盈单
            if self.tp_order_id:
                try:
                    self.trader.api.cancel_order(self.trader.symbol, self.tp_order_id)
                except:
                    pass

            position = self.trader.position
            if not position or position.get('size', Decimal('0')) == 0:
                return

            position_size = position['size']

            order = self.trader.api.place_order(
                symbol=self.trader.symbol,
                side='SELL' if self.side == 'LONG' else 'BUY',
                type='LIMIT',
                price=str(protection_price),
                quantity=str(position_size),
                positionSide=self.side
            )

            self.tp_order_id = order['orderId']
            self.protected = True
            self.protection_time = datetime.now()

            if self.trader.log_manager:
                self.trader.log_manager.system.info(
                    f"[持仓超时] 浮亏保本 → 止盈单下移至开仓价 {protection_price}"
                )
            self.trader._add_action("持仓超时", f"浮亏保本：止盈单下移至开仓价 {protection_price}")

        except Exception:
            pass

    async def _execute_profit_protection(self, current_price: Decimal):
        """
        场景2：浮盈时创建保本止损单（触发价=开仓价）
        场景3：浮盈+移动止损启用+无活动订单+价格在网格1~2之间 → 额外创建网格1止损单
        """
        if self.protected:
            return

        try:
            position = self.trader.position
            if not position or position.get('size', Decimal('0')) == 0:
                self.protected = True
                self.protection_time = datetime.now()
                return

            position_size = position['size']
            side = 'SELL' if self.side == 'LONG' else 'BUY'

            # --- 场景2：保本止损单（始终创建） ---
            await self._create_breakeven_stop_order(side, position_size)

            # --- 场景3：网格1止损单（条件满足时额外创建） ---
            ts_manager = getattr(self.trader, 'trailing_stop_manager', None)
            if ts_manager and ts_manager.enabled and len(ts_manager.active_orders) == 0:
                # 判断价格是否在网格1和网格2之间
                in_range = False
                grid1_price = None
                if len(ts_manager.grid_prices) >= 2:
                    grid1_price = ts_manager.grid_prices[0]
                    grid2_price = ts_manager.grid_prices[1]
                    if self.side == 'LONG':
                        in_range = grid1_price <= current_price < grid2_price
                    else:
                        in_range = grid1_price >= current_price > grid2_price
                elif len(ts_manager.grid_prices) >= 1:
                    grid1_price = ts_manager.grid_prices[0]
                    if self.side == 'LONG':
                        in_range = current_price >= grid1_price
                    else:
                        in_range = current_price <= grid1_price

                if in_range and grid1_price:
                    await self._create_grid1_stop_order(side, position_size, grid1_price)

            self.protected = True
            self.protection_time = datetime.now()

        except Exception:
            self.protected = True
            self.protection_time = datetime.now()

    async def _create_breakeven_stop_order(self, side: str, position_size: Decimal):
        """场景2：创建保本止损单（触发价=开仓价，priceMatch='QUEUE'）"""
        try:
            trigger_price = self.entry_price
            from decimal import ROUND_DOWN
            price_str = str(trigger_price.quantize(Decimal('0.01'), rounding=ROUND_DOWN))
            qty_str = str(position_size.quantize(Decimal('0.001'), rounding=ROUND_DOWN))

            order = self.trader.api.place_algo_order(
                symbol=self.trader.symbol,
                side=side,
                type='STOP',
                triggerPrice=price_str,
                quantity=qty_str,
                priceMatch='QUEUE',
                positionSide=self.side,
                workingType='CONTRACT_PRICE'
            )

            order_id = order.get('orderId') or order.get('algoId')
            if order_id:
                self._breakeven_stop_id = order_id

            if self.trader.log_manager:
                self.trader.log_manager.system.info(
                    f"[持仓超时] 浮盈保本 → 创建止损单 @ {trigger_price} (防利润回吐)"
                )
            self.trader._add_action("持仓超时", f"浮盈保本：创建止损单 @ {trigger_price} (防利润回吐)")

        except Exception as e:
            if self.trader.log_manager:
                self.trader.log_manager.system.info(f"[持仓超时] 创建保本止损单失败：{e}")
            self.trader._add_action("持仓超时", f"保本止损单创建失败：{e}")

    async def _create_grid1_stop_order(self, side: str, position_size: Decimal, grid1_price: Decimal):
        """场景3：创建网格1止损单（触发价=网格1，比保本价更有利润）"""
        try:
            from decimal import ROUND_DOWN
            price_str = str(grid1_price.quantize(Decimal('0.01'), rounding=ROUND_DOWN))
            qty_str = str(position_size.quantize(Decimal('0.001'), rounding=ROUND_DOWN))

            order = self.trader.api.place_algo_order(
                symbol=self.trader.symbol,
                side=side,
                type='STOP',
                triggerPrice=price_str,
                quantity=qty_str,
                priceMatch='QUEUE',
                positionSide=self.side,
                workingType='CONTRACT_PRICE'
            )

            order_id = order.get('orderId') or order.get('algoId')
            if order_id:
                self._grid1_stop_id = order_id

            if self.trader.log_manager:
                self.trader.log_manager.system.info(
                    f"[持仓超时] 浮盈加强 → 创建网格1止损单 @ {grid1_price}"
                )
            self.trader._add_action("持仓超时", f"浮盈加强：创建网格1止损单 @ {grid1_price}")

        except Exception as e:
            if self.trader.log_manager:
                self.trader.log_manager.system.info(f"[持仓超时] 创建网格1止损单失败：{e}")
            self.trader._add_action("持仓超时", f"网格1止损单创建失败：{e}")
    
    def on_position_closed(self):
        """平仓回调 - 清理状态并撤销持仓超时止损单"""
        # 撤销持仓超时止损单（独立于移动止损）
        for stop_id in [self._breakeven_stop_id, self._grid1_stop_id]:
            if stop_id:
                try:
                    self.trader.api.cancel_algo_order(self.trader.symbol, stop_id)
                except:
                    pass

        self.entry_time = None
        self.entry_price = None
        self.side = None
        self.original_tp_price = None
        self.original_sl_price = None
        self.tp_order_id = None
        self.protected = False
        self.protection_time = None
        self.last_check_time = None
        self._breakeven_stop_id = None
        self._grid1_stop_id = None

        if self.trader.log_manager:
            self.trader.log_manager.system.info("[持仓超时] 已平仓，撤销保本止损单")
        self.trader._add_action("持仓超时", "已平仓，撤销保本止损单")
    
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
