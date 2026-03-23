# -*- coding: utf-8 -*-
"""
市场日志 - 记录盘面数据、WebSocket 消息、订单簿深度
JSONL 格式，便于后续分析
"""

import json
import threading
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Any, Optional


class MarketLogger:
    """市场数据日志记录器"""
    
    def __init__(self, log_dir: Path, debug_mode: bool = False):
        self.log_dir = log_dir
        self.debug_mode = debug_mode
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        
        # 日志文件
        self.market_file = open(
            self.log_dir / f"market_{self.current_date}.jsonl",
            'a',
            encoding='utf-8'
        )
        
        # 锁（多线程安全）
        self._lock = threading.Lock()
        
        # 缓存（用于批量写入）
        self._buffer: List[str] = []
        self._buffer_size = 0
        self._max_buffer_size = 100  # 缓存 100 条后批量写入
    
    def _write_line(self, data: Dict[str, Any]):
        """写入一行日志（线程安全）"""
        with self._lock:
            line = json.dumps(data, ensure_ascii=False)
            self.market_file.write(line + '\n')
            self._buffer_size += 1
            
            # 定期 flush
            if self._buffer_size >= self._max_buffer_size:
                self.market_file.flush()
                self._buffer_size = 0
    
    def debug(self, msg: str):
        """DEBUG 日志（仅调试模式）"""
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
    
    def log_orderbook(self, symbol: str, bids: List, asks: List, sequence: int = None):
        """记录订单簿快照"""
        if not self.debug_mode:
            return  # 仅在调试模式记录完整订单簿
        
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'BOOK',
            'symbol': symbol,
            'sequence': sequence,
            'bids': [[float(p), float(q)] for p, q in bids[:10]],  # 前 10 档
            'asks': [[float(p), float(q)] for p, q in asks[:10]]
        }
        self._write_line(data)
    
    def log_trade(self, symbol: str, price: float, qty: float, side: str, trade_id: str = None):
        """记录成交"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'TRADE',
            'symbol': symbol,
            'price': price,
            'qty': qty,
            'side': side,
            'trade_id': trade_id
        }
        self._write_line(data)
    
    def log_signal(self, side: str, price: float, features: Dict[str, Any]):
        """记录信号触发时的市场状态"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'SIGNAL',
            'side': side,
            'price': price,
            'features': features
        }
        self._write_line(data)
    
    def log_amplitude(self, symbol: str, window: str, high: float, low: float, 
                      amplitude: float, start_price: float, end_price: float):
        """记录振幅异动"""
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'AMPLITUDE',
            'symbol': symbol,
            'window': window,
            'high': high,
            'low': low,
            'amplitude': amplitude,
            'start_price': start_price,
            'end_price': end_price
        }
        self._write_line(data)
    
    def log_price_update(self, symbol: str, price: float, change_pct: float = None):
        """记录价格更新"""
        if not self.debug_mode:
            return
        
        data = {
            'ts': datetime.now().isoformat(),
            'type': 'PRICE',
            'symbol': symbol,
            'price': price,
            'change_pct': change_pct
        }
        self._write_line(data)
    
    def log_ws_message(self, direction: str, msg_type: str, data: Any):
        """记录 WebSocket 原始消息"""
        if not self.debug_mode:
            return
        
        # 简化大数据
        if isinstance(data, dict) and 'bids' in data:
            data = {'type': 'orderbook', 'count': len(data.get('bids', []))}
        
        log_data = {
            'ts': datetime.now().isoformat(),
            'type': 'WS',
            'direction': direction,  # 'TX' or 'RX'
            'msg_type': msg_type,
            'data': data
        }
        self._write_line(log_data)
    
    def close(self):
        """关闭文件"""
        with self._lock:
            if self._buffer_size > 0:
                self.market_file.flush()
            self.market_file.close()
