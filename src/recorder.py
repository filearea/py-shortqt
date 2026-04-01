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
        self._klines_cache.append(kline)
        if len(self._klines_cache) >= 100:
            self._flush_klines()
    
    def save_orderbook(self, bids: List, asks: List):
        current_time = time.time()
        if current_time - self._last_orderbook_save < self.orderbook_interval:
            return
        snapshot = {'timestamp': datetime.now().isoformat(), 'symbol': self.symbol, 'bids': [[str(p), str(q)] for p, q in bids], 'asks': [[str(p), str(q)] for p, q in asks]}
        self._orderbooks_cache.append(snapshot)
        self._last_orderbook_save = current_time
        self._orderbooks_saved += 1
        if len(self._orderbooks_cache) >= 10:
            self._flush_orderbooks()
    
    def _flush_klines(self):
        if not self._klines_cache:
            return
        self.klines_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.klines_file, 'a', encoding='utf-8') as f:
            for kline in self._klines_cache:
                f.write(json.dumps(kline, ensure_ascii=False) + '\n')
        self._klines_saved += len(self._klines_cache)
        self._klines_cache = []
    
    def _flush_orderbooks(self):
        if not self._orderbooks_cache:
            return
        self.orderbook_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.orderbook_file, 'a', encoding='utf-8') as f:
            for snapshot in self._orderbooks_cache:
                f.write(json.dumps(snapshot, ensure_ascii=False) + '\n')
        self._orderbooks_cache = []
    
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
