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
from src import __version__


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
    
    def render(self) -> Layout:
        """渲染界面 - v1.4.0"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),  # 自适应高度
            Layout(name="footer", size=12),  # 固定日志 12 行
            Layout(name="indicators", size=6)  # v1.4.0 新增：指标区 6 行（三行布局）
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
        
        # v1.4.0 新增：指标区
        layout["indicators"].update(Panel(self._render_indicators(), title="盘面指标"))
        
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
        
        if self.trader.early_close_order:
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
    
    def _render_orderbook(self) -> Table:
        """渲染订单簿（自适应档位，最多 20 档，最新价居中）"""
        ob_table = Table(show_header=False, box=None, padding=(0, 1))
        ob_table.add_column("价格", justify="right", width=10)
        ob_table.add_column("数量", justify="right", width=10)
        
        asks = self.trader.orderbook.get('asks', [])
        bids = self.trader.orderbook.get('bids', [])
        
        # 计算可用高度（总高度 - 头部 3 行 - 日志 12 行 - 指标 6 行 - 边框等 4 行 = 约 20 行）
        # 卖盘 + 买盘 + 最新价 = 总行数
        # 假设最多 20 档，则卖 10 + 买 10 + 最新价 1 = 21 行
        max_levels = 10  # 单边最多显示 10 档
        
        # 卖盘（倒序，从远到近）
        for i in range(min(max_levels, len(asks)) - 1, -1, -1):
            price, qty = asks[i]
            ob_table.add_row(f"[red]{price:.2f}[/red]", f"{qty:.3f}")
        
        # 最新价（居中显示）
        mid_price = f"{self.trader.last_price:.2f}" if self.trader.last_price else "----"
        ob_table.add_row(f"[bold yellow]▶ {mid_price} ◀[/bold yellow]", "")
        
        # 买盘（从近到远）
        for i in range(min(max_levels, len(bids))):
            price, qty = bids[i]
            ob_table.add_row(f"[green]{price:.2f}[/green]", f"{qty:.3f}")
        
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
            
            # 显示止盈止损配置信息
            if self.config_manager:
                acc_text.append("\n")
                acc_text.append("─" * 20 + "\n", style="dim")
                
                # 止盈
                tp_config = self.config_manager.get('take_profit', {})
                tp_mode = tp_config.get('mode', 'fixed')
                if tp_mode == 'fixed':
                    tp_value = tp_config.get('points', 1.00)
                    acc_text.append(f"止盈：+{tp_value:.2f}点\n", style="green")
                else:
                    tp_value = tp_config.get('percent', 0.36)
                    acc_text.append(f"止盈：+{tp_value:.2f}%\n", style="green")
                
                # 止损触发
                sl_config = self.config_manager.get('stop_loss', {})
                sl_trigger_mode = sl_config.get('trigger_mode', 'fixed')
                if sl_trigger_mode == 'fixed':
                    sl_trigger_value = abs(sl_config.get('trigger_points', 3.00))
                    acc_text.append(f"止损：触发 -{sl_trigger_value:.2f}点 / 挂单 ", style="red")
                else:
                    sl_trigger_value = abs(sl_config.get('trigger_percent', 0.50))
                    acc_text.append(f"止损：触发 -{sl_trigger_value:.2f}% / 挂单 ", style="red")
                
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
        
        return acc_text
    
    def _render_indicators(self) -> Table:
        """渲染指标区 - v1.4.0 新增（纵向三行布局）"""
        from rich.table import Table
        
        # 如果没有指标管理器，显示提示信息
        if not self.indicators:
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column("提示", style="dim")
            table.add_row("指标模块未初始化")
            return table
        
        # 获取指标数据
        display_data = self.indicators.get_display_data()
        
        # 创建单列表格（三行）
        table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
        table.add_column("指标", ratio=1)
        
        vol_lines = display_data['volatility_lines']
        liq_lines = display_data['liquidity_lines']
        score = display_data['score_display']
        
        # 第一行：波动率（横向展示）
        vol_row = Text()
        vol_row.append("波动率：", style="bold cyan")
        vol_parts = []
        for line in vol_lines:
            # 清理格式，只保留核心数据
            clean_line = line.replace('🟡', '').replace('🔴', '').replace('[正常]', '').strip()
            if clean_line:
                vol_parts.append(clean_line)
        vol_row.append(" | ".join(vol_parts[:5]))  # 最多显示 5 个
        
        # 添加状态标记
        if any('🟡' in l or '🔴' in l for l in vol_lines):
            vol_row.append(" 🟡", style="yellow")
        
        # 第二行：流动性（横向展示）
        liq_row = Text()
        liq_row.append("流动性：", style="bold cyan")
        liq_parts = []
        for line in liq_lines:
            clean_line = line.replace('🟡', '').replace('🔴', '').replace('[正常]', '').replace('[充足]', '').strip()
            if clean_line:
                liq_parts.append(clean_line)
        liq_row.append(" | ".join(liq_parts[:3]))  # 最多显示 3 个
        
        if any('🟡' in l or '🔴' in l for l in liq_lines):
            liq_row.append(" 🟡", style="yellow")
        
        # 第三行：交易建议（信号灯 + 评分）
        score_row = Text()
        score_row.append("建议：", style="bold cyan")
        score_row.append(f" {score['emoji']} ", style=f"bold {score['color']}")
        score_row.append(f"{score['recommendation']} ", style=f"bold {score['color']}")
        score_row.append(f"| 评分 {score['score']}/100", style="dim")
        
        # 添加三行
        table.add_row(vol_row)
        table.add_row(liq_row)
        table.add_row(score_row)
        
        return table

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

