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
        """保存 K 线数据（v1.5.2 修复：已关闭 K 线立即写入，未关闭 K 线更新缓存）"""
        is_closed = kline.get('is_closed', False)
        timestamp = kline.get('timestamp')
        
        # 如果缓存中有未关闭的 K 线且时间戳相同，更新最后一条
        if self._klines_cache and not is_closed:
            last_kline = self._klines_cache[-1]
            if last_kline.get('timestamp') == timestamp:
                # 更新最后一条 K 线（不写入，等关闭时再写）
                self._klines_cache[-1] = kline
                return
        
        # 新 K 线或已关闭的 K 线，追加到缓存
        self._klines_cache.append(kline)
        
        # v1.5.2 修复：已关闭的 K 线立即 flush，确保数据及时写入
        if is_closed:
            self._flush_klines()
        elif len(self._klines_cache) >= 100:
            # 未关闭的 K 线攒够 100 条再写
            self._flush_klines()
    
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
                # v1.5.0 修复：添加 default=str 处理 Decimal 类型
                f.write(json.dumps(kline, ensure_ascii=False, default=str) + '\n')
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
