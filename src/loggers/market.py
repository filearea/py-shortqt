# -*- coding: utf-8 -*-
"""
市场指标日志模块

记录盘面技术指标快照到日志文件。
"""

import json
from pathlib import Path
from datetime import datetime
from decimal import Decimal


class MarketLogger:
    """市场指标日志记录器"""
    
    def __init__(self, log_dir: Path, debug_mode: bool = False):
        """
        初始化市场日志记录器
        
        Args:
            log_dir: 日志目录路径
            debug_mode: 调试模式（暂不使用）
        """
        self.log_dir = log_dir
        self.log_file = self.log_dir / "market.log"
        
        # 确保日志目录存在
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 打开日志文件
        self.file = open(self.log_file, 'w', encoding='utf-8')
    
    def log_snapshot(self, symbol: str, price: Decimal, 
                     volatility: dict, liquidity: dict, 
                     score: dict, alerts: list = None):
        """
        记录指标快照
        
        Args:
            symbol: 交易对
            price: 当前价格
            volatility: 波动率指标
            liquidity: 流动性指标
            score: 综合评分
            alerts: 告警列表
        """
        timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        
        # 构建日志条目
        log_entry = {
            'timestamp': timestamp,
            'type': 'market_snapshot',
            'symbol': symbol,
            'price': float(price),
            'volatility': {
                '1min_amplitude': volatility.get('1min_amplitude', 0),
                '5min_amplitude': volatility.get('5min_amplitude', 0),
                '1h_avg_amplitude': volatility.get('1h_avg_amplitude', 0),
                'change_rate': volatility.get('change_rate', 0),
                'atr_14': volatility.get('atr_14'),
                '1min_status': volatility.get('1min_status', ''),
                '1h_status': volatility.get('1h_status', ''),
                'change_rate_status': volatility.get('change_rate_status', '')
            },
            'liquidity': {
                'spread': float(liquidity.get('spread', 0)),
                'spread_rate': liquidity.get('spread_rate', 0),
                'orderbook_depth': liquidity.get('orderbook_depth', 0),
                'spread_status': liquidity.get('spread_status', ''),
                'depth_status': liquidity.get('depth_status', '')
            },
            'score': {
                'quality_score': score.get('quality_score', 0),
                'recommendation': score.get('recommendation', ''),
                'signal_color': score.get('signal_color', ''),
                'signal_emoji': score.get('signal_emoji', '')
            },
            'alerts': alerts or []
        }
        
        # 写入日志
        # v1.5.0 修复：添加 default=str 处理 Decimal 类型
        self.file.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")
        self.file.flush()
    
    def log_alert(self, alert_type: str, message: str, metrics: dict = None):
        """
        记录告警
        
        Args:
            alert_type: 告警类型
            message: 告警消息
            metrics: 相关指标
        """
        timestamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        
        log_entry = {
            'timestamp': timestamp,
            'type': 'alert',
            'alert_type': alert_type,
            'message': message,
            'metrics': metrics or {}
        }
        
        # v1.5.0 修复：添加 default=str 处理 Decimal 类型
        self.file.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")
        self.file.flush()
    
    def close(self):
        """关闭日志文件"""
        if self.file:
            self.file.close()
