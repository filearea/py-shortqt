# -*- coding: utf-8 -*-
"""
TUI 设置界面
"""

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from decimal import Decimal
from typing import Any, Optional, Tuple, List


class SettingsUI:
    """设置界面"""
    
    def __init__(self, config_manager, trader=None):
        self.config_manager = config_manager
        self.trader = trader
        self.current_tab = 0  # 0=交易参数，1=备份管理
        self.current_field = 0  # 当前编辑的字段索引
        self.editing = False  # 是否处于编辑模式
        self.input_buffer = ""  # 数字输入缓冲
        self.modified = False  # 是否有未保存的修改
        
        # 字段定义（按 Tab 分组）
        self.tabs = [
            {
                'name': '交易参数',
                'fields': [
                    {'key': 'take_profit.mode', 'label': '止盈模式', 'type': 'select', 'options': ['fixed', 'percentage'], 'labels': ['固定点数', '百分比']},
                    {'key': 'take_profit.points', 'label': '止盈点数', 'type': 'decimal', 'min': 0.01, 'max': 100, 'step': 0.01, 'unit': '点', 'visible_cond': lambda c: c.get('take_profit.mode') == 'fixed'},
                    {'key': 'take_profit.percent', 'label': '止盈百分比', 'type': 'decimal', 'min': 0.01, 'max': 10, 'step': 0.01, 'unit': '%', 'visible_cond': lambda c: c.get('take_profit.mode') == 'percentage'},
                    
                    {'key': 'stop_loss.trigger_mode', 'label': '止损触发模式', 'type': 'select', 'options': ['fixed', 'percentage'], 'labels': ['固定点数', '百分比']},
                    {'key': 'stop_loss.trigger_points', 'label': '止损触发点数', 'type': 'decimal', 'min': 0.01, 'max': 100, 'step': 0.01, 'unit': '点', 'visible_cond': lambda c: c.get('stop_loss.trigger_mode') == 'fixed'},
                    {'key': 'stop_loss.trigger_percent', 'label': '止损触发百分比', 'type': 'decimal', 'min': 0.01, 'max': 10, 'step': 0.01, 'unit': '%', 'visible_cond': lambda c: c.get('stop_loss.trigger_mode') == 'percentage'},
                    
                    {'key': 'stop_loss.limit_mode', 'label': '实际止损价模式', 'type': 'select', 'options': ['queue', 'custom'], 'labels': ['同向价 1', '自定义滑点']},
                    {'key': 'stop_loss.limit_offset', 'label': '自定义滑点', 'type': 'decimal', 'min': 0.01, 'max': 100, 'step': 0.01, 'unit': '点', 'visible_cond': lambda c: c.get('stop_loss.limit_mode') == 'custom'},
                    
                    {'key': 'stop_market.max_loss_percent', 'label': '最大损失比例', 'type': 'decimal', 'min': 10, 'max': 80, 'step': 1, 'unit': '%'},
                    
                    {'key': 'leverage.api', 'label': 'API 杠杆', 'type': 'int', 'min': 1, 'max': 125, 'step': 1},
                    {'key': 'leverage.actual', 'label': '实际杠杆', 'type': 'int', 'min': 1, 'max': 125, 'step': 1},
                    
                    {'key': 'order_timeout_seconds', 'label': '订单超时', 'type': 'decimal', 'min': 0.5, 'max': 30, 'step': 0.5, 'unit': 's'},
                ]
            },
            {
                'name': '备份管理',
                'fields': []
            }
        ]
    
    def render(self) -> Panel:
        """渲染设置界面（简化为 Panel，避免 Layout 性能问题）"""
        try:
            lines = []
            
            # 头部
            lines.append("[bold cyan]⚙️ 设置面板[/bold cyan]  ←→调整  ↑↓切换  Tab 标签  S 返回")
            lines.append("")
            
            # 主体
            if self.current_tab == 0:
                lines.extend(self._render_trading_tab_lines())
            else:
                lines.extend(self._render_backup_tab_lines())
            
            lines.append("")
            lines.append(self._render_footer_lines())
            
            content = "\n".join(lines)
            return Panel(content, title="py-shortqt v1.2.0 设置")
        except Exception as e:
            return Panel(f"[red]渲染错误：{e}[/red]", title="错误")
    
    def _render_trading_tab_lines(self) -> list:
        """渲染交易参数标签页（返回文本行）"""
        config = self.config_manager.get_config()
        lines = []
        
        # 获取可见字段列表
        visible_fields = []
        for i, field in enumerate(self.tabs[0]['fields']):
            if 'visible_cond' in field:
                if not field['visible_cond'](config):
                    continue
            visible_fields.append((i, field))
        
        if not visible_fields:
            lines.append("无可用配置项")
            return lines
        
        # 确保 current_field 在有效范围内
        max_field = len(visible_fields) - 1
        if self.current_field > max_field:
            self.current_field = max_field
        if self.current_field < 0:
            self.current_field = 0
        
        # 渲染每个可见字段
        for visible_idx, (original_idx, field) in enumerate(visible_fields):
            # 渲染字段
            prefix = "→ " if visible_idx == self.current_field else "  "
            value = self._get_nested_value(config, field['key'])
            
            if field['type'] == 'select':
                # 选择类型
                options = field['options']
                labels = field.get('labels', options)
                current_idx = options.index(value) if value in options else 0
                label = labels[current_idx]
                
                if i == self.current_field:
                    lines.append(f"{prefix}[bold yellow]{field['label']}:[/bold yellow] [green]● {label}[/green]  ←→切换")
                else:
                    lines.append(f"{prefix}{field['label']}: {label}")
            
            elif field['type'] in ['decimal', 'int']:
                # 数值类型
                unit = field.get('unit', '')
                
                if self.editing and i == self.current_field:
                    # 编辑模式
                    lines.append(f"{prefix}[bold yellow]{field['label']}:[/bold yellow] {value}{self.input_buffer}_  Enter 确认")
                else:
                    # 显示模式
                    if i == self.current_field:
                        lines.append(f"{prefix}[bold yellow]{field['label']}:[/bold yellow] [green]{value}{unit}[/green]  ←→调整")
                    else:
                        lines.append(f"{prefix}{field['label']}: {value}{unit}")
        
        # 实时计算预览
        lines.append("")
        lines.append("─" * 50)
        lines.append("[bold]实时计算预览（开仓价 2150，多单，保证金 35U）[/bold]")
        
        # 计算示例值
        entry_price = Decimal('2150.00')
        tp_price = self.config_manager.get_take_profit_price(entry_price)
        sl_trigger, _ = self.config_manager.get_stop_loss_params(entry_price, 'LONG', Decimal('0.407'))
        sm_price = self.config_manager.get_stop_market_price(
            entry_price, 'LONG', Decimal('0.407'), Decimal('35.00'), Decimal('2060.00')
        )
        
        lines.append(f"  止盈：{tp_price:.2f}  止损触发：{sl_trigger:.2f}  保底：{sm_price:.2f}")
        
        # 盈亏比
        tp_diff = tp_price - entry_price
        sl_diff = entry_price - sl_trigger
        if sl_diff > 0:
            ratio = tp_diff / sl_diff
            status = "✓" if ratio >= Decimal('1.5') else "⚠️"
            lines.append(f"  盈亏比：{ratio:.2f}:1 {status}")
        
        return lines
    
    def _render_trading_tab(self) -> Panel:
        """渲染交易参数标签页（兼容旧接口）"""
        lines = self._render_trading_tab_lines()
        return Panel("\n".join(lines), title="交易参数")
        
        # 渲染每个字段
        visible_fields = []
        for i, field in enumerate(self.tabs[0]['fields']):
            # 检查可见性
            if 'visible_cond' in field:
                if not field['visible_cond'](config):
                    continue
            
            visible_fields.append((i, field))
            
            # 渲染字段
            prefix = "→ " if i == self.current_field else "  "
            value = self._get_nested_value(config, field['key'])
            
            if field['type'] == 'select':
                # 选择类型
                options = field['options']
                labels = field.get('labels', options)
                current_idx = options.index(value) if value in options else 0
                label = labels[current_idx]
                
                if i == self.current_field:
                    lines.append(f"{prefix}[bold yellow]{field['label']}:[/bold yellow] [green]● {label}[/green]  ←→切换")
                else:
                    lines.append(f"{prefix}{field['label']}: {label}")
            
            elif field['type'] in ['decimal', 'int']:
                # 数值类型
                unit = field.get('unit', '')
                
                if self.editing and i == self.current_field:
                    # 编辑模式
                    lines.append(f"{prefix}[bold yellow]{field['label']}:[/bold yellow] [{value}{self.input_buffer}_]  Enter 确认")
                else:
                    # 显示模式
                    if i == self.current_field:
                        lines.append(f"{prefix}[bold yellow]{field['label']}:[/bold yellow] [green]{value}{unit}[/green]  ←→调整  数字键输入")
                    else:
                        lines.append(f"{prefix}{field['label']}: {value}{unit}")
        
        # 实时计算预览
        lines.append("")
        lines.append("─" * 50)
        lines.append("[bold]实时计算预览（假设开仓价 2150.00，多单，保证金 35U）[/bold]")
        
        # 计算示例值
        entry_price = Decimal('2150.00')
        tp_price = self.config_manager.get_take_profit_price(entry_price)
        sl_trigger, _ = self.config_manager.get_stop_loss_params(entry_price, 'LONG', Decimal('0.407'))
        sm_price = self.config_manager.get_stop_market_price(
            entry_price, 'LONG', Decimal('0.407'), Decimal('35.00'), Decimal('2060.00')
        )
        
        lines.append(f"  止盈价：{tp_price:.2f}")
        lines.append(f"  止损触发：{sl_trigger:.2f}")
        lines.append(f"  保底止损：{sm_price:.2f}")
        
        # 盈亏比
        tp_diff = tp_price - entry_price
        sl_diff = entry_price - sl_trigger
        if sl_diff > 0:
            ratio = tp_diff / sl_diff
            status = "✓" if ratio >= Decimal('1.5') else "⚠️"
            lines.append(f"  盈亏比：{ratio:.2f}:1 {status}")
        

    
    def _render_backup_tab_lines(self) -> list:
        """渲染备份管理标签页（返回文本行）"""
        lines = []
        backups = self.config_manager.list_backups()
        
        if backups:
            for i, backup in enumerate(backups[:10]):  # 最多显示 10 个
                prefix = "→ " if i == getattr(self, 'backup_index', 0) else "  "
                lines.append(f"{prefix}{backup}")
        else:
            lines.append("暂无备份")
        
        lines.append("")
        lines.append("B 新建备份  R 恢复选中  X 删除")
        
        return lines
    
    def _render_backup_tab(self) -> Panel:
        """渲染备份管理标签页（兼容旧接口）"""
        lines = self._render_backup_tab_lines()
        return Panel("\n".join(lines), title="备份管理")
    
    def _render_footer_lines(self) -> str:
        """渲染底部操作提示（返回文本）"""
        if self.current_tab == 0:
            return "[green]Enter 保存[/green]  [yellow]D 重置默认[/yellow]  [blue]B 备份[/blue]"
        else:
            return "B 新建备份  R 恢复选中  X 删除"
    
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
        """
        处理按键输入
        返回：'continue' 继续设置界面，'exit' 退出，'save' 保存并退出，'cancel' 取消
        """
        # 全局快捷键
        if key == 's':
            return 'exit'
        elif key == 'tab':
            self.current_tab = (self.current_tab + 1) % len(self.tabs)
            self.current_field = 0
            return 'continue'
        
        # Tab 0: 交易参数
        if self.current_tab == 0:
            return self._handle_trading_tab_key(key)
        else:
            return self._handle_backup_tab_key(key)
    
    def _handle_trading_tab_key(self, key: str) -> str:
        """处理交易参数标签页的按键"""
        config = self.config_manager.get_config()
        
        # 获取当前可见字段
        visible_fields = []
        for i, field in enumerate(self.tabs[0]['fields']):
            if 'visible_cond' in field:
                if not field['visible_cond'](config):
                    continue
            visible_fields.append((i, field))
        
        if not visible_fields:
            return 'continue'
        
        # 调整当前字段索引到可见范围内
        max_field = len(visible_fields) - 1
        if self.current_field > max_field:
            self.current_field = max_field
        if self.current_field < 0:
            self.current_field = 0
        
        if key == 'up':
            # 上一个字段（基于可见字段索引）
            self.current_field = max(0, self.current_field - 1)
            self.editing = False
            self.input_buffer = ""
        
        elif key == 'down':
            # 下一个字段（基于可见字段索引）
            self.current_field = min(max_field, self.current_field + 1)
            self.editing = False
            self.input_buffer = ""
        
        elif key == 'left':
            # 减少数值（基于可见字段索引）
            visible_idx = self.current_field
            field_idx, field = visible_fields[visible_idx]
            if field['type'] in ['decimal', 'int']:
                current = self._get_nested_value(config, field['key'])
                if current is not None:
                    step = field.get('step', 1)
                    if isinstance(current, float):
                        current = Decimal(str(current)) - Decimal(str(step))
                    else:
                        current -= step
                    # 限制范围
                    current = max(field.get('min', 0), min(field.get('max', 999), current))
                    self._set_nested_value(config, field['key'], float(current) if isinstance(current, Decimal) else current)
                    self.config_manager.config = config
                    self.modified = True
        
        elif key == 'right':
            # 增加数值（基于可见字段索引）
            visible_idx = self.current_field
            field_idx, field = visible_fields[visible_idx]
            if field['type'] in ['decimal', 'int']:
                current = self._get_nested_value(config, field['key'])
                if current is not None:
                    step = field.get('step', 1)
                    if isinstance(current, float):
                        current = Decimal(str(current)) + Decimal(str(step))
                    else:
                        current += step
                    # 限制范围
                    current = max(field.get('min', 0), min(field.get('max', 999), current))
                    self._set_nested_value(config, field['key'], float(current) if isinstance(current, Decimal) else current)
                    self.config_manager.config = config
                    self.modified = True
        
        elif key == 'enter':
            # 确认/切换选项（基于可见字段索引）
            visible_idx = self.current_field
            field_idx, field = visible_fields[visible_idx]
            if field['type'] == 'select':
                # 切换选项
                options = field['options']
                current = self._get_nested_value(config, field['key'])
                current_idx = options.index(current) if current in options else 0
                new_idx = (current_idx + 1) % len(options)
                self._set_nested_value(config, field['key'], options[new_idx])
                self.config_manager.config = config
                self.modified = True
            elif field['type'] in ['decimal', 'int'] and self.editing:
                # 确认输入
                self.editing = False
                self.input_buffer = ""
        
        elif key.isdigit() or key == '.':
            # 数字输入（基于可见字段索引）
            visible_idx = self.current_field
            field_idx, field = visible_fields[visible_idx]
            if field['type'] in ['decimal', 'int']:
                if not self.editing:
                    self.editing = True
                    self.input_buffer = ""
                self.input_buffer += key
        
        elif key == 'd':
            # 重置默认（基于可见字段索引）
            visible_idx = self.current_field
            field_idx, field = visible_fields[visible_idx]
            return 'reset_confirm'
        
        elif key == 'b':
            # 备份（基于可见字段索引）
            visible_idx = self.current_field
            field_idx, field = visible_fields[visible_idx]
            self.config_manager.backup_config()
            return 'backed_up'
        
        return 'continue'
    
    def _handle_backup_tab_key(self, key: str) -> str:
        """处理备份管理标签页的按键"""
        backups = self.config_manager.list_backups()
        
        if key == 'b':
            # 新建备份
            self.config_manager.backup_config()
            return 'backed_up'
        
        elif key == 'r' and backups:
            # 恢复选中备份
            backup_idx = getattr(self, 'backup_index', 0)
            if backup_idx < len(backups):
                self.config_manager.restore_config(backups[backup_idx])
                return 'restored'
        
        elif key == 'x' and backups:
            # 删除选中备份
            backup_idx = getattr(self, 'backup_index', 0)
            if backup_idx < len(backups):
                self.config_manager.delete_backup(backups[backup_idx])
                return 'deleted'
        
        return 'continue'
    
    def save_config(self) -> Tuple[bool, List[str]]:
        """
        保存配置
        返回：(是否成功，错误消息列表)
        """
        from .validator import ConfigValidator
        
        config = self.config_manager.get_config()
        valid, errors = ConfigValidator.validate(config)
        
        if not valid:
            return False, errors
        
        self.config_manager.save()
        return True, []
