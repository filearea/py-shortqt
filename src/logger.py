# -*- coding: utf-8 -*-
"""
日志系统 - 记录市场数据、交易动作、信号特征
"""

import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal
import time


class TradeLogger:
    """交易日志系统"""
    
    def __init__(self, log_dir: Path):
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = log_dir / self.run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 日志文件
        self.market_log = open(self.log_dir / "market_data.log", "w", encoding='utf-8')
        self.trade_log = open(self.log_dir / "trade_actions.log", "w", encoding='utf-8')
        self.signals_file = open(self.log_dir / "signals.csv", "w", encoding='utf-8')
        self.snapshots_file = open(self.log_dir / "snapshots.csv", "w", encoding='utf-8')
        
        # CSV 头
        self.signals_file.write("timestamp,side,entry_price,price_5s_change,price_10s_change,price_30s_change,orderbook_imbalance,spread,bid_depth_3,ask_depth_3,bid_depth_10,ask_depth_10,result,pnl,duration_sec\n")
        self.snapshots_file.write("timestamp,price,bid1,bid1_qty,ask1,ask1_qty,spread,imbalance,bid_depth_3,ask_depth_3\n")
        
        # 价格历史（用于计算变化率）
        self.price_history = []
        
        print(f"✓ 日志目录：{self.log_dir}")
    
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
    
    def calc_depth(self, orderbook: dict, levels: int) -> tuple:
        """计算前 N 档买卖盘深度"""
        bids = orderbook.get('bids', [])[:levels]
        asks = orderbook.get('asks', [])[:levels]
        bid_depth = sum(q for _, q in bids)
        ask_depth = sum(q for _, q in asks)
        return bid_depth, ask_depth
    
    def record_signal(self, side: str, entry_price: Decimal, orderbook: dict):
        """记录开仓信号特征"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        price_5s = self.calc_price_change(5)
        price_10s = self.calc_price_change(10)
        price_30s = self.calc_price_change(30)
        imbalance = self.calc_orderbook_imbalance(orderbook)
        bid1 = orderbook['bids'][0][0] if orderbook.get('bids') else entry_price
        ask1 = orderbook['asks'][0][0] if orderbook.get('asks') else entry_price
        spread = float(ask1 - bid1)
        bid_depth_3, ask_depth_3 = self.calc_depth(orderbook, 3)
        bid_depth_10, ask_depth_10 = self.calc_depth(orderbook, 10)
        
        line = f"{ts},{side},{float(entry_price):.2f},{price_5s:.4f},{price_10s:.4f},{price_30s:.4f},{imbalance:.4f},{spread:.4f},{bid_depth_3:.3f},{ask_depth_3:.3f},{bid_depth_10:.3f},{ask_depth_10:.3f},,,,,\n"
        self.signals_file.write(line)
        self.signals_file.flush()
    
    def update_signal_result(self, result: str, pnl: float, duration: float):
        """更新信号结果（平仓时调用）"""
        self.signals_file.seek(0)
        lines = self.signals_file.readlines()
        if len(lines) > 1:
            last_line = lines[-1].strip()
            parts = last_line.split(',')
            if len(parts) >= 12:
                parts[12] = result
                parts[13] = f"{pnl:.2f}"
                parts[14] = f"{duration:.1f}"
                lines[-1] = ','.join(parts) + '\n'
                self.signals_file.close()
                self.signals_file = open(self.signals_file.name, 'w', encoding='utf-8')
                self.signals_file.writelines(lines)
                self.signals_file.flush()
    
    def record_snapshot(self, price: Decimal, orderbook: dict):
        """记录市场快照（每秒）"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        bid1 = orderbook['bids'][0][0] if orderbook.get('bids') else Decimal(0)
        bid1_qty = orderbook['bids'][0][1] if orderbook.get('bids') else Decimal(0)
        ask1 = orderbook['asks'][0][0] if orderbook.get('asks') else Decimal(0)
        ask1_qty = orderbook['asks'][0][1] if orderbook.get('asks') else Decimal(0)
        spread = float(ask1 - bid1)
        imbalance = self.calc_orderbook_imbalance(orderbook)
        bid_depth_3, ask_depth_3 = self.calc_depth(orderbook, 3)
        
        line = f"{ts},{float(price):.2f},{float(bid1):.2f},{float(bid1_qty):.3f},{float(ask1):.2f},{float(ask1_qty):.3f},{spread:.4f},{imbalance:.4f},{bid_depth_3:.3f},{ask_depth_3:.3f}\n"
        self.snapshots_file.write(line)
        self.snapshots_file.flush()
    
    def log_action(self, action_type: str, details: dict):
        """记录交易动作"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        actions = {
            "ORDER_PLACED": f"[挂单] 方向={details.get('side')} 价格={details.get('price'):.2f} 数量={details.get('size'):.3f} ETH",
            "ORDER_FILLED": f"[成交] 挂单成交 价格={details.get('price'):.2f} 数量={details.get('size'):.3f} ETH",
            "TP_FILLED": f"[止盈] 价格={details.get('price'):.2f} 数量={details.get('size'):.3f} ETH PnL={details.get('pnl'):+.2f} USDT",
            "SL_FILLED": f"[止损] 价格={details.get('price'):.2f} 数量={details.get('size'):.3f} ETH PnL={details.get('pnl'):+.2f} USDT",
            "EARLY_FILLED": f"[提前平仓] 价格={details.get('price'):.2f} 数量={details.get('size'):.3f} ETH PnL={details.get('pnl'):+.2f} USDT",
            "BALANCE_CHANGE": f"[余额] 变化={details.get('change'):+.2f} USDT 余额={details.get('balance'):.2f} USDT",
            "ORDER_CANCELLED": f"[撤单] 价格={details.get('price'):.2f}",
            "REJECTED": f"[拒绝] 原因={details.get('reason')}",
        }
        
        msg = actions.get(action_type, f"[{action_type}] {details}")
        self.trade_log.write(f"{ts} {msg}\n")
        self.trade_log.flush()
    
    def log_market_data(self, price: Decimal, orderbook: dict):
        """记录市场数据"""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        bid1 = orderbook['bids'][0] if orderbook.get('bids') else (None, None)
        ask1 = orderbook['asks'][0] if orderbook.get('asks') else (None, None)
        
        bid1_price = f"{bid1[0]:.2f}" if bid1 and bid1[0] else "N/A"
        bid1_qty = f"{bid1[1]:.3f}" if bid1 and bid1[1] else "N/A"
        ask1_price = f"{ask1[0]:.2f}" if ask1 and ask1[0] else "N/A"
        ask1_qty = f"{ask1[1]:.3f}" if ask1 and ask1[1] else "N/A"
        
        line = f"{ts} | {price:>10.2f} | {bid1_price:>10} | {bid1_qty:>10} | {ask1_price:>10} | {ask1_qty:>10}\n"
        self.market_log.write(line)
        self.market_log.flush()
    
    def close(self):
        """关闭日志文件"""
        self.market_log.close()
        self.trade_log.close()
        self.signals_file.close()
        self.snapshots_file.close()
        
        signals_count = len(open(self.log_dir / 'signals.csv').readlines()) - 1
        snapshots_count = len(open(self.log_dir / 'snapshots.csv').readlines()) - 1
        
        print(f"✓ 日志已保存：{self.log_dir}")
        print(f"✓ 信号记录：{signals_count} 条")
        print(f"✓ 市场快照：{snapshots_count} 条")
