# -*- coding: utf-8 -*-
"""
实盘交易 UI
"""

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from decimal import Decimal


class LiveTradingUI:
    """实盘交易界面"""
    
    def __init__(self, trader, leverage: int, take_profit: Decimal = Decimal('1'), stop_loss: Decimal = Decimal('3'), actual_leverage: int = 25):
        self.trader = trader
        self.leverage = leverage  # API 杠杆
        self.actual_leverage = actual_leverage  # 实际杠杆
        self.take_profit = take_profit
        self.stop_loss = stop_loss
    
    def render(self) -> Layout:
        """渲染界面"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),  # 自适应高度
            Layout(name="footer", size=12)  # 固定日志 12 行
        )
        
        # 头部
        header = self._render_header()
        layout["header"].update(header)
        
        # 主体
        main_layout = Layout()
        main_layout.split_row(
            Layout(name="orderbook", ratio=2),
            Layout(name="account", ratio=1)
        )
        
        main_layout["orderbook"].update(Panel(self._render_orderbook(), title="订单簿"))
        main_layout["account"].update(Panel(self._render_account(), title="账户"))
        layout["main"].update(main_layout)
        
        # 底部日志
        layout["footer"].update(Panel(self._render_log(), title="日志"))
        
        return layout
    
    def _render_header(self) -> Panel:
        """渲染头部"""
        price = self.trader.last_price
        price_text = f"{price:.2f}" if price else "等待..."
        
        # 价格变化箭头
        price_arrow = ""
        if hasattr(self.trader, 'last_price_change') and self.trader.last_price_change:
            price_arrow = " [green]↑[/green]" if self.trader.last_price_change > 0 else " [red]↓[/red]"
        
        # 状态
        status = "就绪 - ↑做多 ↓做空 ←撤单 →平仓 S 设置 Q 退出"
        
        if self.trader.early_close_order:
            status = f"[yellow]平仓挂单中[/yellow] @ {self.trader.early_close_order['price']:.2f} (←撤单)"
        elif self.trader.pending_order:
            side_text = '多' if self.trader.pending_order['side'] == 'LONG' else '空'
            status = f"[yellow]开仓挂单中[/yellow] - {side_text} @ {self.trader.pending_order['price']:.2f} (←撤单)"
        elif self.trader.position:
            status = f"[green]持仓中[/green] (→提前平仓)"
        
        return Panel(
            f"[bold cyan]ETHUSDC[/bold cyan]  |  价格：[yellow]{price_text}{price_arrow}[/yellow]  |  杠杆：[bold]{self.actual_leverage}x/{self.leverage}x[/bold]  |  {status}",
            title="py-shortqt v1.2.0 实盘"
        )
    
    def _render_orderbook(self) -> Table:
        """渲染订单簿（7 档）"""
        ob_table = Table(show_header=False, box=None, padding=(0, 1))
        ob_table.add_column("价格", justify="right", width=10)
        ob_table.add_column("数量", justify="right", width=10)
        
        asks = self.trader.orderbook.get('asks', [])
        bids = self.trader.orderbook.get('bids', [])
        
        # 卖盘（倒序，显示 7 档）
        for i in range(6, -1, -1):
            if i < len(asks):
                price, qty = asks[i]
                ob_table.add_row(f"[red]{price:.2f}[/red]", f"{qty:.3f}")
            else:
                ob_table.add_row("", "")
        
        # 最新价
        mid_price = f"{self.trader.last_price:.2f}" if self.trader.last_price else "----"
        ob_table.add_row(f"[bold yellow]{mid_price}[/bold yellow]", "")
        
        # 买盘（显示 7 档）
        for i in range(7):
            if i < len(bids):
                price, qty = bids[i]
                ob_table.add_row(f"[green]{price:.2f}[/green]", f"{qty:.3f}")
            else:
                ob_table.add_row("", "")
        
        return ob_table
    
    def _render_account(self) -> Text:
        """渲染账户信息"""
        acc_text = Text()
        
        # 右上角：可用余额 + 占用保证金
        available = float(self.trader.available_balance)
        position_margin = float(self.trader.position_margin)
        order_margin = float(self.trader.order_margin)
        total_occupied = position_margin + order_margin
        
        acc_text.append("可用：", style="default")
        acc_text.append(f"{available:.6f} U\n", style="green")
        acc_text.append("占用：", style="default")
        acc_text.append(f"{total_occupied:.6f} U\n\n", style="yellow")
        
        # 持仓信息
        if self.trader.position:
            pos = self.trader.position
            side = "做多" if pos['side'] == 'LONG' else "做空"
            color = 'green' if pos['side'] == 'LONG' else 'red'
            
            acc_text.append(f"持仓：{side}\n", style=f"bold {color}")
            acc_text.append(f"开仓价：{pos['entry_price']:.2f}\n")  # 价格 2 位
            acc_text.append(f"数量：{pos['size']:.3f} ETH\n\n")
            
            # 止盈
            if self.trader.tp_order:
                tp = self.trader.tp_order.get('price', 0)
                acc_text.append(f"止盈：{tp:.2f}\n", style="green")  # 价格 2 位
            
            # 止损
            if self.trader.sl_order:
                sl = self.trader.sl_order.get('trigger', 0)
                acc_text.append(f"止损：{sl:.2f}\n", style="red")  # 价格 2 位
            
            # 保底止损
            if self.trader.stop_market_order:
                sm = self.trader.stop_market_order.get('trigger', 0)
                liq = self.trader.stop_market_order.get('liquidation', 0)
                acc_text.append(f"保底：{sm:.2f} (强平{liq:.2f})\n", style="bold red")  # 价格 2 位
            
            # 浮动盈亏
            if self.trader.last_price:
                entry = pos['entry_price']
                size = pos['size']
                if pos['side'] == 'LONG':
                    pnl = (self.trader.last_price - entry) * size
                else:
                    pnl = (entry - self.trader.last_price) * size
                c = "green" if pnl >= 0 else "red"
                acc_text.append(f"\n浮动：{pnl:+.6f} USDT", style=c)  # PnL 6 位
        
        elif self.trader.pending_order:
            order = self.trader.pending_order
            side = "做多" if order['side'] == 'LONG' else "做空"
            color = 'green' if order['side'] == 'LONG' else 'red'
            acc_text.append(f"开仓挂单：{side}\n", style=color)
            price = order.get('price', Decimal('0'))
            acc_text.append(f"价格：{price:.2f}\n")  # 价格 2 位
            acc_text.append(f"数量：{order['size']:.3f} ETH")
        
        else:
            acc_text.append("无持仓\n", style="gray")
        
        return acc_text
    
    def _render_log(self) -> Text:
        """渲染日志（带颜色高亮）"""
        log_text = Text()
        
        # 显示最近的操作日志
        if hasattr(self.trader, 'action_log') and self.trader.action_log:
            actions = self.trader.action_log[-10:]  # 显示最近 10 条
            for action in reversed(actions):
                # 完整时间戳：月 - 日 时：分：秒。毫秒
                time_str = action['time'].strftime('%m-%d %H:%M:%S.%f')[:-3] if hasattr(action['time'], 'strftime') else ''
                action_name = action['action']
                details = action['details']
                
                # 根据日志类型设置颜色
                if '成交' in action_name:
                    if '开仓' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold cyan")
                        log_text.append(f"{details}\n", style="cyan")
                    elif '止盈' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold green")
                        log_text.append(f"{details}\n", style="green")
                    elif '止损' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details}\n", style="red")
                    elif '平仓' in action_name or 'PnL' in details:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold yellow")
                        # PnL 正负颜色
                        if 'PnL' in details:
                            if '+' in details:
                                log_text.append(f"{details}\n", style="green")
                            elif '-' in details:
                                log_text.append(f"{details}\n", style="red")
                            else:
                                log_text.append(f"{details}\n", style="yellow")
                        else:
                            log_text.append(f"{details}\n", style="yellow")
                elif '挂单' in action_name or '已下' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="blue")
                    log_text.append(f"{details}\n", style="default")
                elif '撤销' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="dim")
                    log_text.append(f"{details}\n", style="dim")
                else:
                    log_text.append(f"{time_str}  {action_name}  {details}\n")
        else:
            log_text.append("等待操作...\n", style="dim")
        
        return log_text
