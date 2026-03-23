# -*- coding: utf-8 -*-
"""
交易日志 - 记录订单、持仓、信号结果
JSONL 格式，便于后续分析
"""

import json
import threading
import csv
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional


class TradingLogger:
    """交易日志记录器"""
    
    def __init__(self, log_dir: Path, debug_mode: bool = False):
        self.log_dir = log_dir
        self.debug_mode = debug_mode
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        
        # 交易日志文件（JSONL）
        self.trading_file = open(
            self.log_dir / f"trading_{self.current_date}.jsonl",
            'a',
            encoding='utf-8'
        )
        
        # 信号 CSV 文件
        signals_file = self.log_dir / f"signals_{self.current_date}.csv"
        self.signals_file = open(signals_file, 'a', encoding='utf-8', newline='')
        self.signals_writer = csv.writer(self.signals_file)
        
        # 写入 CSV 表头（如果是新文件）
        if signals_file.stat().st_size == 0:
            self.signals_writer.writerow([
                'timestamp', 'side', 'entry_price', 'price_5s_change', 
                'price_10s_change', 'price_30s_change', 'orderbook_imbalance',
                'spread', 'bid_depth_3', 'ask_depth_3', 'result', 'pnl', 'duration_sec'
            ])
        
        # 锁
        self._lock = threading.Lock()
        
        # 当前信号
        self._current_signal: Optional[Dict] = None
    
    def debug(self, msg: str):
        """DEBUG 日志"""
        if self.debug_mode:
            self._write_line({
                'ts': datetime.now().isoformat(),
                'type': 'DEBUG',
                'message': msg
            })
    
    def info(self, msg: str):
        """INFO 日志"""
        self._write_line({
            'ts': datetime.now().isoformat(),
            'type': 'INFO',
            'message': msg
        })
    
    def warning(self, msg: str):
        """WARNING 日志"""
        self._write_line({
            'ts': datetime.now().isoformat(),
            'type': 'WARNING',
            'message': msg
        })
    
    def error(self, msg: str):
        """ERROR 日志"""
        self._write_line({
            'ts': datetime.now().isoformat(),
            'type': 'ERROR',
            'message': msg
        })
    
    def _write_line(self, data: Dict[str, Any]):
        """写入一行日志"""
        with self._lock:
            line = json.dumps(data, ensure_ascii=False)
            self.trading_file.write(line + '\n')
            self.trading_file.flush()
    
    def log_order_new(self, order_id: str, side: str, order_type: str, 
                      price: float, qty: float, position_side: str = None):
        """记录新订单"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'ORDER_NEW',
            'order_id': order_id,
            'side': side,
            'order_type': order_type,
            'price': price,
            'qty': qty,
            'position_side': position_side
        }
        self._write_line(data)
    
    def log_order_filled(self, order_id: str, avg_price: float, filled_qty: float,
                         commission: float, commission_asset: str, pnl: float = None):
        """记录订单成交"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'ORDER_FILLED',
            'order_id': order_id,
            'avg_price': avg_price,
            'filled_qty': filled_qty,
            'commission': commission,
            'commission_asset': commission_asset,
            'pnl': pnl
        }
        self._write_line(data)
    
    def log_order_canceled(self, order_id: str, reason: str = None):
        """记录订单取消"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'ORDER_CANCELED',
            'order_id': order_id,
            'reason': reason
        }
        self._write_line(data)
    
    def log_position_open(self, side: str, entry_price: float, size: float,
                          leverage: int = None, margin: float = None):
        """记录开仓"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'POSITION_OPEN',
            'side': side,
            'entry_price': entry_price,
            'size': size,
            'leverage': leverage,
            'margin': margin
        }
        self._write_line(data)
    
    def log_position_close(self, side: str, exit_price: float, size: float,
                           pnl: float, pnl_pct: float, reason: str,
                           entry_price: float = None, duration_sec: float = None):
        """记录平仓"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'POSITION_CLOSE',
            'side': side,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'size': size,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason,  # 'TP', 'SL', 'MANUAL', 'STOP_MARKET'
            'duration_sec': duration_sec
        }
        self._write_line(data)
    
    def log_position_update(self, side: str, entry_price: float, size: float,
                            unrealized_pnl: float, current_price: float):
        """记录持仓更新（定期快照）"""
        if not self.debug_mode:
            return
        
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'POSITION_UPDATE',
            'side': side,
            'entry_price': entry_price,
            'size': size,
            'unrealized_pnl': unrealized_pnl,
            'current_price': current_price
        }
        self._write_line(data)
    
    def log_signal_start(self, side: str, entry_price: float, features: Dict[str, Any]):
        """记录信号开始（开仓时）"""
        self._current_signal = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'side': side,
            'entry_price': entry_price,
            'features': features,
            'start_time': datetime.now()
        }
    
    def log_signal_result(self, result_type: str, pnl: float, 
                          duration_sec: float, exit_price: float = None):
        """记录信号结果（平仓时）"""
        if self._current_signal:
            # 写入 CSV
            features = self._current_signal.get('features', {})
            row = [
                self._current_signal['timestamp'],
                self._current_signal['side'],
                f"{self._current_signal['entry_price']:.2f}",
                f"{features.get('price_5s_change', 0):.4f}",
                f"{features.get('price_10s_change', 0):.4f}",
                f"{features.get('price_30s_change', 0):.4f}",
                f"{features.get('orderbook_imbalance', 0):.4f}",
                f"{features.get('spread', 0):.2f}",
                f"{features.get('bid_depth_3', 0):.3f}",
                f"{features.get('ask_depth_3', 0):.3f}",
                result_type,
                f"{pnl:.6f}",
                f"{duration_sec:.2f}"
            ]
            
            with self._lock:
                self.signals_writer.writerow(row)
                self.signals_file.flush()
            
            # 同时写入 JSONL
            data = {
                'ts': datetime.now().isoformat(),
                'type': 'SIGNAL_RESULT',
                'result': result_type,
                'pnl': pnl,
                'duration_sec': duration_sec,
                'exit_price': exit_price,
                'features': features
            }
            self._write_line(data)
            
            self._current_signal = None
    
    def log_balance_update(self, available: float, position_margin: float,
                           order_margin: float, total: float):
        """记录账户余额更新"""
        if not self.debug_mode:
            return
        
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'BALANCE',
            'available': available,
            'position_margin': position_margin,
            'order_margin': order_margin,
            'total': total
        }
        self._write_line(data)
    
    def close(self):
        """关闭文件"""
        with self._lock:
            self.trading_file.close()
            self.signals_file.close()
