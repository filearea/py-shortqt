# -*- coding: utf-8 -*-
"""实时数据记录器 - v1.4.1"""
import json
import time
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from typing import List, Dict, Any

DATA_DIR = Path(__file__).parent.parent / "data"
KLINES_DIR = DATA_DIR / "klines"
ORDERBOOK_DIR = DATA_DIR / "orderbook"

class RealtimeRecorder:
    def __init__(self, symbol: str = "ETHUSDC", orderbook_interval: int = 60):
        self.symbol = symbol
        self.orderbook_interval = orderbook_interval
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        KLINES_DIR.mkdir(parents=True, exist_ok=True)
        ORDERBOOK_DIR.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.klines_file = KLINES_DIR / symbol / f"{self.today}.jsonl"
        self.orderbook_file = ORDERBOOK_DIR / symbol / f"{self.today}.jsonl"
        self._klines_cache: List[Dict] = []
        self._orderbooks_cache: List[Dict] = []
        self._last_orderbook_save = 0.0
        self._klines_saved = 0
        self._orderbooks_saved = 0
    
    def save_kline(self, kline: Dict[str, Any]):
        """保存 K 线数据（v1.5.3 修复：只保存已关闭的 K 线，避免重复和中间态数据）"""
        is_closed = kline.get('is_closed', False)
        
        if not is_closed:
            # 未关闭的 K 线不写入文件，只更新内存（供指标计算用）
            return
        
        # 已关闭的 K 线，Decimal 转 float 后写入
        kline_clean = {
            'timestamp': kline.get('timestamp'),
            'open': float(kline.get('open', 0)),
            'high': float(kline.get('high', 0)),
            'low': float(kline.get('low', 0)),
            'close': float(kline.get('close', 0)),
            'volume': float(kline.get('volume', 0)),
            'is_closed': True
        }
        
        # 立即写入文件
        self.klines_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.klines_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(kline_clean, ensure_ascii=False) + '\n')
        self._klines_saved += 1
    
    def save_orderbook(self, bids: List, asks: List):
        """保存订单簿快照（v1.5.0 修复：立即保存，不缓存，避免数据丢失）"""
        current_time = time.time()
        if current_time - self._last_orderbook_save < self.orderbook_interval:
            return
        
        snapshot = {
            'timestamp': datetime.now().isoformat(),
            'symbol': self.symbol,
            'bids': [[str(p), str(q)] for p, q in bids],
            'asks': [[str(p), str(q)] for p, q in asks]
        }
        
        # v1.5.0 修复：立即保存，不缓存
        self.orderbook_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.orderbook_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(snapshot, ensure_ascii=False, default=str) + '\n')
        
        self._last_orderbook_save = current_time
        self._orderbooks_saved += 1
    
    def _flush_klines(self):
        if not self._klines_cache:
            return
        self.klines_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.klines_file, 'a', encoding='utf-8') as f:
            for kline in self._klines_cache:
                # v1.5.3 修复：Decimal 转 float，避免 JSON 中变成字符串
                kline_clean = {
                    'timestamp': kline.get('timestamp'),
                    'open': float(kline.get('open', 0)),
                    'high': float(kline.get('high', 0)),
                    'low': float(kline.get('low', 0)),
                    'close': float(kline.get('close', 0)),
                    'volume': float(kline.get('volume', 0)),
                    'turnover': float(kline.get('turnover', 0)),
                    'trades': int(kline.get('trades', 0)),
                    'buy_volume': float(kline.get('buy_volume', 0)),
                    'buy_turnover': float(kline.get('buy_turnover', 0)),
                    'is_closed': kline.get('is_closed', False)
                }
                f.write(json.dumps(kline_clean, ensure_ascii=False) + '\n')
        self._klines_saved += len(self._klines_cache)
        self._klines_cache = []
    
    def _flush_orderbooks(self):
        """清空订单簿缓存（v1.5.0 后不再使用，改为立即保存）"""
        # v1.5.0 修复：不再缓存，直接保存
        pass
    
    def flush_all(self):
        self._flush_klines()
        self._flush_orderbooks()
    
    def get_stats(self) -> Dict[str, int]:
        return {'klines_saved': self._klines_saved + len(self._klines_cache), 'orderbooks_saved': self._orderbooks_saved + len(self._orderbooks_cache)}
    
    def __del__(self):
        try:
            self.flush_all()
        except:
            pass
