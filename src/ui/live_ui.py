# -*- coding: utf-8 -*-
"""
实盘交易 UI - v1.4.1 优化布局
"""

import os
import re
from datetime import datetime
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from decimal import Decimal
from src import __version__


def _pnl_style_from_details(details: str) -> str:
    """从 details 中提取 PnL 并返回颜色：盈利绿色，亏损红色，0 按盈利"""
    match = re.search(r'PnL:\s*([+-]?[\d.]+)', details)
    if match:
        pnl = float(match.group(1))
        return 'green' if pnl >= 0 else 'red'
    return None


def _fmt_num(value) -> str:
    """格式化数字：最大 6 位小数，省略末尾的 0，0 直接显示 '0'"""
    if value is None:
        return '--'
    f = float(value)
    if f == 0:
        return '0'
    return f'{f:.6f}'.rstrip('0').rstrip('.')


def _fmt_time(dt, now=None) -> str:
    """格式化时间：同天显示 HH:MM，昨天显示 昨天 HH:MM，其余 MM-DD HH:MM"""
    from datetime import datetime as _dt, timedelta
    if dt is None:
        return ''
    if now is None:
        now = _dt.now()
    if dt.date() == now.date():
        return dt.strftime('%H:%M')
    yesterday = now.date() - timedelta(days=1)
    if dt.date() == yesterday:
        return f'昨天 {dt.strftime("%H:%M")}'
    return dt.strftime('%m-%d %H:%M')


class DepthPressureTracker:
    """买卖盘占优滚动采样器 — 100ms 采样，5 分钟窗口，百分比展示"""

    def __init__(self, window_minutes: int = 5, sample_interval_ms: int = 100):
        from collections import deque
        import time as _time
        self._deque = deque
        self._time = _time
        self._sample_interval = sample_interval_ms / 1000.0
        self._window = window_minutes * 60.0
        self._buy_ts = deque()
        self._sell_ts = deque()
        self._last_sample = 0.0

    def sample(self, imbalance: float):
        """采样：imbalance > 0.15 买盘+1，< -0.15 卖盘+1，势均力敌不+分"""
        now = self._time.time()
        if now - self._last_sample < self._sample_interval:
            return
        self._last_sample = now
        if imbalance > 0.15:
            self._buy_ts.append(now)
        elif imbalance < -0.15:
            self._sell_ts.append(now)
        self._prune(now)

    def _prune(self, now: float):
        cutoff = now - self._window
        while self._buy_ts and self._buy_ts[0] < cutoff:
            self._buy_ts.popleft()
        while self._sell_ts and self._sell_ts[0] < cutoff:
            self._sell_ts.popleft()

    def get_ratio(self) -> tuple:
        """返回 (buy_percent, sell_percent)，无数据返回 (0, 0)"""
        now = self._time.time()
        self._prune(now)
        b, s = len(self._buy_ts), len(self._sell_ts)
        total = b + s
        if total == 0:
            return 0.0, 0.0
        return b / total * 100, s / total * 100

    @property
    def total_samples(self) -> int:
        self._prune(self._time.time())
        return len(self._buy_ts) + len(self._sell_ts)


