# -*- coding: utf-8 -*-
"""
配置验证器 - 验证配置合理性
"""

from decimal import Decimal
from typing import Tuple, List, Any


class ConfigValidator:
    """配置验证器"""
    
    @staticmethod
    def validate(config: dict) -> Tuple[bool, List[str]]:
        """
        验证配置合理性
        返回：(是否有效，错误消息列表)
        """
        errors = []
        
        # 1. 止盈验证
        tp = config.get('take_profit', {})
        mode = tp.get('mode', 'fixed')
        
        if mode == 'fixed':
            points = tp.get('points', 0)
            if points < 0.01:
                errors.append("止盈点数不能小于 0.01")
            if points > 100:
                errors.append("止盈点数不能大于 100")
        else:
            percent = tp.get('percent', 0)
            if percent < 0.01:
                errors.append("止盈百分比不能小于 0.01%")
            if percent > 10:
                errors.append("止盈百分比不能大于 10%")
        
        # 2. 止损验证
        sl = config.get('stop_loss', {})
        trigger_mode = sl.get('trigger_mode', 'fixed')
        
        if trigger_mode == 'fixed':
            points = abs(sl.get('trigger_points', 0))
            if points < 0.01:
                errors.append("止损触发点数不能小于 0.01")
        else:
            percent = abs(sl.get('trigger_percent', 0))
            if percent < 0.01:
                errors.append("止损触发百分比不能小于 0.01%")
        
        # 滑点验证
        limit_mode = sl.get('limit_mode', 'queue')
        if limit_mode == 'custom':
            offset = sl.get('limit_offset', 0)
            if offset < 0.01:
                errors.append("滑点点数不能小于 0.01")
        
        # 3. 最大损失验证
        max_loss = config.get('stop_market', {}).get('max_loss_percent', 0)
        if max_loss < Decimal('0.1'):
            errors.append("最大损失比例不能小于 0.1%")
        if max_loss > 80:
            errors.append("最大损失比例不能大于 80%（建议 20-40%）")
        
        # 4. 杠杆验证
        leverage = config.get('leverage', {})
        api_lev = leverage.get('api', 0)
        actual_lev = leverage.get('actual', 0)
        
        if api_lev < 1 or api_lev > 125:
            errors.append("API 杠杆必须在 1-125 之间")
        if actual_lev < 1 or actual_lev > 125:
            errors.append("实际杠杆必须在 1-125 之间")
        # 移除"实际杠杆不能大于 API 杠杆"错误，因为 save_config 会自动修正
        
        # 5. 订单超时验证
        timeout = config.get('order_timeout_seconds', 0)
        if timeout < 0.5:
            errors.append("订单超时不能小于 0.5 秒")
        if timeout > 30:
            errors.append("订单超时不能大于 30 秒")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_take_profit(entry_price: Decimal, tp_price: Decimal, side: str) -> Tuple[bool, str]:
        """
        验证止盈价合理性
        """
        if side == 'LONG':
            if tp_price <= entry_price:
                return False, "多单止盈价必须高于开仓价"
            diff = tp_price - entry_price
            if diff < Decimal('0.01'):
                return False, "止盈点数过小（至少 0.01 点）"
        else:
            if tp_price >= entry_price:
                return False, "空单止盈价必须低于开仓价"
            diff = entry_price - tp_price
            if diff < Decimal('0.01'):
                return False, "止盈点数过小（至少 0.01 点）"
        
        return True, "✓"
    
    @staticmethod
    def validate_stop_loss(entry_price: Decimal, sl_trigger: Decimal, side: str) -> Tuple[bool, str]:
        """
        验证止损触发价合理性
        """
        if side == 'LONG':
            if sl_trigger >= entry_price:
                return False, "多单止损触发价必须低于开仓价"
            diff = entry_price - sl_trigger
            if diff < Decimal('0.01'):
                return False, "止损点数过小（至少 0.01 点）"
        else:
            if sl_trigger <= entry_price:
                return False, "空单止损触发价必须高于开仓价"
            diff = sl_trigger - entry_price
            if diff < Decimal('0.01'):
                return False, "止损点数过小（至少 0.01 点）"
        
        return True, "✓"
    
    @staticmethod
    def validate_profit_loss_ratio(tp_price: Decimal, sl_trigger: Decimal, side: str) -> Tuple[bool, str, Decimal]:
        """
        验证盈亏比
        返回：(是否合理，消息，盈亏比)
        """
        if side == 'LONG':
            profit = tp_price - sl_trigger
            loss = sl_trigger - tp_price  # 负值
        else:
            profit = sl_trigger - tp_price
            loss = tp_price - sl_trigger  # 负值
        
        # 简单计算：止盈幅度 / 止损幅度
        entry = (tp_price + sl_trigger) / 2  # 近似开仓价
        profit_pct = abs(tp_price - sl_trigger) / entry
        loss_pct = abs(sl_trigger - tp_price) / entry
        
        if loss_pct == 0:
            return False, "止损幅度为 0", Decimal('0')
        
        ratio = profit_pct / loss_pct if loss_pct > 0 else Decimal('0')
        
        if ratio < Decimal('1.5'):
            return False, f"盈亏比 {ratio:.2f}:1 过低（建议至少 1.5:1）", ratio
        
        return True, f"盈亏比 {ratio:.2f}:1 ✓", ratio
