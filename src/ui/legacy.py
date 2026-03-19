# -*- coding: utf-8 -*-
"""
TUI 界面 - Rich 终端显示
"""

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from decimal import Decimal


class TradingUI:
    """交易界面"""
    
    def __init__(self, state, leverage: int, take_profit: Decimal, stop_loss: Decimal):
        self.state = state
        self.leverage = leverage
        self.take_profit = take_profit
        self.stop_loss = stop_loss
    
    def render(self) -> Layout:
        """渲染界面"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=10)
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
        
        main_layout["orderbook"].update(Panel(self._render_orderbook(), title=""))
        main_layout["account"].update(Panel(self._render_account(), title="账户"))
        layout["main"].update(main_layout)
        
        # 底部日志
        layout["footer"].update(Panel(self._render_log(), title="日志"))
        
        return layout
    
    def _render_header(self) -> Panel:
        """渲染头部"""
        price_text = f"{self.state.last_price:.2f}" if self.state.last_price else "等待..."
        price_arrow = ""
        if self.state.last_price_change:
            price_arrow = " [green]↑[/green]" if self.state.last_price_change > 0 else " [red]↓[/red]"
        
        status = "就绪 - ↑做多 ↓做空 ←撤单 →平仓 Q 退出"
        # 检查是否有挂单
        has_pending = hasattr(self.state, 'pending_order') and self.state.pending_order is not None
        if has_pending:
            if self.state.pending_order.get('close_type') == 'EARLY':
                status = f"[yellow]平仓挂单中[/yellow] @ {self.state.pending_order['price']:.2f} (等待成交)"
            else:
                side_text = '多' if self.state.pending_order['side'] == 'LONG' else '空'
                status = f"[yellow]开仓挂单中[/yellow] - {side_text} @ {self.state.pending_order['price']:.2f} (←撤单)"
        elif self.state.position:
            status = f"[green]持仓中[/green] (→提前平仓)"
        
        # 调试：打印挂单状态
        # if has_pending:
        #     print(f"[UI DEBUG] 挂单中：{self.state.pending_order}")
        
        return Panel(
            f"[bold cyan]ETHUSDC[/bold cyan]  |  价格：[yellow]{price_text}{price_arrow}[/yellow]  |  更新：[green]{self.state.updates_per_second:.1f} Hz[/green]  |  {status}",
            title="Maker Scalper Test"
        )
    
    def _render_orderbook(self) -> Table:
        """渲染订单簿"""
        ob_table = Table(show_header=False, box=None, padding=(0, 1))
        ob_table.add_column("价格", justify="right", width=10)
        ob_table.add_column("数量", justify="right", width=10)
        
        asks = self.state.orderbook.get('asks', [])
        bids = self.state.orderbook.get('bids', [])
        
        # 卖盘（倒序）
        for i in range(9, -1, -1):
            if i < len(asks):
                price, qty = asks[i]
                ob_table.add_row(f"[red]{price:.2f}[/red]", f"{qty:.3f}")
            else:
                ob_table.add_row("", "")
        
        # 最新价
        mid_price = f"{self.state.last_price:.2f}" if self.state.last_price else "----"
        ob_table.add_row(f"[bold yellow]{mid_price}[/bold yellow]", "")
        
        # 买盘
        for i in range(10):
            if i < len(bids):
                price, qty = bids[i]
                ob_table.add_row(f"[green]{price:.2f}[/green]", f"{qty:.3f}")
            else:
                ob_table.add_row("", "")
        
        return ob_table
    
    def _render_account(self) -> Text:
        """渲染账户信息"""
        acc_text = Text()
        balance_val = float(self.state.balance) if hasattr(self.state.balance, '__float__') else self.state.balance
        acc_text.append(f"余额：[green]{balance_val:.2f} USDT[/green]\n\n")
        acc_text.append(f"杠杆：{self.leverage}x\n\n")
        
        if self.state.position:
            pos = self.state.position
            side = "做多" if pos['side'] == 'LONG' else "做空"
            color = 'green' if pos['side'] == 'LONG' else 'red'
            
            acc_text.append(f"[bold {color}]持仓：{side}[/bold {color}]\n")
            acc_text.append(f"开仓价：{pos['entry_price']:.2f}\n")
            acc_text.append(f"数量：{pos['size']:.3f} ETH\n\n")
            
            # 止盈止损目标
            if self.state.last_price:
                tp_dist = pos['tp_price'] - self.state.last_price if pos['side'] == 'LONG' else self.state.last_price - pos['tp_price']
                sl_dist = self.state.last_price - pos['sl_price'] if pos['side'] == 'LONG' else pos['sl_price'] - self.state.last_price
                tp_pct = (tp_dist / self.state.last_price * 100) if self.state.last_price else Decimal(0)
                sl_pct = (sl_dist / self.state.last_price * 100) if self.state.last_price else Decimal(0)
                
                acc_text.append(f"[green]止盈：{pos['tp_price']:.2f}[/green]  距离：{tp_dist:+.2f} ({tp_pct:+.2f}%)\n")
                acc_text.append(f"[red]止损：{pos['sl_price']:.2f}[/red]  距离：{sl_dist:+.2f} ({sl_pct:+.2f}%)\n")
                
                # 浮动盈亏
                if pos['side'] == 'LONG':
                    pnl = (self.state.last_price - pos['entry_price']) * pos['size']
                else:
                    pnl = (pos['entry_price'] - self.state.last_price) * pos['size']
                c = "green" if pnl >= 0 else "red"
                acc_text.append(f"\n[{c}]浮动：{pnl:+.2f} USDT[/{c}]")
        
        elif hasattr(self.state, 'pending_order') and self.state.pending_order:
            order = self.state.pending_order
            side = "做多" if order['side'] == 'LONG' else "做空"
            color = 'green' if order['side'] == 'LONG' else 'red'
            
            acc_text.append(f"[bold {color}]挂单：{side}[/bold {color}]\n")
            acc_text.append(f"挂单价：{order['price']:.2f}\n")
            acc_text.append(f"数量：{order['size']:.3f} ETH\n")
            if not order.get('close_type'):
                acc_text.append(f"\n[dim]按 ← 撤单[/dim]")
        
        else:
            # 调试：检查是否真的有 pending_order
            has_pending = hasattr(self.state, 'pending_order')
            pending_val = self.state.pending_order if has_pending else None
            # print(f"[UI DEBUG] 账户显示：has_pending={has_pending}, pending_val={pending_val}")
            
            acc_text.append("[dim]无持仓[/dim]\n")
            acc_text.append(f"\n止盈：+{self.take_profit} 点\n")
            acc_text.append(f"止损：-{self.stop_loss} 点")
        
        return acc_text
    
    def _render_log(self) -> Text:
        """渲染日志"""
        log_text = Text()
        log_text.append("操作日志:\n", style="bold")
        for act in reversed(self.state.action_log[-6:]):
            t = act['time'].strftime("%H:%M:%S")
            log_text.append(f"  {t} {act['action']} {act['details']}\n")
        
        log_text.append("\n交易记录:\n", style="bold")
        recent = [t for t in self.state.trades if t['type'] in ['TP', 'SL', 'EARLY']][-3:]
        for trade in reversed(recent):
            t = trade['time'].strftime("%H:%M:%S")
            c = "green" if trade['pnl'] >= 0 else "red"
            label = "提前" if trade['type'] == 'EARLY' else trade['type']
            log_text.append(f"  {t} {label} [{c}]{trade['pnl']:+.2f} USDT[/{c}]\n")
        
        if not recent:
            log_text.append("  暂无完成交易\n")
        
        return log_text
