# -*- coding: utf-8 -*-
"""
移动止损管理器 - v1.5.0

功能：
- 在开仓价与目标止盈价之间均分 N 格
- 第 1 格仅观察，从第 2 格开始触发止损单
- 自动管理币安 10 个条件单限制（最多 8 个移动止损单）
- 支持滚动策略（格数 > 8 时自动滚动）
"""

from decimal import Decimal
from typing import Optional, Dict, List
from datetime import datetime


class TrailingStopManager:
    """移动止损管理器"""
    
    def __init__(self, trader, symbol_info, config):
        """
        初始化移动止损管理器
        
        Args:
            trader: LiveTrader 实例
            symbol_info: SymbolInfo 实例
            config: 移动止损配置 {enabled: bool, grid_count: int}
        """
        self.trader = trader
        self.symbol_info = symbol_info
        self.config = config
        self.enabled = config.get('enabled', False)
        self.grid_count = max(3, config.get('grid_count', 5))  # 至少 3 格
        
        # 状态
        self.entry_price: Optional[Decimal] = None  # 开仓价
        self.take_profit_price: Optional[Decimal] = None  # 目标止盈价
        self.side: Optional[str] = None  # LONG/SHORT
        self.position_size: Optional[Decimal] = None  # 仓位数量
        
        # 网格价格
        self.grid_prices: List[Decimal] = []  # [第 1 格，第 2 格，...]
        
        # 活动订单 {grid_level: order_id}
        self.active_orders: Dict[int, int] = {}
        
        # 统计
        self.total_triggers = 0  # 总触发次数
        self.last_trigger_level = 0  # 最后触发格数
    
    def calculate_grid_prices(self, entry_price: Decimal, take_profit_price: Decimal, side: str):
        """
        计算移动止损网格价格
        
        Args:
            entry_price: 开仓价
            take_profit_price: 目标止盈价
            side: LONG/SHORT
        """
        self.entry_price = entry_price
        self.take_profit_price = take_profit_price
        self.side = side
        
        # 计算总间距
        if side == 'LONG':
            total_range = take_profit_price - entry_price
        else:  # SHORT
            total_range = entry_price - take_profit_price
        
        # 均分 N 格
        grid_step = total_range / self.grid_count
        
        # 生成网格价格
        self.grid_prices = []
        for i in range(1, self.grid_count + 1):
            if side == 'LONG':
                grid_price = entry_price + (grid_step * i)
            else:  # SHORT
                grid_price = entry_price - (grid_step * i)
            self.grid_prices.append(grid_price)
        
        return self.grid_prices
    
    def get_trigger_price_for_level(self, level: int) -> Optional[Decimal]:
        """
        获取指定格数的触发价格（第 1 格不触发）
        
        Args:
            level: 格数（1-based）
        
        Returns:
            触发价格，如果 level=1 则返回 None（不触发）
        """
        if level < 1 or level > len(self.grid_prices):
            return None
        
        # 第 1 格不触发
        if level == 1:
            return None
        
        # 从第 2 格开始，触发价格 = 前一格的价格
        return self.grid_prices[level - 2]
    
    async def update_trailing_stop(self, current_price: Decimal):
        """
        根据当前价格更新移动止损单
        
        Args:
            current_price: 当前最新价
        """
        if not self.enabled:
            return
        
        if not self.entry_price or not self.grid_prices:
            return
        
        # 确定当前价格超过了第几格
        current_level = self._get_current_level(current_price)
        
        if current_level == 0:
            # 价格还未超过第 1 格，不操作
            return
        
        # 第 1 格仅观察，不触发
        if current_level == 1:
            return
        
        # 从第 2 格开始触发
        await self._update_stop_order_for_level(current_level)
    
    def _get_current_level(self, price: Decimal) -> int:
        """
        获取当前价格超过的格数
        
        Returns:
            超过的格数（0 表示未超过任何格）
        """
        if self.side == 'LONG':
            # 多单：价格从下往上
            for i, grid_price in enumerate(self.grid_prices, 1):
                if price < grid_price:
                    return i - 1
            return len(self.grid_prices)
        else:  # SHORT
            # 空单：价格从上往下
            for i, grid_price in enumerate(self.grid_prices, 1):
                if price > grid_price:
                    return i - 1
            return len(self.grid_prices)
    
    async def _update_stop_order_for_level(self, current_level: int):
        """
        为当前格数更新止损单
        
        Args:
            current_level: 当前价格超过的格数
        """
        # 获取应该触发的止损价格（前一格）
        trigger_price = self.get_trigger_price_for_level(current_level)
        if trigger_price is None:
            return
        
        # 检查该格是否已经有止损单
        target_level = current_level - 1  # 需要设置止损的格数
        
        if target_level in self.active_orders:
            # 已有止损单，不需要重复创建
            return
        
        # 需要创建新的止损单
        # 检查是否达到币安限制（最多 8 个移动止损单）
        if len(self.active_orders) >= 8:
            # 需要滚动：撤销最早的止损单（第 1 格开始）
            await self._roll_stop_orders()
        
        # 创建止损单
        await self._create_stop_order(target_level, trigger_price)
    
    async def _roll_stop_orders(self):
        """滚动止损单（撤销最早的，腾出空间）"""
        if not self.active_orders:
            return
        
        # 找到最早的格数（最小的 level）
        min_level = min(self.active_orders.keys())
        order_id = self.active_orders[min_level]
        
        # 撤销订单
        try:
            await self.trader.api.cancel_algo_order(self.symbol, order_id)
            del self.active_orders[min_level]
        except Exception as e:
            # 撤销失败，记录日志但不中断
            if self.trader.log_manager:
                self.trader.log_manager.system.debug(f"[移动止损] 滚动撤销失败：{e}")
    
    async def _create_stop_order(self, level: int, trigger_price: Decimal):
        """
        创建条件止损单
        
        Args:
            level: 格数
            trigger_price: 触发价格
        """
        try:
            # 确定订单方向
            if self.side == 'LONG':
                side = 'SELL'
            else:  # SHORT
                side = 'BUY'
            
            # 创建条件止损单
            order = await self.trader.api.create_algo_order(
                symbol=self.symbol,
                side=side,
                type='STOP',
                trigger_price=str(trigger_price),
                quantity=str(self.position_size),
                price_match='QUEUE',  # 同向价一，Maker 成交
                position_side=self.side,
                working_type='CONTRACT_PRICE'
            )
            
            order_id = order['orderId']
            self.active_orders[level] = order_id
            self.total_triggers += 1
            self.last_trigger_level = level
            
            # 记录日志
            if self.trader.log_manager:
                self.trader.log_manager.system.info(
                    f"[移动止损] 第{level}格触发，创建止损单 @ {trigger_price} (order_id={order_id})"
                )
        
        except Exception as e:
            # 创建失败，记录日志
            if self.trader.log_manager:
                self.trader.log_manager.system.debug(f"[移动止损] 创建止损单失败：{e}")
    
    async def on_position_opened(self, entry_price: Decimal, take_profit_price: Decimal, 
                                  side: str, position_size: Decimal):
        """
        开仓回调 - 初始化移动止损
        
        Args:
            entry_price: 开仓价
            take_profit_price: 目标止盈价
            side: LONG/SHORT
            position_size: 仓位数量
        """
        if not self.enabled:
            return
        
        self.entry_price = entry_price
        self.take_profit_price = take_profit_price
        self.side = side
        self.position_size = position_size
        
        # 计算网格价格
        self.calculate_grid_prices(entry_price, take_profit_price, side)
        
        # 重置状态
        self.active_orders.clear()
        self.total_triggers = 0
        self.last_trigger_level = 0
        
        if self.trader.log_manager:
            self.trader.log_manager.system.info(
                f"[移动止损] 已初始化：{self.grid_count}格，间距={(self.take_profit_price - self.entry_price) / self.grid_count:.2f}"
            )
    
    async def on_position_closed(self):
        """平仓回调 - 清理移动止损"""
        # 撤销所有活动止损单
        for level, order_id in list(self.active_orders.items()):
            try:
                await self.trader.api.cancel_algo_order(self.symbol, order_id)
            except:
                pass
        
        self.active_orders.clear()
        self.entry_price = None
        self.take_profit_price = None
        self.side = None
        self.position_size = None
        self.grid_prices = []
        
        if self.trader.log_manager:
            self.trader.log_manager.system.info("[移动止损] 已平仓，清理所有止损单")
    
    def get_status(self) -> Dict:
        """获取状态信息"""
        if not self.enabled or not self.entry_price:
            return {'status': '未启用'}
        
        if not self.grid_prices:
            return {'status': '等待触发'}
        
        return {
            'status': '已激活',
            'grid_count': self.grid_count,
            'active_levels': len(self.active_orders),
            'total_triggers': self.total_triggers,
            'last_trigger_level': self.last_trigger_level,
        }
