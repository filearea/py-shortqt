# -*- coding: utf-8 -*-
"""
TUI 设置界面 - v1.5.0
"""

from rich.panel import Panel
from decimal import Decimal
from typing import Any, Tuple, List
from src import __version__


class SettingsUI:
    """设置界面"""
    
    def __init__(self, config_manager, trader=None):
        self.config_manager = config_manager
        self.trader = trader
        self.current_tab = 0
        self.current_field = 0
        self.editing = False
        self.input_buffer = ""
        self.modified = False
        
        self.tabs = [
            {
                'name': '交易参数',
                'fields': [
                    {'key': 'take_profit.mode', 'label': '止盈模式', 'type': 'select', 
                     'options': ['fixed', 'percentage'], 'labels': ['固定点数', '百分比']},
                    {'key': 'take_profit.points', 'label': '止盈点数', 'type': 'decimal', 
                     'min': 0.01, 'max': 100, 'step': 0.01, 'unit': '点', 
                     'visible_cond': lambda c: c.get('take_profit', {}).get('mode') == 'fixed'},
                    {'key': 'take_profit.percent', 'label': '止盈百分比', 'type': 'decimal', 
                     'min': 0.01, 'max': 10, 'step': 0.01, 'unit': '%',
                     'visible_cond': lambda c: c.get('take_profit', {}).get('mode') == 'percentage'},
                    
                    {'key': 'stop_loss.trigger_mode', 'label': '止损触发模式', 'type': 'select',
                     'options': ['fixed', 'percentage'], 'labels': ['固定点数', '百分比']},
                    {'key': 'stop_loss.trigger_points', 'label': '止损触发点数', 'type': 'decimal',
                     'min': 0.01, 'max': 100, 'step': 0.01, 'unit': '点',
                     'visible_cond': lambda c: c.get('stop_loss', {}).get('trigger_mode') == 'fixed'},
                    {'key': 'stop_loss.trigger_percent', 'label': '止损触发百分比', 'type': 'decimal',
                     'min': 0.01, 'max': 10, 'step': 0.01, 'unit': '%',
                     'visible_cond': lambda c: c.get('stop_loss', {}).get('trigger_mode') == 'percentage'},
                    
                    {'key': 'stop_loss.limit_mode', 'label': '实际止损价模式', 'type': 'select',
                     'options': ['queue', 'custom'], 'labels': ['同向价 1', '自定义滑点']},
                    {'key': 'stop_loss.limit_offset', 'label': '自定义滑点', 'type': 'decimal',
                     'min': 0.01, 'max': 100, 'step': 0.01, 'unit': '点',
                     'visible_cond': lambda c: c.get('stop_loss', {}).get('limit_mode') == 'custom'},
                    
                    {'key': 'stop_market.max_loss_percent', 'label': '最大损失比例', 'type': 'decimal',
                     'min': 0.1, 'max': 80, 'step': 0.1, 'unit': '%'},
                    
                    {'key': 'leverage.api', 'label': 'API 杠杆', 'type': 'int',
                     'min': 1, 'max': 125, 'step': 1},
                    {'key': 'leverage.actual', 'label': '实际杠杆', 'type': 'int',
                     'min': 1, 'max': 125, 'step': 1},
                    
                    {'key': 'order_timeout_seconds', 'label': '订单超时', 'type': 'decimal',
                     'min': 0.5, 'max': 30, 'step': 0.5, 'unit': 's'},
                ]
            },
            {
                'name': '智能止损',
                'fields': [
                    {'key': 'trailing_stop.enabled', 'label': '启用移动止损', 'type': 'bool'},
                    {'key': 'trailing_stop.grid_count', 'label': '移动止损格数', 'type': 'int',
                     'min': 3, 'max': 20, 'step': 1, 'unit': '格',
                     'visible_cond': lambda c: c.get('trailing_stop', {}).get('enabled', False)},
                    
                    {'key': 'loss_protection.enabled', 'label': '启用浮亏保护', 'type': 'bool'},
                    {'key': 'loss_protection.trigger_minutes', 'label': '浮亏保护触发时间', 'type': 'int',
                     'min': 1, 'max': 60, 'step': 1, 'unit': '分钟',
                     'visible_cond': lambda c: c.get('loss_protection', {}).get('enabled', False)},
                ]
            },
            {'name': '备份管理', 'fields': []}
        ]
    
    def render(self) -> Panel:
        """渲染设置界面"""
        try:
            lines = []
            
            # 根据当前标签页显示标题
            tab_name = self.tabs[self.current_tab]['name']
            
            if self.editing:
                lines.append(f"[bold cyan]⚙️ {tab_name}[/bold cyan]  Enter 确认  Esc 取消  Tab 换页  S 保存退出")
            else:
                lines.append(f"[bold cyan]⚙️ {tab_name}[/bold cyan]  ↑↓切换  ←→调整  Enter 编辑  Tab 换页  S 保存退出")
            lines.append("")
            
            if self.current_tab == 0:
                lines.extend(self._render_trading_tab_lines())
            elif self.current_tab == 1:
                lines.extend(self._render_smart_stop_tab_lines())
            else:
                lines.extend(self._render_backup_tab_lines())
            
            # 日志区域
            if self.trader and hasattr(self.trader, 'action_log'):
                actions = self.trader.action_log
                if actions:
                    lines.append("")
                    lines.append("─" * 50)
                    lines.append("[bold]最近操作[/bold]")
                    for action in reversed(actions[-5:]):
                        time_str = action['time'].strftime('%H:%M:%S') if hasattr(action['time'], 'strftime') else ''
                        lines.append(f"  [{time_str}] {action['action']}: {action['details']}")
            
            lines.append("")
            lines.append(self._render_footer_lines())
            
            content = "\n".join(lines)
            return Panel(content, title=f"py-shortqt v{__version__}")
        except Exception as e:
            return Panel(f"[red]渲染错误：{e}[/red]", title="错误")
    
    def _get_visible_fields(self) -> List[Tuple[int, dict]]:
        """获取可见字段列表"""
        if self.current_tab != 0:
            return []
        
        config = self.config_manager.get_config()
        visible = []
        
        for i, field in enumerate(self.tabs[0]['fields']):
            if 'visible_cond' in field:
                if not field['visible_cond'](config):
                    continue
            visible.append((i, field))
        
        return visible
    
    def _render_trading_tab_lines(self) -> list:
        """渲染交易参数标签页"""
        config = self.config_manager.get_config()
        lines = []
        visible_fields = self._get_visible_fields()
        
        if not visible_fields:
            lines.append("无可用配置项")
            return lines
        
        max_field = len(visible_fields) - 1
        if self.current_field > max_field:
            self.current_field = max_field
        if self.current_field < 0:
            self.current_field = 0
        
        for visible_idx, (original_idx, field) in enumerate(visible_fields):
            is_selected = (visible_idx == self.current_field)
            value = self._get_nested_value(config, field['key'])
            
            if field['type'] == 'select':
                options = field['options']
                labels = field.get('labels', options)
                current_idx = options.index(value) if value in options else 0
                label = labels[current_idx]
                
                if is_selected:
                    if self.editing:
                        option_strs = []
                        for j, opt_label in enumerate(labels):
                            if j == current_idx:
                                option_strs.append(f"[green]●{opt_label}[/green]")
                            else:
                                option_strs.append(f"○{opt_label}")
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] {' '.join(option_strs)}  ←→切换  Enter 确认")
                    else:
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] [green]{label}[/green]  [dim][Enter 切换][/dim]")
                else:
                    lines.append(f"  {field['label']}: {label}")
            
            elif field['type'] in ['decimal', 'int']:
                unit = field.get('unit', '')
                
                if is_selected:
                    if self.editing:
                        # 编辑模式：只显示 input_buffer（包含当前值）
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] [green]{self.input_buffer}_[/green]  [dim][数字输入 Enter 确认][/dim]")
                    else:
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] [green]{value}{unit}[/green]  [dim][←→调整 或 Enter 输入][/dim]")
                else:
                    lines.append(f"  {field['label']}: {value}{unit}")
        
        # 实时计算预览
        lines.append("")
        lines.append("─" * 50)
        
        # 获取进入设置时的最新价格和保证金
        if self.trader:
            entry_price = self.trader.last_price or Decimal('2150.00')
            balance = self.trader.available_balance or Decimal('35.00')
        else:
            entry_price = Decimal('2150.00')
            balance = Decimal('35.00')
        
        lines.append(f"[bold]实时计算预览（开仓价 {entry_price:.2f}，多单，保证金 {balance:.2f}U）[/bold]")
        
        # 获取实际杠杆，计算仓位
        leverage_config = self.config_manager.get('leverage', {})
        actual_leverage = leverage_config.get('actual', 25)
        
        # 计算名义价值和仓位
        notional = balance * actual_leverage
        size = notional / entry_price
        
        # 计算止盈止损价格
        tp_price = self.config_manager.get_take_profit_price(entry_price)
        sl_trigger, _ = self.config_manager.get_stop_loss_params('ETHUSDC', entry_price, 'LONG', size)
        sm_price = self.config_manager.get_stop_market_price(
            entry_price, 'LONG', size, balance, Decimal('2060.00')
        )
        
        # 计算预估 PnL
        tp_pnl = (tp_price - entry_price) * size  # 止盈 PnL
        sl_pnl = (sl_trigger - entry_price) * size  # 止损触发时的亏损
        max_loss_usd = balance * Decimal(str(self.config_manager.get('stop_market.max_loss_percent', 30))) / Decimal('100')  # 保底最大损失
        
        # 计算百分比
        tp_pnl_pct = (tp_pnl / balance * Decimal('100')) if balance > 0 else Decimal('0')
        sl_pnl_pct = (sl_pnl / balance * Decimal('100')) if balance > 0 else Decimal('0')
        max_loss_pct = Decimal(str(self.config_manager.get('stop_market.max_loss_percent', 30)))
        
        lines.append(f"  仓位：{size:.3f} ETH (名义价值：{notional:.2f}U)")
        lines.append(f"  止盈价：{tp_price:.2f}  止损触发：{sl_trigger:.2f}  保底：{sm_price:.2f}")
        lines.append(f"  止盈 PnL：+{tp_pnl:.2f}U (+{tp_pnl_pct:.1f}%)")
        lines.append(f"  止损亏损：{sl_pnl:.2f}U ({sl_pnl_pct:.1f}%)  保底最大损失：{max_loss_usd:.2f}U ({max_loss_pct:.1f}%)")
        
        # 盈亏比
        tp_diff = tp_price - entry_price
        sl_diff = entry_price - sl_trigger
        if sl_diff > 0:
            ratio = tp_diff / sl_diff
            status = "✓" if ratio >= Decimal('1.5') else "⚠️"
            lines.append(f"  盈亏比：{ratio:.2f}:1 {status}")
        
        return lines
    
    def _render_smart_stop_tab_lines(self) -> list:
        """渲染智能止损标签页"""
        config = self.config_manager.get_config()
        lines = []
        
        # 获取智能止损的字段
        smart_stop_fields = self.tabs[1]['fields']
        
        for i, field in enumerate(smart_stop_fields):
            is_selected = (i == self.current_field)
            value = self._get_nested_value(config, field['key'])
            
            if field['type'] == 'bool':
                label = "✓ 是" if value else "○ 否"
                
                if is_selected:
                    if self.editing:
                        option_strs = [f"[green]●是[/green]" if value else "○是", "●否" if not value else "○否"]
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] {' '.join(option_strs)}  ←→切换  Enter 确认")
                    else:
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] [green]{label}[/green]  [dim][←→切换][/dim]")
                else:
                    lines.append(f"  {field['label']}: {label}")
            
            elif field['type'] == 'int':
                # 检查可见性条件
                if 'visible_cond' in field:
                    if not field['visible_cond'](config):
                        continue
                
                unit = field.get('unit', '')
                
                if is_selected:
                    if self.editing:
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] [green]{self.input_buffer}_[/green]  [dim][数字输入 Enter 确认][/dim]")
                    else:
                        lines.append(f"[bold yellow]→ {field['label']}:[/bold yellow] [green]{value}{unit}[/green]  [dim][←→调整 或 Enter 输入][/dim]")
                else:
                    lines.append(f"  {field['label']}: {value}{unit}")
        
        # 底部说明
        lines.append("")
        lines.append("─" * 50)
        lines.append("[dim]移动止损：开仓价到止盈价均分 N 格，第 1 格观察，第 2 格开始触发[/dim]")
        lines.append("[dim]浮亏保护：开仓后 N 分钟检测浮亏，自动下移止盈到开仓价[/dim]")
        
        return lines
    
    def _render_backup_tab_lines(self) -> list:
        """渲染备份管理标签页"""
        lines = []
        backups = self.config_manager.list_backups()
        
        if backups:
            for i, backup in enumerate(backups[:10]):
                prefix = "→ " if i == getattr(self, 'backup_index', 0) else "  "
                lines.append(f"{prefix}{backup}")
        else:
            lines.append("暂无备份")
        
        lines.append("")
        lines.append("B 新建备份  R 恢复选中  X 删除")
        
        return lines
    
    def _render_footer_lines(self) -> str:
        """渲染底部操作提示"""
        if self.editing:
            return "[green]Enter 确认[/green]  [red]Esc 取消[/red]  [yellow]S 保存退出[/yellow]"
        else:
            if self.current_tab == 0:
                return "[green]S 保存退出[/green]  [yellow]D 重置默认[/yellow]  [blue]B 备份[/blue]  [dim]Esc 放弃修改[/dim]"
            else:
                return "B 新建备份  R 恢复选中  X 删除  S 返回"
    
    def _get_nested_value(self, config: dict, key: str) -> Any:
        """获取嵌套配置值"""
        keys = key.split('.')
        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return None
        return value
    
    def _set_nested_value(self, config: dict, key: str, value: Any):
        """设置嵌套配置值"""
        keys = key.split('.')
        d = config
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
    
    def handle_key(self, key: str) -> str:
        """处理按键输入"""
        if key == 's':
            return 'save'
        elif key == 'escape':
            if self.editing:
                self.editing = False
                self.input_buffer = ""
                return 'continue'
            elif self.modified:
                return 'confirm_exit'
            else:
                return 'exit'
        elif key == 'tab':
            self.current_tab = (self.current_tab + 1) % len(self.tabs)
            self.current_field = 0
            self.editing = False
            return 'continue'
        
        if self.current_tab == 0:
            return self._handle_trading_tab_key(key)
        elif self.current_tab == 1:
            return self._handle_smart_stop_tab_key(key)
        else:
            return self._handle_backup_tab_key(key)
    
    def _handle_trading_tab_key(self, key: str) -> str:
        """处理交易参数标签页的按键"""
        config = self.config_manager.get_config()
        visible_fields = self._get_visible_fields()
        
        if not visible_fields:
            return 'continue'
        
        max_field = len(visible_fields) - 1
        if self.current_field > max_field:
            self.current_field = max_field
        if self.current_field < 0:
            self.current_field = 0
        
        visible_idx = self.current_field
        field_idx, field = visible_fields[visible_idx]
        
        if self.editing:
            if field['type'] == 'select':
                if key == 'left':
                    options = field['options']
                    current = self._get_nested_value(config, field['key'])
                    current_idx = options.index(current) if current in options else 0
                    new_idx = (current_idx - 1) % len(options)
                    self._set_nested_value(config, field['key'], options[new_idx])
                    self.config_manager.config = config
                    self.modified = True
                elif key == 'right':
                    options = field['options']
                    current = self._get_nested_value(config, field['key'])
                    current_idx = options.index(current) if current in options else 0
                    new_idx = (current_idx + 1) % len(options)
                    self._set_nested_value(config, field['key'], options[new_idx])
                    self.config_manager.config = config
                    self.modified = True
                elif key == 'enter':
                    self.editing = False
                    self.input_buffer = ""
            
            elif field['type'] in ['decimal', 'int']:
                if key == 'enter':
                    # 确认输入
                    if self.input_buffer:
                        try:
                            if field['type'] == 'int':
                                new_value = int(self.input_buffer)
                            else:
                                new_value = float(self.input_buffer)
                            new_value = max(field.get('min', 0), min(field.get('max', 999), new_value))
                            self._set_nested_value(config, field['key'], new_value)
                            self.config_manager.config = config
                            self.modified = True
                        except ValueError:
                            pass
                    self.editing = False
                    self.input_buffer = ""
                elif key.isdigit() or key == '.':
                    self.input_buffer += key
                elif key == 'backspace':
                    if self.input_buffer:
                        self.input_buffer = self.input_buffer[:-1]
                elif key == 'left':
                    # 在编辑模式下，←减少数值
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        if isinstance(current, float):
                            current = Decimal(str(current)) - Decimal(str(step))
                        else:
                            current -= step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self.input_buffer = str(current)
                        self._set_nested_value(config, field['key'], float(current) if isinstance(current, Decimal) else current)
                        self.config_manager.config = config
                        self.modified = True
                elif key == 'right':
                    # 在编辑模式下，→增加数值
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        if isinstance(current, float):
                            current = Decimal(str(current)) + Decimal(str(step))
                        else:
                            current += step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self.input_buffer = str(current)
                        self._set_nested_value(config, field['key'], float(current) if isinstance(current, Decimal) else current)
                        self.config_manager.config = config
                        self.modified = True
        
        else:
            if key == 'up':
                self.current_field = max(0, self.current_field - 1)
            elif key == 'down':
                self.current_field = min(max_field, self.current_field + 1)
            elif key == 'enter':
                # 进入编辑模式
                if field['type'] in ['decimal', 'int']:
                    # 数值类型：把当前值放入 input_buffer，方便修改
                    current = self._get_nested_value(config, field['key'])
                    self.input_buffer = str(current) if current is not None else ""
                else:
                    self.input_buffer = ""
                self.editing = True
                return 'enter_edit'
            elif key == 'left':
                if field['type'] == 'select':
                    options = field['options']
                    current = self._get_nested_value(config, field['key'])
                    current_idx = options.index(current) if current in options else 0
                    new_idx = (current_idx - 1) % len(options)
                    self._set_nested_value(config, field['key'], options[new_idx])
                    self.config_manager.config = config
                    self.modified = True
                elif field['type'] in ['decimal', 'int']:
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        if isinstance(current, float):
                            current = Decimal(str(current)) - Decimal(str(step))
                        else:
                            current -= step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self._set_nested_value(config, field['key'], float(current) if isinstance(current, Decimal) else current)
                        self.config_manager.config = config
                        self.modified = True
            elif key == 'right':
                if field['type'] == 'select':
                    options = field['options']
                    current = self._get_nested_value(config, field['key'])
                    current_idx = options.index(current) if current in options else 0
                    new_idx = (current_idx + 1) % len(options)
                    self._set_nested_value(config, field['key'], options[new_idx])
                    self.config_manager.config = config
                    self.modified = True
                elif field['type'] in ['decimal', 'int']:
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        if isinstance(current, float):
                            current = Decimal(str(current)) + Decimal(str(step))
                        else:
                            current += step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self._set_nested_value(config, field['key'], float(current) if isinstance(current, Decimal) else current)
                        self.config_manager.config = config
                        self.modified = True
            elif key == 'd':
                return 'reset_confirm'
            elif key == 'b':
                self.config_manager.backup_config()
                return 'backed_up'
        
        return 'continue'
    
    def _handle_smart_stop_tab_key(self, key: str) -> str:
        """处理智能止损标签页的按键"""
        config = self.config_manager.get_config()
        smart_stop_fields = self.tabs[1]['fields']
        
        max_field = len(smart_stop_fields) - 1
        if self.current_field > max_field:
            self.current_field = max_field
        if self.current_field < 0:
            self.current_field = 0
        
        field = smart_stop_fields[self.current_field]
        
        if self.editing:
            if field['type'] == 'bool':
                if key == 'left' or key == 'right':
                    current = self._get_nested_value(config, field['key'])
                    new_value = not (current or False)
                    self._set_nested_value(config, field['key'], new_value)
                    self.config_manager.config = config
                    self.modified = True
                elif key == 'enter':
                    self.editing = False
                    self.input_buffer = ""
            
            elif field['type'] == 'int':
                if key == 'enter':
                    if self.input_buffer:
                        try:
                            new_value = int(self.input_buffer)
                            new_value = max(field.get('min', 0), min(field.get('max', 999), new_value))
                            self._set_nested_value(config, field['key'], new_value)
                            self.config_manager.config = config
                            self.modified = True
                        except ValueError:
                            pass
                    self.editing = False
                    self.input_buffer = ""
                elif key.isdigit():
                    self.input_buffer += key
                elif key == 'backspace':
                    if self.input_buffer:
                        self.input_buffer = self.input_buffer[:-1]
                elif key == 'left':
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        current -= step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self.input_buffer = str(current)
                        self._set_nested_value(config, field['key'], current)
                        self.config_manager.config = config
                        self.modified = True
                elif key == 'right':
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        current += step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self.input_buffer = str(current)
                        self._set_nested_value(config, field['key'], current)
                        self.config_manager.config = config
                        self.modified = True
        else:
            if key == 'up':
                self.current_field = max(0, self.current_field - 1)
            elif key == 'down':
                self.current_field = min(max_field, self.current_field + 1)
            elif key == 'enter':
                if field['type'] == 'int':
                    current = self._get_nested_value(config, field['key'])
                    self.input_buffer = str(current) if current is not None else ""
                else:
                    self.input_buffer = ""
                self.editing = True
                return 'enter_edit'
            elif key == 'left':
                if field['type'] == 'bool':
                    current = self._get_nested_value(config, field['key'])
                    new_value = not (current or False)
                    self._set_nested_value(config, field['key'], new_value)
                    self.config_manager.config = config
                    self.modified = True
                elif field['type'] == 'int':
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        current -= step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self._set_nested_value(config, field['key'], current)
                        self.config_manager.config = config
                        self.modified = True
            elif key == 'right':
                if field['type'] == 'bool':
                    current = self._get_nested_value(config, field['key'])
                    new_value = not (current or False)
                    self._set_nested_value(config, field['key'], new_value)
                    self.config_manager.config = config
                    self.modified = True
                elif field['type'] == 'int':
                    current = self._get_nested_value(config, field['key'])
                    if current is not None:
                        step = field.get('step', 1)
                        current += step
                        current = max(field.get('min', 0), min(field.get('max', 999), current))
                        self._set_nested_value(config, field['key'], current)
                        self.config_manager.config = config
                        self.modified = True
        
        return 'continue'
    
    def _handle_backup_tab_key(self, key: str) -> str:
        """处理备份管理标签页的按键"""
        backups = self.config_manager.list_backups()
        
        if key == 'b':
            self.config_manager.backup_config()
            return 'backed_up'
        elif key == 'r' and backups:
            backup_idx = getattr(self, 'backup_index', 0)
            if backup_idx < len(backups):
                self.config_manager.restore_config(backups[backup_idx])
                return 'restored'
        elif key == 'x' and backups:
            backup_idx = getattr(self, 'backup_index', 0)
            if backup_idx < len(backups):
                self.config_manager.delete_backup(backups[backup_idx])
                return 'deleted'
        
        return 'continue'
    
    def save_config(self) -> Tuple[bool, List[str]]:
        """保存配置"""
        from src.config.validator import ConfigValidator
        
        config = self.config_manager.get_config()
        
        # 自动修正：如果实际杠杆 > API 杠杆，自动覆盖为 API 杠杆
        leverage = config.get('leverage', {})
        api_lev = leverage.get('api', 100)
        actual_lev = leverage.get('actual', 25)
        
        if actual_lev > api_lev:
            config['leverage']['actual'] = api_lev
            self.config_manager.config = config
            self.trader._add_action("ℹ️ 已自动修正", f"实际杠杆调整为{api_lev}x（不能超过 API 杠杆）")
        
        valid, errors = ConfigValidator.validate(config)
        
        if not valid:
            return False, errors
        
        self.config_manager.save()
        self.modified = False
        return True, []
