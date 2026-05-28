# -*- coding: utf-8 -*-
"""
移动止损管理器 - v1.5.3

功能：
- 在开仓价与目标止盈价之间均分 N 格
- 第 1 格仅观察，从第 2 格开始触发止损单
- 自动管理币安 10 个条件单限制（最多 8 个移动止损单）
- 支持滚动策略（格数 > 8 时自动滚动）
"""

from decimal import Decimal, ROUND_DOWN
from typing import Optional, Dict, List, Set
from datetime import datetime
import time


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
        self.symbol = trader.symbol  # v1.5.0 修复：保存 symbol

        # 状态
        self.entry_price: Optional[Decimal] = None  # 开仓价
        self.take_profit_price: Optional[Decimal] = None  # 目标止盈价
        self.side: Optional[str] = None  # LONG/SHORT
        self.position_size: Optional[Decimal] = None  # 仓位数量

        # 网格价格
        self.grid_prices: List[Decimal] = []  # [第 1 格，第 2 格，...]

        # 活动订单 {grid_level: order_id}
        self.active_orders: Dict[int, int] = {}

        # 失败记录（防止同一 level 反复重试）
        self._failed_levels: Set[int] = set()

        # 统计
        self.total_triggers = 0  # 总触发次数
        self.last_trigger_level = 0  # 最后触发格数
        self.max_level_reached = 0  # 价格曾到达的最高格数（用于 TUI 显示）

        # 持仓验证缓存（避免每帧都查 API）
        self._last_verify_time: float = 0
        self._last_verify_result: Optional[bool] = None
        self._verify_cache_seconds: float = 3.0  # 3 秒缓存

    def refresh_config(self, config: dict):
        """运行时刷新配置（支持 TUI 修改后不重启生效）"""
        self.config = config
        self.enabled = config.get('enabled', False)
        self.grid_count = max(3, config.get('grid_count', 5))

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

        # 价格精度：ETHUSDC 为 2 位小数（tick_size=0.01）
        price_quant = Decimal('0.01')

        # 生成网格价格（截断精度）
        self.grid_prices = []
        for i in range(1, self.grid_count + 1):
            if side == 'LONG':
                grid_price = entry_price + (grid_step * i)
            else:  # SHORT
                grid_price = entry_price - (grid_step * i)
            # v1.5.3 修复：截断价格精度，避免 [-1111] Precision error
            grid_price = grid_price.quantize(price_quant, rounding=ROUND_DOWN)
            self.grid_prices.append(grid_price)

        # 网格价格计算完成（不打印详细日志）

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

        # 先检查交易所真实持仓，防止 WebSocket 丢消息导致状态不一致
        if not await self._verify_position_exists():
            return

        # 确定当前价格超过了第几格
        current_level = self._get_current_level(current_price)

        # 记录曾到达的最高格数（价格回落不减少）
        if current_level > self.max_level_reached:
            self.max_level_reached = current_level

        if current_level == 0:
            return

        if current_level == 1:
            return

        # 从第 2 格开始触发（日志在 _create_stop_order 中记录）
        await self._update_stop_order_for_level(current_level)

    async def _verify_position_exists(self) -> bool:
        """检查交易所是否还有真实持仓，如果没有就清空本地状态（带缓存）"""
        now = time.time()
        # 使用缓存结果
        if self._last_verify_result is not None and (now - self._last_verify_time) < self._verify_cache_seconds:
            return self._last_verify_result

        try:
            positions = self.trader.api.get_position(self.trader.symbol)
            has_position = False
            for pos in positions:
                amt = Decimal(pos.get('positionAmt', '0'))
                if amt != 0:
                    has_position = True
                    break

            if not has_position and self.trader.position is not None:
                # 交易所无持仓但本地有 -> 止损单可能已成交但 User Stream 没推
                pos = self.trader.position
                entry_price = pos.get('entry_price', Decimal('0'))
                side = pos.get('side', '')
                size = pos.get('size', Decimal('0'))

                # 查最近成交，计算 PnL
                close_price = None
                try:
                    fills = self.trader.api.get_fills(self.trader.symbol, limit=5)
                    for fill in reversed(fills):
                        if side == 'LONG' and fill.get('side') == 'SELL':
                            close_price = Decimal(fill['price'])
                            break
                        elif side == 'SHORT' and fill.get('side') == 'BUY':
                            close_price = Decimal(fill['price'])
                            break
                except Exception:
                    pass

                # 计算 PnL
                if close_price and entry_price and size:
                    if side == 'LONG':
                        pnl = (close_price - entry_price) * size
                    else:
                        pnl = (entry_price - close_price) * size
                    self.trader._add_action("持仓同步",
                        f"交易所已无持仓 | PnL: {pnl:+.2f} USDT (成交价 {close_price:.2f})")
                else:
                    self.trader._add_action("持仓同步", "交易所已无持仓，清理本地状态")

                if self.trader.log_manager:
                    if close_price:
                        self.trader.log_manager.system.info(
                            f"[持仓同步] 交易所已无持仓 | PnL: {pnl:+.2f} USDT "
                            f"(开仓 {entry_price:.2f} → 平仓 {close_price:.2f})"
                        )
                    else:
                        self.trader.log_manager.system.info(
                            "[持仓同步] 交易所已无持仓（可能已被止损/止盈成交），清理本地状态"
                        )

                # 撤销交易所上所有遗留订单（止盈/止损/保底止损）
                try:
                    self.trader.api.cancel_all_open_orders(self.trader.symbol)
                    self.trader._add_action("持仓同步", "已撤销交易所所有遗留订单")
                except Exception as e:
                    self.trader._add_action("持仓同步", f"撤销遗留订单失败：{e}")

                self.trader.position = None
                self.trader.tp_order = None
                self.trader.sl_order = None
                self.trader.stop_market_order = None
                self.trader.early_close_order = None
                self.trader.pending_order = None
                await self.on_position_closed()
                self._last_verify_result = False
                self._last_verify_time = now
                return False

            self._last_verify_result = has_position
            self._last_verify_time = now
            return has_position
        except Exception as e:
            # API 调用失败，保守处理：不创建新订单
            if self.trader.log_manager:
                self.trader.log_manager.system.debug(f"[移动止损] 持仓验证失败：{e}")
            return False

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

        # v1.5.3 修复：跳过之前失败的 level，防止反复重试刷日志
        if target_level in self._failed_levels:
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

        # 撤销订单（cancel_algo_order 是同步函数）
        try:
            self.trader.api.cancel_algo_order(self.trader.symbol, order_id)
            del self.active_orders[min_level]
        except Exception as e:
            # 撤销失败，记录日志但不中断
            if self.trader.log_manager:
                self.trader.log_manager.system.info(f"[移动止损] 滚动撤销失败：{e}")
            self.trader._add_action("移动止损", f"滚动撤销 level={min_level} 失败")

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

            # v1.5.3 修复：价格和数量精度截断
            price_str = str(trigger_price.quantize(Decimal('0.01'), rounding=ROUND_DOWN))
            qty_str = str(self.position_size.quantize(Decimal('0.001'), rounding=ROUND_DOWN))

            # 创建条件止损单（place_algo_order 是同步函数，不需要 await）
            order = self.trader.api.place_algo_order(
                symbol=self.trader.symbol,
                side=side,
                type='STOP',
                triggerPrice=price_str,
                quantity=qty_str,
                priceMatch='QUEUE',  # 同向价一，Maker 成交
                positionSide=self.side,
                workingType='CONTRACT_PRICE'
            )

            # 币安 Algo Order 返回的是 algoId，不是 orderId
            order_id = order.get('orderId') or order.get('algoId')
            if not order_id:
                if self.trader.log_manager:
                    self.trader.log_manager.system.error(f"[移动止损] 创建条件单失败，返回数据：{order}")
                return

            self.active_orders[level] = order_id
            self.total_triggers += 1
            self.last_trigger_level = level

            # 记录关键日志
            if self.trader.log_manager:
                self.trader.log_manager.system.info(
                    f"[移动止损] 第{level}格触发，止损单 @ {trigger_price}"
                )
            self.trader._add_action("移动止损", f"第{level}格触发，止损单 @ {trigger_price}")
            self.trader.play_ding(2)  # 网格触发响两声

        except Exception as e:
            # v1.5.3 修复：记录失败 level，防止反复重试
            self._failed_levels.add(level)
            if self.trader.log_manager:
                self.trader.log_manager.system.info(f"[移动止损] 创建止损单失败（level={level}，已标记跳过）：{e}")
            self.trader._add_action("移动止损", f"第{level}格创建失败，已标记跳过")

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
        self._failed_levels.clear()
        self.total_triggers = 0
        self.last_trigger_level = 0
        self.max_level_reached = 0
        self._last_verify_time = 0
        self._last_verify_result = None

    async def on_position_closed(self):
        """平仓回调 - 清理移动止损"""
        # 撤销所有活动止损单
        for level, order_id in list(self.active_orders.items()):
            try:
                self.trader.api.cancel_algo_order(self.trader.symbol, order_id)
            except:
                pass

        self.active_orders.clear()
        self._failed_levels.clear()
        self.entry_price = None
        self.take_profit_price = None
        self.side = None
        self.position_size = None
        self.grid_prices = []
        self.max_level_reached = 0

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
            'max_level_reached': self.max_level_reached,
        }
