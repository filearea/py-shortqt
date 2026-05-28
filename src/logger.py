# -*- coding: utf-8 -*-
"""
日志系统 - 记录市场数据、交易动作、信号特征
日志位置：项目根目录/logs/
"""

import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal
import time
import json


def convert_decimal(obj):
    """将 Decimal 转换为 float，用于 JSON 序列化"""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: convert_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimal(v) for v in obj]
    return obj


def safe_float(val, default=0.0):
    """安全转换为 float"""
    if val is None:
        return default
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


class TradeLogger:
    """交易日志系统 - 记录交易数据"""
    
    def __init__(self, log_dir: Path = None):
        # 使用项目根目录的 logs 文件夹
        if log_dir is None:
            project_root = Path(__file__).parent.parent
            log_dir = project_root / "logs"
        
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = log_dir / self.run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 交易日志文件
        self.trade_log = open(self.log_dir / "trades.log", "w", encoding='utf-8')
        self.orders_log = open(self.log_dir / "orders.log", "w", encoding='utf-8')
        self.positions_log = open(self.log_dir / "positions.log", "w", encoding='utf-8')
        self.pnl_log = open(self.log_dir / "pnl.log", "w", encoding='utf-8')
        
        # 信号特征 CSV
        self.signals_file = open(self.log_dir / "signals.csv", "w", encoding='utf-8')
        self.signals_file.write("timestamp,side,entry_price,price_5s_change,price_10s_change,price_30s_change,orderbook_imbalance,spread,bid_depth_3,ask_depth_3,result,pnl,duration_sec\n")
        
        # 价格历史（用于计算变化率）
        self.price_history = []
        self.current_signal = None
    
    def record_price(self, price: Decimal):
        """记录价格历史"""
        now = time.time()
        self.price_history.append((now, float(price)))
        cutoff = now - 60
        self.price_history = [(t, p) for t, p in self.price_history if t > cutoff]
    
    def calc_price_change(self, seconds: int) -> float:
        """计算 X 秒前到现在的价格变化率"""
        if len(self.price_history) < 2:
            return 0.0
        now = time.time()
        cutoff = now - seconds
        old_price = None
        for t, p in reversed(self.price_history):
            if t <= cutoff:
                old_price = p
                break
        if old_price is None and len(self.price_history) >= 2:
            old_price = self.price_history[0][1]
        if old_price is None or old_price == 0:
            return 0.0
        current = self.price_history[-1][1]
        return (current - old_price) / old_price * 100
    
    def calc_orderbook_imbalance(self, orderbook: dict) -> float:
        """计算订单簿不平衡度 (-1 到 1)"""
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total
    
    def record_signal(self, side: str, entry_price: Decimal, orderbook: dict):
        """记录开仓信号特征"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        price_5s = self.calc_price_change(5)
        price_10s = self.calc_price_change(10)
        price_30s = self.calc_price_change(30)
        imbalance = self.calc_orderbook_imbalance(orderbook)
        
        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])
        spread = float(asks[0][0] - bids[0][0]) if bids and asks else 0
        bid_depth_3 = sum(q for _, q in bids[:3])
        ask_depth_3 = sum(q for _, q in asks[:3])
        
        self.current_signal = {
            'timestamp': timestamp,
            'side': side,
            'entry_price': float(entry_price),
            'price_5s_change': price_5s,
            'price_10s_change': price_10s,
            'price_30s_change': price_30s,
            'orderbook_imbalance': imbalance,
            'spread': spread,
            'bid_depth_3': bid_depth_3,
            'ask_depth_3': ask_depth_3
        }
    
    def update_signal_result(self, result_type: str, pnl: float, duration_sec: float):
        """更新信号结果（止盈/止损后）"""
        if self.current_signal:
            self.current_signal['result'] = result_type
            self.current_signal['pnl'] = pnl
            self.current_signal['duration_sec'] = duration_sec
            
            # 写入 CSV
            line = f"{self.current_signal['timestamp']},{self.current_signal['side']},{self.current_signal['entry_price']:.2f}," \
                   f"{self.current_signal['price_5s_change']:.4f},{self.current_signal['price_10s_change']:.4f}," \
                   f"{self.current_signal['price_30s_change']:.4f},{self.current_signal['orderbook_imbalance']:.4f}," \
                   f"{self.current_signal['spread']:.2f},{self.current_signal['bid_depth_3']:.3f}," \
                   f"{self.current_signal['ask_depth_3']:.3f},{self.current_signal['result']},{self.current_signal['pnl']:.6f}," \
                   f"{self.current_signal['duration_sec']:.2f}\n"
            self.signals_file.write(line)
            self.signals_file.flush()
            self.current_signal = None
    
    def log_trade(self, action: str, details: dict):
        """记录交易动作"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        log_entry = {
            'timestamp': timestamp,
            'action': action,
            **convert_decimal(details)
        }
        self.trade_log.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        self.trade_log.flush()
    
    def log_order(self, order_type: str, order_data: dict):
        """记录订单信息"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = {
            'timestamp': timestamp,
            'order_type': order_type,
            **convert_decimal(order_data)
        }
        self.orders_log.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        self.orders_log.flush()
    
    def log_position(self, side: str, entry_price: Decimal, size: Decimal, current_pnl: Decimal = Decimal('0')):
        """记录持仓信息"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = {
            'timestamp': timestamp,
            'side': side,
            'entry_price': float(entry_price),
            'size': float(size),
            'current_pnl': float(current_pnl)
        }
        self.positions_log.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        self.positions_log.flush()
    
    def log_pnl(self, action: str, pnl: float, details: dict = None):
        """记录 PnL"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = {
            'timestamp': timestamp,
            'action': action,
            'pnl': pnl,
            'details': details or {}
        }
        self.pnl_log.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        self.pnl_log.flush()
    
    def log_balance(self, event: str, balance: Decimal, details: dict = None):
        """
        记录账户余额（用于复合收益率计算）
        
        Args:
            event: 事件类型 ('startup' | 'position_closed' | 'shutdown')
            balance: 账户余额 (USDC)
            details: 额外信息
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = {
            'timestamp': timestamp,
            'type': 'BALANCE',
            'event': event,
            'balance': float(balance),
            'details': details or {}
        }
        self.trade_log.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        self.trade_log.flush()  # 立即刷新到磁盘，避免程序关闭时丢失
    
    def close(self):
        """关闭所有日志文件"""
        self.trade_log.close()
        self.orders_log.close()
        self.positions_log.close()
        self.pnl_log.close()
        self.signals_file.close()
