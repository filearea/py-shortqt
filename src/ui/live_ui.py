# -*- coding: utf-8 -*-
"""
实盘交易 UI - v1.4.1 优化布局
"""

import re
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
        """渲染界面 - v1.4.0 验收布局"""
        # v1.5.0 修复：固定 layout 结构，避免抖动
        layout = Layout()
        
        # 固定各部分高度，避免重新计算
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),  # 剩余空间
            Layout(name="footer", size=12),
            Layout(name="indicators", size=5)
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
        
        # 计算订单簿区域可用高度
        # 总高度 - 头部 3 行 - 日志 12 行 - 指标 5 行 - Panel 边框 4 行 = 订单簿区域高度
        # 订单簿区域高度 - 最新价 1 行 = 买卖盘总行数
        # 买卖盘各占一半
        try:
            from rich.console import Console
            console = Console()
            total_height = console.height if console.height else 45
            orderbook_height = total_height - 3 - 12 - 5 - 4
            # 计算每边显示的档数
            levels_per_side = max(5, (orderbook_height - 1) // 2)
            max_levels = min(levels_per_side, 200)  # 最多 200 档
        except:
            max_levels = 15  # 默认 15 档
        
        main_layout["orderbook"].update(Panel(self._render_orderbook(max_levels), title="订单簿"))
        main_layout["account"].update(Panel(self._render_account(), title="账户"))
        layout["main"].update(main_layout)
        
        # 底部日志
        layout["footer"].update(Panel(self._render_log(), title="日志"))
        
        # v1.4.0 新增：指标区（放在日志上面，紧凑布局）
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
    
    def _render_orderbook(self, max_levels: int = 15) -> Table:
        """渲染订单簿（动态调整档位，最新价永远居中，挂单价格标记）"""
        from decimal import Decimal
        
        ob_table = Table(show_header=False, box=None, padding=(0, 1))
        ob_table.add_column("价格", justify="right", width=10)
        ob_table.add_column("数量", justify="right", width=10)
        
        asks = self.trader.orderbook.get('asks', [])
        bids = self.trader.orderbook.get('bids', [])
        
        # 收集所有用户挂单价格
        user_order_prices = set()
        
        # 开仓挂单
        if hasattr(self.trader, 'pending_order') and self.trader.pending_order:
            user_order_prices.add(float(self.trader.pending_order['price']))
        
        # 止盈单
        if hasattr(self.trader, 'tp_order') and self.trader.tp_order:
            user_order_prices.add(float(self.trader.tp_order.get('price', 0)))
        
        # 止损单
        if hasattr(self.trader, 'sl_order') and self.trader.sl_order:
            user_order_prices.add(float(self.trader.sl_order.get('price', 0)))
        
        # 保底止损单
        if hasattr(self.trader, 'stop_market_order') and self.trader.stop_market_order:
            user_order_prices.add(float(self.trader.stop_market_order.get('trigger', 0)))
        
        # 提前平仓单
        if hasattr(self.trader, 'early_close_order') and self.trader.early_close_order:
            user_order_prices.add(float(self.trader.early_close_order.get('price', 0)))
        
        # 移除 0 值
        user_order_prices.discard(0.0)
        
        # 订单簿排序：最新价永远居中，卖盘在上，买盘在下，数量相等
        # 使用传入的 max_levels，取买卖盘中较小的数量
        display_levels = min(len(bids), len(asks), max_levels)
        
        # 卖盘（倒序：从远到近，价格从高到低）- 显示在最新价上方
        for i in range(display_levels - 1, -1, -1):
            price, qty = asks[i]
            price_float = float(price)
            
            # 检查是否是用户挂单价格
            if price_float in user_order_prices:
                ob_table.add_row(f"[bold magenta]◀ {price:.2f}[/bold magenta]", f"[bold magenta]{qty:.3f}[/bold magenta]")
            else:
                ob_table.add_row(f"[red]{price:.2f}[/red]", f"{qty:.3f}")
        
        # 最新价（居中显示）
        if self.trader.last_price:
            mid_price = f"{self.trader.last_price:.2f}"
            ob_table.add_row(f"[bold yellow]  {mid_price}  [/bold yellow]", "")
        else:
            ob_table.add_row(f"[bold yellow]  ----  [/bold yellow]", "")
        
        # 买盘（正序：从近到远，价格从高到低）- 显示在最新价下方
        for i in range(display_levels):
            price, qty = bids[i]
            price_float = float(price)
            
            # 检查是否是用户挂单价格
            if price_float in user_order_prices:
                ob_table.add_row(f"[bold magenta]◀ {price:.2f}[/bold magenta]", f"[bold magenta]{qty:.3f}[/bold magenta]")
            else:
                ob_table.add_row(f"[green]{price:.2f}[/green]", f"{qty:.3f}")
        
        return ob_table
        
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
        
        # 第一行：波动率（横向展示）
        vol_row = Text()
        vol_row.append("波动率：", style="bold cyan")
        vol_parts = []
        for line in vol_lines:
            # 清理格式，只保留核心数据
            clean_line = line.replace('🟡', '').replace('🔴', '').replace('[正常]', '').replace('[WARN]', '').strip()
            if clean_line:
                vol_parts.append(clean_line)
        vol_row.append(" | ".join(vol_parts[:5]))  # 最多显示 5 个
        
        # 添加状态标记
        if any('🟡' in l or '🔴' in l for l in vol_lines):
            vol_row.append(" 🟡", style="yellow")
        
        # 第二行：流动性（买卖深度分开颜色渲染）
        liq_row = Text()
        liq_row.append("流动性：", style="bold cyan")
        for line in liq_lines:
            clean_line = line.replace('🟡', '').replace('🔴', '').replace('[正常]', '').replace('[充足]', '').replace('[WARN]', '').strip()
            if clean_line.startswith('买盘：'):
                liq_row.append(f" {clean_line} ", style="green")
            elif clean_line.startswith('卖盘：'):
                liq_row.append(f" {clean_line} ", style="red")
            elif clean_line:
                liq_row.append(f" {clean_line} ", style="cyan")

        # 深度不平衡指示
        bid_depth = liq.get('bid_depth_surface', 0)
        ask_depth = liq.get('ask_depth_surface', 0)
        total_depth = bid_depth + ask_depth
        if total_depth > 0:
            imbalance = (bid_depth - ask_depth) / total_depth
            if imbalance > 0.15:
                liq_row.append(" 买盘占优 ", style="green")
            elif imbalance < -0.15:
                liq_row.append(" 卖盘占优 ", style="red")
        
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

                # 1. 成交类 — 含 PnL 的按盈亏，否则按类型
                if '成交' in action_name:
                    if pnl_style:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style=f"bold {pnl_style}")
                        log_text.append(f"{details}\n", style=pnl_style)
                    elif '开仓' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold cyan")
                        log_text.append(f"{details}\n", style="cyan")
                    elif '止盈' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold green")
                        log_text.append(f"{details}\n", style="green")
                    elif '止损' in action_name or '保底止损' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details}\n", style="red")
                    else:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold yellow")
                        log_text.append(f"{details}\n", style="yellow")

                # 2. 下单类（止盈/止损/保底/开仓/提前平仓）
                elif '已下' in action_name or '挂单' in action_name:
                    if '止盈' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold green")
                        log_text.append(f"{details}\n", style="green")
                    elif '止损' in action_name or '保底' in action_name:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details}\n", style="red")
                    else:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold blue")
                        log_text.append(f"{details}\n", style="blue")

                # 3. 撤销/取消类
                elif '撤销' in action_name or '取消' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="dim yellow")
                    log_text.append(f"{details}\n", style="dim yellow")

                # 4. 持仓同步/更新类
                elif '持仓同步' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold cyan")
                    log_text.append(f"{details}\n", style=pnl_style if pnl_style else "cyan")

                # 5. 持仓超时（浮亏保护 / 浮盈保本）
                elif '持仓超时' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold yellow")
                    log_text.append(f"{details}\n", style=pnl_style if pnl_style else "yellow")

                # 6. 移动止损
                elif '移动止损' in action_name:
                    if '失败' in action_name or '失败' in details:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold red")
                        log_text.append(f"{details}\n", style="red")
                    else:
                        log_text.append(f"{time_str}  ", style="dim")
                        log_text.append(f"{action_name}  ", style="bold cyan")
                        log_text.append(f"{details}\n", style="cyan")

                # 7. 恢复/保护类（止盈恢复、浮盈保护等）
                elif '恢复' in action_name or '保护' in action_name or '保本' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold green")
                    log_text.append(f"{details}\n", style="green")

                # 8. 错误/失败类
                elif '错误' in action_name or '失败' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold red")
                    log_text.append(f"{details}\n", style="red")

                # 9. 初始化/开始
                elif '初始化' in action_name or '开始' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold green")
                    log_text.append(f"{details}\n", style="green")

                # 10. 检测类
                elif '检测' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="dim")
                    log_text.append(f"{details}\n", style="default")

                # 11. 异常类
                elif '异常' in action_name:
                    log_text.append(f"{time_str}  ", style="dim")
                    log_text.append(f"{action_name}  ", style="bold red")
                    log_text.append(f"{details}\n", style="red")

                # 12. 默认
                else:
                    log_text.append(f"{time_str}  {action_name}  {details}\n")
        else:
            log_text.append("等待操作...\n", style="dim")

        return log_text

