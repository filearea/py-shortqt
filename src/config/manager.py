# -*- coding: utf-8 -*-
"""
配置管理器 - 统一管理所有配置
"""

import json
import shutil
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import Any


class ConfigManager:
    """配置管理器"""
    
    # 默认配置
    DEFAULT_CONFIG = {
        "take_profit": {
            "mode": "fixed",
            "points": 1.00,
            "percent": 0.36
        },
        "stop_loss": {
            "trigger_mode": "fixed",
            "trigger_points": 3.00,
            "trigger_percent": 0.50,
            "limit_mode": "queue",
            "limit_offset": 10.50
        },
        "stop_market": {
            "max_loss_percent": 30.00
        },
        "leverage": {
            "api": 100,
            "actual": 25
        },
        "order_timeout_seconds": 2.00
    }
    
    def __init__(self, config_path: str = "config/runtime.json"):
        self.config_path = Path(config_path)
        self.config = {}
        self.load()
    
    def load(self):
        """加载配置"""
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
        else:
            # 配置文件不存在，使用默认配置
            self.config = self.DEFAULT_CONFIG.copy()
            self.save()
    
    def save(self, auto_backup: bool = True):
        """保存配置"""
        if auto_backup and self.config_path.exists():
            # 自动备份
            self.backup_config("runtime.json.auto")
        
        # 确保目录存在
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 添加最后修改时间
        self.config['last_modified'] = datetime.now().isoformat()
        
        # 保存配置
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项（支持点号访问，如 'take_profit.mode'）"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def set(self, key: str, value: Any):
        """设置配置项（支持点号访问）"""
        keys = key.split('.')
        config = self.config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
    
    def get_config(self) -> dict:
        """获取完整配置"""
        return self.config.copy()
    
    def update_config(self, new_config: dict):
        """更新配置"""
        self.config.update(new_config)
    
    # ========== 备份/恢复/重置 ==========
    
    def backup_config(self, backup_name: str = None) -> str:
        """备份当前配置"""
        if not self.config_path.exists():
            return "配置文件不存在"
        
        if backup_name is None:
            backup_name = f"runtime.json.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        backup_path = self.config_path.parent / backup_name
        shutil.copy(self.config_path, backup_path)
        
        return backup_path
    
    def restore_config(self, backup_name: str) -> bool:
        """从备份恢复配置"""
        backup_path = self.config_path.parent / backup_name
        if not backup_path.exists():
            return False
        
        shutil.copy(backup_path, self.config_path)
        self.load()
        return True
    
    def list_backups(self) -> list[str]:
        """列出所有备份文件"""
        backups = []
        config_dir = self.config_path.parent
        for f in config_dir.glob("runtime.json.*"):
            if f.name != 'runtime.json.auto':
                backups.append(f.name)
        return sorted(backups, reverse=True)
    
    def delete_backup(self, backup_name: str) -> bool:
        """删除备份文件"""
        backup_path = self.config_path.parent / backup_name
        if backup_path.exists():
            backup_path.unlink()
            return True
        return False
    
    def reset_to_defaults(self):
        """重置为默认配置"""
        self.config = self.DEFAULT_CONFIG.copy()
        self.save(auto_backup=True)
    
    # ========== 便捷访问方法 ==========
    
    def get_take_profit_price(self, entry_price: Decimal) -> Decimal:
        """计算止盈价"""
        tp = self.config.get('take_profit', {})
        mode = tp.get('mode', 'fixed')
        
        if mode == 'fixed':
            points = Decimal(str(tp.get('points', 1.00)))
            return entry_price + points
        else:
            percent = Decimal(str(tp.get('percent', 0.36)))
            return entry_price * (Decimal('1') + percent / Decimal('100'))
    
    def get_stop_loss_params(self, symbol: str, entry_price: Decimal, side: str, size: Decimal) -> tuple[Decimal, dict]:
        """
        计算止损单参数
        返回：(触发价，Algo API 参数)
        """
        sl = self.config.get('stop_loss', {})
        
        # 1. 计算触发价
        trigger_mode = sl.get('trigger_mode', 'fixed')
        if trigger_mode == 'fixed':
            points = Decimal(str(sl.get('trigger_points', 3.00)))
            if side == 'LONG':
                trigger_price = entry_price - points
            else:
                trigger_price = entry_price + points
        else:
            percent = Decimal(str(sl.get('trigger_percent', 0.50))) / Decimal('100')
            if side == 'LONG':
                trigger_price = entry_price * (Decimal('1') - percent)
            else:
                trigger_price = entry_price * (Decimal('1') + percent)
        
        # 2. 构建 Algo API 参数
        algo_params = {
            'symbol': symbol,  # 交易对
            'side': 'SELL' if side == 'LONG' else 'BUY',  # 止损方向
            'type': 'STOP',  # STOP 类型
            'triggerPrice': str(trigger_price),
            'quantity': str(size),
            'workingType': 'CONTRACT_PRICE',
            'positionSide': side,
            'timeInForce': 'GTC'
        }
        
        limit_mode = sl.get('limit_mode', 'queue')
        if limit_mode == "queue":
            # 同向价 1 模式（原有功能，别改崩）
            algo_params['priceMatch'] = 'QUEUE'
            # 不传 price
        else:
            # 自定义滑点模式（新增）
            # 多单止损：卖出平仓，挂高价（触发价 + 滑点）
            # 空单止损：买入平仓，挂低价（触发价 - 滑点）
            offset = Decimal(str(sl.get('limit_offset', 10.50)))
            if side == 'LONG':
                limit_price = trigger_price + offset
            else:
                limit_price = trigger_price - offset
            algo_params['price'] = str(limit_price)
            # 不传 priceMatch
        
        return trigger_price, algo_params
    
    def get_stop_market_price(
        self,
        entry_price: Decimal,
        side: str,
        size: Decimal,
        balance_before: Decimal,
        liquidation_price: Decimal
    ) -> Decimal:
        """
        计算保底止损触发价（基于最大损失比例）
        """
        max_loss_percent = Decimal(str(self.config.get('stop_market', {}).get('max_loss_percent', 30.00)))
        
        # 1. 名义仓位价值
        notional = entry_price * size
        
        # 2. 手续费（Taker 0.05%）
        fee = notional * Decimal('0.0005')
        
        # 3. 最大损失（USDT）
        max_loss_usd = balance_before * (max_loss_percent / Decimal('100'))
        
        # 4. 实际可承受价格损失
        price_loss_usd = max_loss_usd - fee
        
        # 5. 损失价差
        price_diff = price_loss_usd / size
        
        # 6. 止损价
        if side == 'LONG':
            stop_price = entry_price - price_diff
            # 和强平价 +1 比较，取更高的（更安全）
            liquidation_stop = liquidation_price + Decimal('1')
            stop_price = max(stop_price, liquidation_stop)
        else:
            stop_price = entry_price + price_diff
            # 和强平价 -1 比较，取更低的（更安全）
            liquidation_stop = liquidation_price - Decimal('1')
            stop_price = min(stop_price, liquidation_stop)
        
        return stop_price
    
    def get_leverage_config(self) -> tuple[int, int]:
        """获取杠杆配置 (API 杠杆，实际杠杆)"""
        lev = self.config.get('leverage', {})
        return lev.get('api', 100), lev.get('actual', 25)
    
    def get_order_timeout(self) -> float:
        """获取订单超时时间"""
        return self.config.get('order_timeout_seconds', 2.00)