class LiveTradingUI:
    """实盘交易界面 - v1.4.0"""
    
    def __init__(self, trader, leverage: int, take_profit: Decimal = Decimal('1'), 
                 stop_loss: Decimal = Decimal('3'), actual_leverage: int = 25, 
                 config_manager=None, indicators=None):
        self.trader = trader
        self.leverage = leverage  # API 杠杆
        self.actual_leverage = actual_leverage  # 实际杠杆
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.config_manager = config_manager  # 配置管理器，用于读取止盈止损配置
        self.indicators = indicators  # v1.4.0 新增：指标管理器
        self.depth_pressure = DepthPressureTracker(window_minutes=5, sample_interval_ms=100)  # v1.7.13: 买卖盘压力滚动采样
    
    def render(self, console_height: int = None) -> Layout:
        """渲染界面 - v1.6.6 布局"""
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="top"),      # orderbook + account + history
            Layout(name="footer", size=12),
            Layout(name="indicators", size=5),
            Layout(name="stats", size=3)  # 24h 数据统计
        )

        header = self._render_header()
        layout["header"].update(header)

        # top 区域：水平分割
        top_layout = Layout()
        top_layout.split_row(
            Layout(name="orderbook", size=27),   # 固定宽度
            Layout(name="history"),              # 自适应中间
            Layout(name="account", size=40),     # 固定宽度
        )

        # 计算订单簿显示档数（基于传入的终端实际高度）
        try:
            total_height = console_height or os.get_terminal_size().lines
            # top_height = 终端总高度 - header(3) - footer(12) - indicators(5)
            # orderbook 内部 Panel 有 2 行边框 + 最新价 1 行
            top_height = total_height - 3 - 12 - 5
            levels = max(5, min((top_height - 3) // 2, 20))
        except:
            levels = 15

        # 订单簿：买卖盘数量相等，最新价居中
        asks = list(self.trader.orderbook.get('asks', [])[:levels])
        bids = list(self.trader.orderbook.get('bids', [])[:levels])

        # 订单簿：header（价格范围3行）+ 买卖盘
        ob_renderable = self._render_orderbook_with_header(asks, bids, levels)
        top_layout["orderbook"].update(Panel(ob_renderable, title="订单簿"))
        top_layout["history"].update(Panel(self._render_position_history(), title="历史持仓"))
        top_layout["account"].update(Panel(self._render_account(), title="账户"))
        layout["top"].update(top_layout)

        layout["footer"].update(Panel(self._render_log(), title="日志"))
        layout["indicators"].update(Panel(self._render_indicators(), title="盘面指标"))
        # 动态标题：根据配置显示统计周期
        if self.trader and hasattr(self.trader, 'config_manager') and self.trader.config_manager:
            stats_period = self.trader.config_manager.get('stats_period', {})
            mode = stats_period.get('mode', '24h')
            if mode == 'calendar_day':
                tz = stats_period.get('timezone', '+8')
                stats_title = f"数据统计（自然日 UTC{tz}）"
            else:
                stats_title = "数据统计（24h）"
        else:
            stats_title = "数据统计（24h）"
        layout["stats"].update(Panel(self._render_stats(), title=stats_title))

        return layout
    
    def _render_header(self) -> Panel:
        """渲染头部"""
        price = self.trader.last_price
        price_text = f"{price:.2f}" if price else "等待..."
        
        # 价格变化箭头
        price_arrow = ""
        if hasattr(self.trader, 'last_price_change') and self.trader.last_price_change:
            price_arrow = " [green]↑[/green]" if self.trader.last_price_change > 0 else " [red]↓[/red]"
        
        # WebSocket 连接状态
        ws_status = self._render_ws_status()
        
        # 状态
        status = "就绪 - ↑做多 ↓做空 ←撤单 →平仓 S 设置 H 同步 Q 退出"

        # v1.9.0：分批模式状态
        if self.trader.batch_state and self.trader.batch_state.get('enabled') and not self.trader.batch_state.get('round_closed'):
            bs = self.trader.batch_state
            filled = sum(1 for b in bs.get('batches', []) if b['status'] in ('filled', 'tp_placed', 'tp_closed'))
            total = bs.get('total_count', 0)
            batch_states = {
                'pending_only': f'[yellow]分批挂单中[/yellow] — {filled}/{total} 批',
                'partial_filled': f'[green]分批持仓[/green] — 已成交 {filled}/{total} 批',
                'all_filled': f'[green]全部成交[/green] — {total}/{total} 批',
                'early_close': f'[yellow]提前平仓中[/yellow]',
            }
            status = batch_states.get(bs.get('state', ''), f'分批模式 — {filled}/{total}')

        elif self.trader.early_close_order:
            status = f"[yellow]平仓挂单中[/yellow] @ {self.trader.early_close_order['price']:.2f} (←撤单)"
        elif self.trader.pending_order:
            side_text = '多' if self.trader.pending_order['side'] == 'LONG' else '空'
            status = f"[yellow]开仓挂单中[/yellow] - {side_text} @ {self.trader.pending_order['price']:.2f} (←撤单)"
        elif self.trader.position:
            status = f"[green]持仓中[/green] (→提前平仓)"
        
        return Panel(
            f"[bold cyan]ETHUSDC[/bold cyan]  |  价格：[yellow]{price_text}{price_arrow}[/yellow]  |  {ws_status}  |  杠杆：[bold]{self.actual_leverage}x/{self.leverage}x[/bold]  |  {status}",
            title=f"py-shortqt v{__version__}"
        )
    
    def _render_ws_status(self) -> str:
        """渲染 WebSocket 连接状态"""
        # 行情 WebSocket 状态
        if hasattr(self.trader, 'listener') and self.trader.listener:
            if self.trader.listener.connected:
                ws_market = "[green]●[/green]"  # 绿色圆点
            else:
                ws_market = "[red]●[/red]"  # 红色圆点
        else:
            ws_market = "[dim]●[/dim]"  # 灰色圆点
        
        # 用户数据流 WebSocket 状态
        if hasattr(self.trader, 'user_stream_ws') and self.trader.user_stream_ws:
            if self.trader.user_stream_ws.connected:
                ws_user = "[green]●[/green]"  # 绿色圆点
            else:
                ws_user = "[red]●[/red]"  # 红色圆点
        else:
            ws_user = "[dim]●[/dim]"  # 灰色圆点
        
        return f"行情：{ws_market} 订单：{ws_user}"
    
    def _render_orderbook(self, max_levels: int = 15) -> Table:
        """渲染订单簿（动态调整档位，最新价永远居中，特殊价格分类标记）"""
        from decimal import Decimal

        ob_table = Table(show_header=False, box=None, padding=(0, 1))
        ob_table.add_column("价格", justify="right", width=10)
        ob_table.add_column("数量", justify="right", width=10)

        asks = self.trader.orderbook.get('asks', [])
        bids = self.trader.orderbook.get('bids', [])

        # 收集所有用户挂单价格 → {price: (symbol, color_tag)}
        user_order_prices: dict[float, tuple[str, str]] = {}

        def _add_price(price, symbol, color):
            p = float(price)
            if p > 0:
                user_order_prices[p] = (symbol, color)

        # 止盈单 ✦
        if hasattr(self.trader, 'tp_order') and self.trader.tp_order:
            _add_price(self.trader.tp_order.get('price', 0), '✦', 'bold green')

        # 止损单 ✕
        if hasattr(self.trader, 'sl_order') and self.trader.sl_order:
            _add_price(self.trader.sl_order.get('price', 0), '✕', 'bold red')

        # 保底止损 ◆
        if hasattr(self.trader, 'stop_market_order') and self.trader.stop_market_order:
            _add_price(self.trader.stop_market_order.get('trigger', 0), '◆', 'bold red')

        # 移动止损网格 ○
        if (hasattr(self.trader, 'trailing_stop_manager') and
                self.trader.trailing_stop_manager and
                self.trader.trailing_stop_manager.enabled and
                self.trader.trailing_stop_manager.grid_prices):
            for gp in self.trader.trailing_stop_manager.grid_prices:
                _add_price(gp, '○', 'yellow')

        # 开仓挂单 ◀
        if hasattr(self.trader, 'pending_order') and self.trader.pending_order:
            _add_price(self.trader.pending_order['price'], '◀', 'bold magenta')

        # 提前平仓单 ◀
        if hasattr(self.trader, 'early_close_order') and self.trader.early_close_order:
            _add_price(self.trader.early_close_order.get('price', 0), '◀', 'bold magenta')

        # v1.9.0：分批订单标记
        if self.trader.batch_state and self.trader.batch_state.get('enabled') and not self.trader.batch_state.get('round_closed'):
            for b in self.trader.batch_state.get('batches', []):
                if b['status'] == 'pending' and b.get('price', 0) > 0:
                    _add_price(b['price'], '▸', 'bold magenta')
                elif b['status'] == 'tp_placed' and b.get('tp_price', 0) > 0:
                    _add_price(b['tp_price'], '✦', 'bold cyan')
            # 提前平仓单（以当前价标记）
            if self.trader.batch_state.get('early_close_order_id') and self.trader.last_price:
                _add_price(self.trader.last_price, '▶', 'bold yellow')

        # 订单簿排序：最新价永远居中，卖盘在上，买盘在下，数量相等
        display_levels = min(len(bids), len(asks), max_levels)

        def _render_price(price, price_float, qty):
            """返回订单簿行文本"""
            if price_float in user_order_prices:
                symbol, color = user_order_prices[price_float]
                return f"[{color}]{symbol} {price:.2f}[/{color}]", f"[{color}]{qty:.3f}[/{color}]"
            else:
                return None, None

        # 卖盘（倒序：从远到近，价格从高到低）
        for i in range(display_levels - 1, -1, -1):
            price, qty = asks[i]
            price_float = float(price)
            price_text, qty_text = _render_price(price, price_float, qty)
            if price_text:
                ob_table.add_row(price_text, qty_text)
            else:
                ob_table.add_row(f"[red]{price:.2f}[/red]", f"{qty:.3f}")

        # 最新价（居中显示）
        if self.trader.last_price:
            mid_price = f"{self.trader.last_price:.2f}"
            ob_table.add_row(f"[bold yellow]  {mid_price}  [/bold yellow]", "")
        else:
            ob_table.add_row(f"[bold yellow]  ----  [/bold yellow]", "")

        # 买盘（正序：从近到远，价格从高到低）
        for i in range(display_levels):
            price, qty = bids[i]
            price_float = float(price)
            price_text, qty_text = _render_price(price, price_float, qty)
            if price_text:
                ob_table.add_row(price_text, qty_text)
            else:
                ob_table.add_row(f"[green]{price:.2f}[/green]", f"{qty:.3f}")

        return ob_table

    def _render_orderbook_with_header(self, asks: list, bids: list, max_levels: int):
        """订单簿 + 近X分钟价格范围头部"""
        from rich.table import Table as RTable
        wrapper = RTable(show_header=False, box=None, padding=(0, 0))
        wrapper.add_column("", ratio=1, width=25)
        wrapper.add_row(self._render_price_range_header())
        wrapper.add_row(self._render_orderbook_levels(asks, bids, max_levels))
        return wrapper

    def _render_price_range_header(self) -> Text:
        """渲染近X分钟最高/最低价（3行）"""
        header = Text()

        minutes = 30
        if self.config_manager:
            minutes = self.config_manager.get('price_range.minutes', 30)

        high = None
        low = None
        if self.indicators and hasattr(self.indicators, 'price_range'):
            high = self.indicators.price_range.get_high()
            low = self.indicators.price_range.get_low()

        current = float(self.trader.last_price) if self.trader.last_price else None

        header.append(f"{minutes} 分钟内\n", style="dim")

        if high is not None:
            pct = f"+{(high - current) / current * 100:.4f}%" if current and current > 0 else ""
            header.append(f"最高：{high:.2f}", style="green")
            if pct:
                header.append(f"  {pct}", style="green")
        else:
            header.append("最高：--", style="dim")
        header.append("\n")

        if low is not None:
            pct = f"{(low - current) / current * 100:.4f}%" if current and current > 0 else ""
            header.append(f"最低：{low:.2f}", style="red")
            if pct:
                header.append(f"  {pct}", style="red")
        else:
            header.append("最低：--", style="dim")

        return header

    def _render_orderbook_levels(self, asks: list, bids: list, max_levels: int) -> Table:
        """渲染订单簿买卖盘（接收已截断的 asks/bids 列表）"""
        from rich.table import Table
        ob_table = Table(show_header=False, box=None, padding=(0, 1))
        ob_table.add_column("价格", justify="right", width=10)
        ob_table.add_column("数量", justify="right", width=10)

        # 收集所有用户挂单价格
        user_order_prices: dict[float, tuple[str, str]] = {}

        def _add_price(price, symbol, color):
            p = float(price)
            if p > 0:
                user_order_prices[p] = (symbol, color)

        if hasattr(self.trader, 'tp_order') and self.trader.tp_order:
            _add_price(self.trader.tp_order.get('price', 0), '✦', 'bold green')
        if hasattr(self.trader, 'sl_order') and self.trader.sl_order:
            _add_price(self.trader.sl_order.get('price', 0), '✕', 'bold red')
        if hasattr(self.trader, 'stop_market_order') and self.trader.stop_market_order:
            _add_price(self.trader.stop_market_order.get('trigger', 0), '◆', 'bold red')
        if (hasattr(self.trader, 'trailing_stop_manager') and
                self.trader.trailing_stop_manager and
                self.trader.trailing_stop_manager.enabled and
                self.trader.trailing_stop_manager.grid_prices):
            for gp in self.trader.trailing_stop_manager.grid_prices:
                _add_price(gp, '○', 'yellow')
        if hasattr(self.trader, 'pending_order') and self.trader.pending_order:
            _add_price(self.trader.pending_order['price'], '◀', 'bold magenta')
        if hasattr(self.trader, 'early_close_order') and self.trader.early_close_order:
            _add_price(self.trader.early_close_order.get('price', 0), '◀', 'bold magenta')

        display_levels = min(len(bids), len(asks), max_levels)

        def _render_price(price, price_float, qty):
            if price_float in user_order_prices:
                symbol, color = user_order_prices[price_float]
                return f"[{color}]{symbol} {price:.2f}[/{color}]", f"[{color}]{qty:.3f}[/{color}]"
            return None, None

        # 卖盘（倒序：从远到近）
        for i in range(display_levels - 1, -1, -1):
            price, qty = asks[i]
            price_float = float(price)
            price_text, qty_text = _render_price(price, price_float, qty)
            if price_text:
                ob_table.add_row(price_text, qty_text)
            else:
                ob_table.add_row(f"[red]{price:.2f}[/red]", f"{qty:.3f}")

        # 最新价居中
        if self.trader.last_price:
            mid_price = f"{self.trader.last_price:.2f}"
            ob_table.add_row(f"[bold yellow]  {mid_price}  [/bold yellow]", "")
        else:
            ob_table.add_row(f"[bold yellow]  ----  [/bold yellow]", "")

        # 买盘（正序：从近到远）
        for i in range(display_levels):
            price, qty = bids[i]
            price_float = float(price)
            price_text, qty_text = _render_price(price, price_float, qty)
            if price_text:
                ob_table.add_row(price_text, qty_text)
            else:
                ob_table.add_row(f"[green]{price:.2f}[/green]", f"{qty:.3f}")

        return ob_table

    def _render_position_history(self) -> Text:
        """渲染历史持仓（4行卡片：方向/PnL | 开仓 | 平仓 | 费用）"""
        from rich.text import Text
        from datetime import datetime
        history_text = Text()
        now = datetime.now()

        if not hasattr(self.trader, 'position_history') or not self.trader.position_history:
            history_text.append("暂无历史持仓", style="dim")
            return history_text

        positions = list(self.trader.position_history)[-10:]
        total_count = len(self.trader.position_history) if hasattr(self.trader, 'position_history') else 0

        for i, pos in enumerate(positions):
            side = pos.get('side', '')
            status = pos.get('status', '')
            pnl = pos.get('pnl', Decimal('0'))
            fee = pos.get('total_fee', Decimal('0'))
            funding = pos.get('funding_fee', Decimal('0'))
            open_time = pos.get('open_time')
            close_time = pos.get('close_time')
            open_avg = pos.get('open_avg_price', Decimal('0'))
            close_avg = pos.get('close_avg_price')
            size = pos.get('max_size', Decimal('0'))
            closed_size = pos.get('closed_size', Decimal('0'))

            # 第1行：方向 + 状态 + PnL
            side_style = 'bold green' if side == 'LONG' else 'bold red'
            history_text.append("  ", style=side_style)
            history_text.append(side, style=side_style)
            history_text.append("  ")

            if status == '未平仓':
                history_text.append(status, style="yellow")
            elif status == '完全平仓':
                history_text.append(status, style="dim")
            else:
                history_text.append(status, style="cyan")
            history_text.append("  ")

            if status == '未平仓':
                history_text.append("PnL: --", style="yellow")
            elif pnl and pnl > 0:
                history_text.append(f"PnL: +{self.trader.format_money(pnl)}", style="green")
            elif pnl and pnl < 0:
                history_text.append(f"PnL: {self.trader.format_money(pnl)}", style="red")
            else:
                history_text.append(f"PnL: {self.trader.format_money(Decimal('0'))}", style="gray")

            history_text.append("\n")

            # 第2行：开仓信息
            open_t = _fmt_time(open_time, now) if isinstance(open_time, datetime) else ''
            history_text.append(f"开仓 {_fmt_num(open_avg)} x {_fmt_num(size)}  @ {open_t}")
            history_text.append("\n")

            # 第3行：平仓信息
            if status == '未平仓':
                history_text.append("未平仓", style="dim")
            elif status == '部分平仓':
                # 部分平仓：显示已平数量 / 总开仓量
                close_t = _fmt_time(close_time, now) if isinstance(close_time, datetime) else ''
                remaining = size - closed_size if size and closed_size else Decimal('0')
                history_text.append(f"平仓 {_fmt_num(close_avg)} x {_fmt_num(closed_size)}/{_fmt_num(size)}  @ {close_t}")
            else:
                # 完全平仓：显示平仓数量
                close_t = _fmt_time(close_time, now) if isinstance(close_time, datetime) else ''
                history_text.append(f"平仓 {_fmt_num(close_avg)} x {_fmt_num(closed_size)}  @ {close_t}")
            history_text.append("\n")

            # 第4行：费用
            history_text.append(f"手续费 {self.trader.format_money(fee)}  ", style="dim")
            if status == '未平仓':
                history_text.append("资费 --  ", style="dim")
                history_text.append("净利 --", style="dim")
            else:
                net_pnl = pnl - fee - funding if pnl else Decimal('0')
                history_text.append(f"资费 {self.trader.format_money(funding)}  ", style="dim")
                if net_pnl > 0:
                    history_text.append(f"净利 +{self.trader.format_money(net_pnl)}", style="green")
                elif net_pnl < 0:
                    history_text.append(f"净利 {self.trader.format_money(net_pnl)}", style="red")
                else:
                    history_text.append(f"净利 {self.trader.format_money(Decimal('0'))}", style="gray")
            history_text.append("\n")

            # 空行分隔
            history_text.append("\n")

        if total_count > 10:
            history_text.append(f"\n... 共 {total_count} 条", style="dim")

        return history_text

    def _render_account(self) -> Text:
        """渲染账户信息"""
        acc_text = Text()
        
        # 右上角：可用余额 + 占用保证金
        available = float(self.trader.available_balance)
        position_margin = float(self.trader.position_margin)
        order_margin = float(self.trader.order_margin)
        total_occupied = position_margin + order_margin
        
        acc_text.append("可用：", style="default")
        acc_text.append(f"{self.trader.format_money(self.trader.available_balance)}\n", style="green")
        acc_text.append("占用：", style="default")
        acc_text.append(f"{self.trader.format_money(Decimal(str(total_occupied)))}\n\n", style="yellow")
        
        # 持仓信息
        if self.trader.position:
            pos = self.trader.position
            side = "做多" if pos['side'] == 'LONG' else "做空"
            color = 'green' if pos['side'] == 'LONG' else 'red'

            entry = pos['entry_price']
            size = pos['size']

            # 预估盈亏辅助函数
            def _est_pnl_text(price):
                if pos['side'] == 'LONG':
                    pnl = (price - entry) * size
                else:
                    pnl = (entry - price) * size
                pnl_color = "green" if pnl >= 0 else "red"
                sign = '+' if pnl >= 0 else ''
                return f" ({sign}{self.trader.format_money(pnl)})", pnl_color

            pos_time = pos.get('time')
            if pos_time:
                elapsed = int((datetime.now() - pos_time).total_seconds())
                h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
                duration_str = f"{h:02d}:{m:02d}:{s:02d}"
            else:
                duration_str = "--:--:--"
            acc_text.append(f"持仓：{side}  {duration_str}\n", style=f"bold {color}")
            acc_text.append(f"开仓价：{entry:.2f}\n")  # 价格 2 位
            acc_text.append(f"数量：{size:.3f} ETH\n\n")

            # 止盈
            if self.trader.tp_order:
                tp = self.trader.tp_order.get('price', 0)
                pnl_text, pnl_color = _est_pnl_text(tp)
                acc_text.append(f"止盈：{tp:.2f}", style="green")  # 价格 2 位
                acc_text.append(pnl_text + "\n", style=pnl_color)

            # 止损
            if self.trader.sl_order:
                sl = self.trader.sl_order.get('trigger', 0)
                pnl_text, pnl_color = _est_pnl_text(sl)
                acc_text.append(f"止损：{sl:.2f}", style="red")  # 价格 2 位
                acc_text.append(pnl_text + "\n", style=pnl_color)

            # 保底止损
            if self.trader.stop_market_order:
                sm = self.trader.stop_market_order.get('trigger', 0)
                liq = self.trader.stop_market_order.get('liquidation', 0)
                pnl_text, pnl_color = _est_pnl_text(sm)
                acc_text.append(f"保底：{sm:.2f}", style="bold red")  # 价格 2 位
                acc_text.append(pnl_text, style=pnl_color)
                acc_text.append(f" (强平{liq:.2f})\n", style="dim")
            
            # v1.5.0 新增：移动止损和浮亏保护状态
            if self.trader.trailing_stop_manager:
                ts = self.trader.trailing_stop_manager
                if ts.enabled and ts.entry_price and ts.grid_prices:
                    # 用 max_level_reached 判断颜色（价格回落不回退）
                    max_reached = ts.max_level_reached
                    # 当前位置
                    current_level = ts._get_current_level(self.trader.last_price) if self.trader.last_price else 0
                    # 标题行
                    acc_text.append("移动止损：\n", style="bold cyan")
                    # 限制显示行数，避免溢出（最多显示 12 格）
                    max_show = min(len(ts.grid_prices), 12)
                    for i in range(max_show):
                        gp = ts.grid_prices[i]
                        level = i + 1
                        if level == current_level:
                            # 当前所在格 — 黄色高亮
                            marker = "▶"
                            style = "yellow"
                        elif level <= max_reached:
                            # 曾触发但已回落 — 绿色（不回退）
                            marker = "✓"
                            style = "green"
                        else:
                            # 未触发
                            marker = "○"
                            style = "dim"
                        acc_text.append(f"  {marker} 第{level}格：{gp:.2f}\n", style=style)
                    if len(ts.grid_prices) > max_show:
                        acc_text.append(f"  ... 共{len(ts.grid_prices)}格\n", style="dim")

            if self.trader.loss_protection_manager:
                lp_status = self.trader.loss_protection_manager.get_status()
                if lp_status.get('status') == '已保护':
                    acc_text.append(f"浮亏保护：已触发 @ {lp_status.get('protection_time')}\n", style="yellow")
                elif lp_status.get('status') == '检测中':
                    pnl_status = lp_status.get('pnl_status', '未知')
                    remaining_time = lp_status.get('remaining_time', '00:00')
                    acc_text.append(f"浮亏保护：{pnl_status} ({remaining_time})\n", style="dim")
            
            # 浮动盈亏
            if self.trader.last_price:
                entry = pos['entry_price']
                size = pos['size']
                if pos['side'] == 'LONG':
                    pnl = (self.trader.last_price - entry) * size
                else:
                    pnl = (entry - self.trader.last_price) * size
                c = "green" if pnl >= 0 else "red"
                sign_pnl = '+' if pnl >= 0 else ''
                acc_text.append(f"\n浮动：{sign_pnl}{self.trader.format_money(pnl)}", style=c)  # PnL 6 位

                # 最高浮盈 / 最高浮亏
                max_pnl = getattr(self.trader, '_max_float_pnl', None)
                max_pnl_price = getattr(self.trader, '_max_float_pnl_price', None)
                min_pnl = getattr(self.trader, '_min_float_pnl', None)
                min_pnl_price = getattr(self.trader, '_min_float_pnl_price', None)
                if max_pnl is not None and max_pnl_price is not None:
                    sign_max = '+' if max_pnl >= 0 else ''
                    acc_text.append(f"\n最高浮盈：{sign_max}{self.trader.format_money(max_pnl)} @ {max_pnl_price:.2f}", style="green")
                if min_pnl is not None and min_pnl_price is not None:
                    sign_min = '+' if min_pnl >= 0 else ''
                    acc_text.append(f"\n最高浮亏：{sign_min}{self.trader.format_money(min_pnl)} @ {min_pnl_price:.2f}", style="red")
        
        # v1.9.0：分批模式
        elif self.trader.batch_state and self.trader.batch_state.get('enabled') and not self.trader.batch_state.get('round_closed'):
            bs = self.trader.batch_state
            side = bs.get('side', '')
            side_label = "做多" if side == 'LONG' else "做空"
            color = 'green' if side == 'LONG' else 'red'
            filled_count = sum(1 for b in bs.get('batches', []) if b['status'] in ('filled', 'tp_placed', 'tp_closed'))
            total_count = bs.get('total_count', 0)
            filled_size = bs.get('total_filled_size', Decimal('0'))
            avg_entry = bs.get('weighted_avg_entry', Decimal('0'))

            # 时间：取首笔成交时间
            first_fill_ts = None
            for b in bs.get('batches', []):
                if b.get('fill_time'):
                    first_fill_ts = b['fill_time']
                    break
            if first_fill_ts:
                elapsed = int((datetime.now() - first_fill_ts).total_seconds())
                h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
                duration_str = f"{h:02d}:{m:02d}:{s:02d}"
            else:
                duration_str = "--:--:--"

            acc_text.append(f"持仓：{side_label}  {duration_str}\n", style=f"bold {color}")
            acc_text.append(f"已成交：{filled_count}/{total_count} 批  ")
            acc_text.append(f"总量：{filled_size:.3f} ETH\n")
            if avg_entry > 0:
                acc_text.append(f"加权均价：{avg_entry:.2f}\n\n")

            # 止盈单（最多5条）
            tp_batches = [b for b in bs.get('batches', []) if b['status'] == 'tp_placed']
            if tp_batches:
                # 按价格接近当前价排序
                if self.trader.last_price:
                    ref = self.trader.last_price
                    if side == 'LONG':
                        tp_batches.sort(key=lambda b: b.get('tp_price', 0) - ref)
                    else:
                        tp_batches.sort(key=lambda b: ref - b.get('tp_price', 0))
                show_tp = tp_batches[:5]
                acc_text.append(f"止盈单（共 {len(tp_batches)} 笔）：\n", style="green")
                for b in show_tp:
                    acc_text.append(f"  批{b['index']+1} @ {b.get('tp_price', 0):.2f}\n", style="dim")
                if len(tp_batches) > 5:
                    acc_text.append(f"  ... 还有 {len(tp_batches) - 5} 笔未显示\n", style="dim")

            # 未成交挂单（最多5条）
            pending = [b for b in bs.get('batches', []) if b['status'] == 'pending']
            if pending:
                if side == 'LONG':
                    pending.sort(key=lambda b: b.get('price', 0), reverse=True)
                else:
                    pending.sort(key=lambda b: b.get('price', 0))
                show_po = pending[:5]
                acc_text.append(f"未成交挂单（共 {len(pending)} 笔）：\n", style="yellow")
                for b in show_po:
                    acc_text.append(f"  批{b['index']+1} @ {b.get('price', 0):.2f} x {b.get('size', 0):.3f} ETH\n", style="dim")
                if len(pending) > 5:
                    acc_text.append(f"  ... 还有 {len(pending) - 5} 笔未显示\n", style="dim")
            elif filled_count > 0:
                acc_text.append("全部成交\n", style="green")

            # 止损/保底
            sl_id = bs.get('sl_order_id')
            sm_id = bs.get('sm_order_id')
            if sl_id or sm_id:
                acc_text.append("\n")
                if sl_id:
                    acc_text.append(f"止损：触发 {avg_entry:.2f} (均价)\n", style="red")
                if sm_id:
                    acc_text.append("保底：已挂\n", style="bold red")

            # 浮亏保护
            lp_config = self.config_manager.get_loss_protection_config() if self.config_manager else {}
            if lp_config.get('enabled') and bs.get('supplement_blocked'):
                acc_text.append("浮亏保护：已触发\n", style="yellow")
            elif lp_config.get('enabled'):
                acc_text.append(f"浮亏保护：检测中\n", style="dim")

            # 状态标签
            state_labels = {
                'pending_only': '等待成交',
                'partial_filled': '部分成交',
                'all_filled': '全部成交',
                'early_close': '提前平仓中',
            }
            state_label = state_labels.get(bs.get('state', ''), bs.get('state', ''))
            acc_text.append(f"\n状态：[{state_label}]", style="cyan")

            # 提示
            acc_text.append("\n", style="default")
            if bs.get('supplement_blocked'):
                acc_text.append("浮亏保护已禁止补单", style="yellow")
            elif bs.get('state') == 'early_close':
                acc_text.append("← 撤销提前平仓", style="dim")
            else:
                acc_text.append("← 撤单  ↑↓ 补单  → 提前平仓", style="dim")

            # 浮动盈亏
            if self.trader.last_price and avg_entry > 0:
                if side == 'LONG':
                    pnl = (self.trader.last_price - avg_entry) * filled_size
                else:
                    pnl = (avg_entry - self.trader.last_price) * filled_size
                c = "green" if pnl >= 0 else "red"
                sign_pnl = '+' if pnl >= 0 else ''
                acc_text.append(f"\n浮动：{sign_pnl}{self.trader.format_money(pnl)}", style=c)

                max_pnl = getattr(self.trader, '_max_float_pnl', None)
                max_pnl_price = getattr(self.trader, '_max_float_pnl_price', None)
                min_pnl = getattr(self.trader, '_min_float_pnl', None)
                min_pnl_price = getattr(self.trader, '_min_float_pnl_price', None)
                if max_pnl is not None and max_pnl_price is not None:
                    sign_max = '+' if max_pnl >= 0 else ''
                    acc_text.append(f"\n最高浮盈：{sign_max}{self.trader.format_money(max_pnl)} @ {max_pnl_price:.2f}", style="green")
                if min_pnl is not None and min_pnl_price is not None:
                    sign_min = '+' if min_pnl >= 0 else ''
                    acc_text.append(f"\n最高浮亏：{sign_min}{self.trader.format_money(min_pnl)} @ {min_pnl_price:.2f}", style="red")

        elif self.trader.pending_order:
            order = self.trader.pending_order
            side = "做多" if order['side'] == 'LONG' else "做空"
            color = 'green' if order['side'] == 'LONG' else 'red'
            acc_text.append(f"开仓挂单：{side}\n", style=color)
            price = order.get('price', Decimal('0'))
            acc_text.append(f"价格：{price:.2f}\n")  # 价格 2 位
            acc_text.append(f"数量：{order['size']:.3f} ETH\n\n")

            # 移动止损配置
            ts = self.trader.trailing_stop_manager
            if ts and ts.enabled:
                acc_text.append(f"移动止损：已开启 ({ts.grid_count}格)\n", style="bold cyan")
            else:
                acc_text.append("移动止损：未开启\n", style="dim")

            # 浮亏保护配置
            lp = self.trader.loss_protection_manager
            if lp and lp.enabled:
                mins = int(lp.trigger_minutes) if lp.trigger_minutes == int(lp.trigger_minutes) else f"{lp.trigger_minutes:.1f}"
                acc_text.append(f"浮亏保护：已开启 ({mins}分钟)\n", style="bold cyan")
            else:
                acc_text.append("浮亏保护：未开启\n", style="dim")
        
        else:
            acc_text.append("无持仓\n", style="gray")
            
            # 显示止盈止损配置信息
            if self.config_manager:
                acc_text.append("\n")
                acc_text.append("─" * 20 + "\n", style="dim")
                
                # 止盈
                atr_val = None
                if self.indicators:
                    atr_val = self.indicators.volatility.get_atr(14)
                tp_config = self.config_manager.get('take_profit', {})
                tp_mode = tp_config.get('mode', 'fixed')
                if tp_mode == 'fixed':
                    tp_value = tp_config.get('points', 1.00)
                    acc_text.append(f"止盈：+{tp_value:.2f}点\n", style="green")
                elif tp_mode == 'percentage':
                    tp_value = tp_config.get('percent', 0.36)
                    acc_text.append(f"止盈：+{tp_value:.2f}%\n", style="green")
                else:  # atr14
                    tp_coeff = tp_config.get('atr14_coefficient', 1.0)
                    if atr_val:
                        tp_dist = atr_val * tp_coeff
                        acc_text.append(f"止盈：ATR14 {atr_val:.4f}×{tp_coeff:.1f}={tp_dist:.2f}点\n", style="green")
                    else:
                        acc_text.append(f"止盈：ATR14 ×{tp_coeff:.1f} (无ATR数据)\n", style="green")

                # 止损触发
                sl_config = self.config_manager.get('stop_loss', {})
                sl_trigger_mode = sl_config.get('trigger_mode', 'fixed')
                if sl_trigger_mode == 'fixed':
                    sl_trigger_value = abs(sl_config.get('trigger_points', 3.00))
                    acc_text.append(f"止损：触发 -{sl_trigger_value:.2f}点 / 挂单 ", style="red")
                elif sl_trigger_mode == 'percentage':
                    sl_trigger_value = abs(sl_config.get('trigger_percent', 0.50))
                    acc_text.append(f"止损：触发 -{sl_trigger_value:.2f}% / 挂单 ", style="red")
                else:  # atr14
                    sl_coeff = sl_config.get('atr14_coefficient', 1.0)
                    if atr_val:
                        sl_dist = atr_val * sl_coeff
                        acc_text.append(f"止损：触发 ATR14 {atr_val:.4f}×{sl_coeff:.1f}={sl_dist:.2f}点 / 挂单 ", style="red")
                    else:
                        acc_text.append(f"止损：触发 ATR14 ×{sl_coeff:.1f} (无ATR数据) / 挂单 ", style="red")
                
                # 挂单方式
                sl_limit_mode = sl_config.get('limit_mode', 'queue')
                if sl_limit_mode == 'queue':
                    acc_text.append("同向价 1\n", style="dim")
                else:
                    sl_offset = sl_config.get('limit_offset', 10.50)
                    acc_text.append(f"滑点{sl_offset:.2f}点\n", style="dim")
                
                # 保底止损
                sm_config = self.config_manager.get('stop_market', {})
                sm_value = sm_config.get('max_loss_percent', 30.00)
                acc_text.append(f"保底：最大损失{sm_value:.1f}%\n", style="bold red")

            # 移动止损配置
            ts = self.trader.trailing_stop_manager
            if ts and ts.enabled:
                acc_text.append(f"移动止损：已开启 ({ts.grid_count}格)\n", style="bold cyan")
            else:
                acc_text.append("移动止损：未开启\n", style="dim")

            # 浮亏保护配置
            lp = self.trader.loss_protection_manager
            if lp and lp.enabled:
                mins = int(lp.trigger_minutes) if lp.trigger_minutes == int(lp.trigger_minutes) else f"{lp.trigger_minutes:.1f}"
                acc_text.append(f"浮亏保护：已开启 ({mins}分钟)\n", style="bold cyan")
            else:
                acc_text.append("浮亏保护：未开启\n", style="dim")
        
        return acc_text
    
    def _render_indicators(self) -> Table:
        """渲染指标区 - v1.4.3 三行布局"""
        from rich.table import Table
        
        # 如果没有指标管理器，显示提示信息
        if not self.indicators:
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column("提示", style="dim")
            table.add_row("指标模块未初始化")
            return table
        
        # 获取指标数据
        display_data = self.indicators.get_display_data()
        snapshot = self.indicators.get_snapshot()
        liq = snapshot.get('liquidity', {})

        # 创建单列表格（三行布局）
        table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        table.add_column("指标", ratio=1)

        vol_lines = display_data['volatility_lines']
        liq_lines = display_data['liquidity_lines']
        score = display_data['score_display']

        # ATR(14) 原始值和波动率百分比
        vol_snapshot = snapshot.get('volatility', {})
        atr_14 = vol_snapshot.get('atr_14')
        atr_vol_pct = vol_snapshot.get('atr_volatility_percent')

        # 第一行：波动率（横向展示）+ ATR(14)
        vol_row = Text()
        vol_row.append("波动率：", style="bold cyan")
        vol_parts = []
        for line in vol_lines:
            # 清理格式，只保留核心数据
            clean_line = line.replace('🟡', '').replace('🔴', '').replace('[正常]', '').replace('[WARN]', '').strip()
            if clean_line:
                vol_parts.append(clean_line)
        vol_row.append(" | ".join(vol_parts[:5]))  # 最多显示 5 个

        # ATR(14) 显示（含 24h 百分位评价）
        vol_row.append("  |  ", style="dim")
        vol_row.append("ATR(14)：", style="bold cyan")
        if atr_14 is not None:
            vol_row.append(f"{atr_14:.4f}", style="cyan")
            if atr_vol_pct is not None:
                vol_row.append(f" ({atr_vol_pct:.3f}%)", style="yellow")
            # v1.10.0：24h 百分位评价
            atr14_pct = vol_snapshot.get('atr14_percentile', 0)
            atr14_ref = vol_snapshot.get('atr14_ref', 'normal')
            if atr14_pct > 0:
                ref_emoji = {'low': '🔴', 'normal': '🟡', 'elevated': '🟢', 'high': '🔴'}.get(atr14_ref, '🟡')
                vol_row.append(f" [P{atr14_pct} {ref_emoji}]")
        else:
            vol_row.append("--", style="dim")

        # 添加状态标记
        if any('🟡' in l or '🔴' in l for l in vol_lines):
            vol_row.append(" 🟡", style="yellow")
        
        # 第二行：流动性（买卖深度固定宽度 + 价差 + 深度对比）
        liq_row = Text()
        liq_row.append("流动性：", style="bold cyan")

        # 价差信息
        for line in liq_lines:
            clean_line = line.replace('🟡', '').replace('🔴', '').replace('[正常]', '').replace('[充足]', '').replace('[WARN]', '').strip()
            if clean_line:
                liq_row.append(f" {clean_line}", style="cyan")

        # 买卖深度（固定宽度：4位整数+2位小数，不足补0）
        bid_depth = liq.get('bid_depth_surface', 0)
        ask_depth = liq.get('ask_depth_surface', 0)
        total_depth = bid_depth + ask_depth

        bid_str = f"{float(bid_depth):07.2f}"
        ask_str = f"{float(ask_depth):07.2f}"

        liq_row.append(f" 买:{bid_str}ETH", style="green")
        liq_row.append(f" 卖:{ask_str}ETH", style="red")

        # 买卖盘压力滚动采样（100ms 采样，5 分钟窗口百分比展示）
        if total_depth > 0:
            imbalance = (bid_depth - ask_depth) / total_depth
            self.depth_pressure.sample(imbalance)
        buy_pct, sell_pct = self.depth_pressure.get_ratio()
        if self.depth_pressure.total_samples > 0:
            liq_row.append(f" 买盘{buy_pct:.2f}%:卖盘{sell_pct:.2f}%",
                          style="green" if buy_pct >= sell_pct else "red")
        else:
            liq_row.append(" 采样中...", style="dim")

        # v1.10.0：主动成交比率（Taker Buy/Sell）
        if self.trader and hasattr(self.trader, 'indicators') and self.trader.indicators:
            taker = self.trader.indicators.get_taker_ratio()
            buy_pct = taker.get('buy_pct', 50)
            sell_pct = taker.get('sell_pct', 50)
            liq_row.append("  主动: ", style="dim")
            liq_row.append(f"买 {buy_pct:.1f}%", style="green" if buy_pct > 55 else "")
            liq_row.append("  ", style="dim")
            liq_row.append(f"卖 {sell_pct:.1f}%", style="red" if sell_pct > 55 else "")

        # 第三行：综合评分 + 方向 + 分类评分
        score_row = Text()
        score_row.append(f"综合：{score['score']:.1f}/100 ", style=f"bold {score['color']}")
        score_row.append(f"{score['emoji']} {score['recommendation']}  ", style=f"bold {score['color']}")

        # 预计方向
        direction_label = score.get('direction_label', '')
        direction = score.get('direction', 'NONE')
        dir_color = 'green' if direction == 'LONG' else ('red' if direction == 'SHORT' else 'dim')
        score_row.append(f"{direction_label} ", style=dir_color)

        # 置信度
        confidence = score.get('confidence', 0)
        score_row.append(f" 可信度:{confidence:.0%}  ", style="dim")

        # 分类评分
        category_scores = score.get('category_scores', {})
        score_row.append("分类：", style="dim")
        score_row.append(f"趋势:{category_scores.get('trend', 0):.0f} ", style="yellow")
        score_row.append(f"| 波动:{category_scores.get('volatility', 0):.0f} ", style="cyan")
        score_row.append(f"| 深度:{category_scores.get('depth', 0):.0f}", style="cyan")
        
        # 添加行
        table.add_row(vol_row)
        table.add_row(liq_row)
        table.add_row(score_row)
        
        return table

    def _render_stats(self) -> Text:
        """渲染 24 小时数据统计（单行展示）"""
        stats_text = Text()

        if not hasattr(self.trader, 'trade_stats_24h') or not self.trader.trade_stats_24h:
            stats_text.append("暂无数据", style="dim")
            return stats_text

        stats = self.trader.trade_stats_24h
        if stats.get('error'):
            stats_text.append("拉取失败", style="dim")
            return stats_text

        round_count = stats.get('round_count', 0)
        win_count = stats.get('win_count', 0)
        win_rate = stats.get('win_rate', 0)
        total_volume = stats.get('total_volume', Decimal('0'))
        total_pnl = stats.get('total_pnl', Decimal('0'))
        avg_pnl_ratio = stats.get('avg_pnl_ratio', 0)
        avg_hold = stats.get('avg_hold_time', '--')

        # 单行展示所有数据
        stats_text.append(f"交易:{round_count}次  ", style="cyan")
        stats_text.append(f"盈利:{win_count}次  ", style="green")
        stats_text.append("交易量:", style="bold cyan")
        stats_text.append(f"{_fmt_num(total_volume)}ETH  ", style="default")
        stats_text.append("累计盈亏:", style="bold cyan")
        if total_pnl > 0:
            stats_text.append(f"+{self.trader.format_money(total_pnl)}  ", style="green")
        elif total_pnl < 0:
            stats_text.append(f"{self.trader.format_money(total_pnl)}  ", style="red")
        else:
            stats_text.append(f"{self.trader.format_money(Decimal('0'))}  ", style="dim")
        stats_text.append("胜率:", style="bold cyan")
        if win_rate > 50:
            stats_text.append(f"{win_rate:.2f}%  ", style="green")
        elif win_rate > 0:
            stats_text.append(f"{win_rate:.2f}%  ", style="yellow")
        else:
            stats_text.append("--  ", style="dim")
        stats_text.append("盈亏比:", style="bold cyan")
        if avg_pnl_ratio == float('inf'):
            stats_text.append("∞  ", style="green")
        elif avg_pnl_ratio > 0:
            if avg_pnl_ratio >= 1:
                stats_text.append(f"{avg_pnl_ratio:.2f}  ", style="green")
            else:
                stats_text.append(f"{avg_pnl_ratio:.2f}  ", style="yellow")
        else:
            stats_text.append("--  ", style="dim")
        stats_text.append("期望:", style="bold cyan")
        ev = stats.get('expected_value', 0)
        if ev > 0:
            stats_text.append(f"+{self.trader.format_money(Decimal(str(ev)))}  ", style="green")
        elif ev < 0:
            stats_text.append(f"{self.trader.format_money(Decimal(str(ev)))}  ", style="red")
        else:
            stats_text.append(f"{self.trader.format_money(Decimal('0'))}  ", style="dim")
        stats_text.append("平均持仓时间:", style="bold cyan")
        stats_text.append(str(avg_hold), style="default")

        return stats_text

    def _render_log(self) -> Text:
        """渲染日志（带颜色高亮 + 错误显示）"""
        log_text = Text()

        # 显示最近的错误日志（如果有）- 红色背景高亮
        if hasattr(self.trader, 'error_log') and self.trader.error_log:
            errors = self.trader.error_log[-5:]  # 显示最近 5 条错误
            log_text.append("⚠️ 最近错误：\n", style="bold red on black")
            for error in reversed(errors):
                time_str = error.get('time', '')
                if hasattr(time_str, 'strftime'):
                    time_str = time_str.strftime('%H:%M:%S')
                msg = error.get('msg', str(error))
                log_text.append(f"  [{time_str}] ", style="dim")
                log_text.append(f"{msg}\n", style="red")
            log_text.append("\n")

        # 显示最近的操作日志
        if hasattr(self.trader, 'action_log') and self.trader.action_log:
            actions = self.trader.action_log[-10:]  # 显示最近 10 条
            for action in reversed(actions):
                # 完整时间戳：月 - 日 时：分：秒。毫秒
                time_str = action['time'].strftime('%m-%d %H:%M:%S.%f')[:-3] if hasattr(action['time'], 'strftime') else ''
                action_name = action['action']
                details = action['details']

                # --- 优先：含 PnL 的日志，按盈亏着色 ---
                pnl_style = _pnl_style_from_details(details)
                # 对日志文本进行金额脱敏（仅TUI显示，日志文件保留原始值）
                if hasattr(self.trader, 'mask_log_text'):
                    details_masked = self.trader.mask_log_text(details)
                else:
                    details_masked = details

                # 0. 部分成交/超时撤单（优先匹配）
                if '部分成交' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold yellow")
                    log_text.append(f"{details_masked}\n", style="yellow")

                elif '超时撤单' in action_name:
                    if '失败' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details_masked}\n", style="red")
                    else:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold cyan")
                        log_text.append(f"{details_masked}\n", style="cyan")

                # 1. 成交类 — 含 PnL 的按盈亏，否则按类型
                elif '成交' in action_name:
                    if pnl_style:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style=f"bold {pnl_style}")
                        log_text.append(f"{details_masked}\n", style=pnl_style)
                    elif '开仓' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold cyan")
                        log_text.append(f"{details_masked}\n", style="cyan")
                    elif '止盈' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold green")
                        log_text.append(f"{details_masked}\n", style="green")
                    elif '止损' in action_name or '保底止损' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details_masked}\n", style="red")
                    else:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold yellow")
                        log_text.append(f"{details_masked}\n", style="yellow")

                # 2. 下单类（止盈/止损/保底/开仓/提前平仓）
                elif '已下' in action_name or '挂单' in action_name:
                    if '止盈' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold green")
                        log_text.append(f"{details_masked}\n", style="green")
                    elif '止损' in action_name or '保底' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details_masked}\n", style="red")
                    else:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold blue")
                        log_text.append(f"{details_masked}\n", style="blue")

                # 3. 撤销/取消类
                elif '撤销' in action_name or '取消' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="dim yellow")
                    log_text.append(f"{details_masked}\n", style="dim yellow")

                # 4. 持仓同步/更新类
                elif '持仓同步' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold cyan")
                    log_text.append(f"{details_masked}\n", style=pnl_style if pnl_style else "cyan")

                # 5. 持仓超时（浮亏保护 / 浮盈保本）
                elif '持仓超时' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold yellow")
                    log_text.append(f"{details_masked}\n", style=pnl_style if pnl_style else "yellow")

                # 6. 移动止损
                elif '移动止损' in action_name:
                    if '失败' in action_name or '失败' in details:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details_masked}\n", style="red")
                    else:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold cyan")
                        log_text.append(f"{details_masked}\n", style="cyan")

                # 7. 恢复/保护类（止盈恢复、浮盈保护等）
                elif '恢复' in action_name or '保护' in action_name or '保本' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold green")
                    log_text.append(f"{details_masked}\n", style="green")

                # 8. 错误/失败类
                elif '错误' in action_name or '失败' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold red")
                    log_text.append(f"{details_masked}\n", style="red")

                # 9. 初始化/开始
                elif '初始化' in action_name or '开始' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold green")
                    log_text.append(f"{details_masked}\n", style="green")

                # 10. 检测类
                elif '检测' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="dim")
                    log_text.append(f"{details_masked}\n", style="default")

                # 11. 异常类
                elif '异常' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold red")
                    log_text.append(f"{details_masked}\n", style="red")

                # 12. 默认
                else:
                    log_text.append(f"{time_str}  {action_name}  {details}\n")
        else:
            log_text.append("等待操作...\n", style="dim")

        return log_text

