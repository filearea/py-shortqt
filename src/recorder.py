# -*- coding: utf-8 -*-
"""
实时数据记录器 - v1.5.5
K 线改为定时 API 拉取（每分钟 1 次），数据更完整
v1.5.5: 修复 K 线缺口 + 订单簿日期切分
"""
import json
import time
import asyncio
import threading
from datetime import datetime
from pathlib import Path
from decimal import Decimal
from typing import List, Dict, Any, Optional

DATA_DIR = Path(__file__).parent.parent / "data"
KLINES_DIR = DATA_DIR / "klines"
ORDERBOOK_DIR = DATA_DIR / "orderbook"


class RealtimeRecorder:
    def __init__(self, symbol: str = "ETHUSDC", orderbook_interval: int = 60, api_client=None):
        self.symbol = symbol
        self.orderbook_interval = orderbook_interval
        self.api_client = api_client  # v1.5.3: 用于 API 拉取 K 线
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        KLINES_DIR.mkdir(parents=True, exist_ok=True)
        ORDERBOOK_DIR.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.klines_file = KLINES_DIR / symbol / f"{self.today}.jsonl"
        self.orderbook_file = ORDERBOOK_DIR / symbol / f"{self.today}.jsonl"
        self._last_orderbook_save = 0.0
        self._klines_saved = 0
        self._orderbooks_saved = 0
        self._last_kline_ts = 0  # 上次保存的 K 线时间戳，防重复
        self._kline_timer_running = False

    def start_kline_timer(self):
        """启动 K 线定时拉取（每 60 秒从 API 拉取最近已关闭的 K 线）"""
        if not self.api_client:
            return
        self._kline_timer_running = True
        thread = threading.Thread(target=self._kline_poll_loop, daemon=True)
        thread.start()

    def stop_kline_timer(self):
        """停止 K 线定时拉取"""
        self._kline_timer_running = False

    def _kline_poll_loop(self):
        """K 线轮询线程：对齐到每分钟第 2 秒拉取"""
        # 启动后等到下一分钟的第 2 秒
        now = time.time()
        seconds_in_minute = now % 60
        if seconds_in_minute < 2:
            time.sleep(2 - seconds_in_minute)
        else:
            time.sleep(62 - seconds_in_minute)
        
        while self._kline_timer_running:
            try:
                self._fetch_and_save_kline()
            except Exception:
                pass
            # 精确对齐到下一分钟的第 2 秒
            now = time.time()
            seconds_in_minute = now % 60
            if seconds_in_minute < 2:
                wait = 2 - seconds_in_minute
            else:
                wait = 62 - seconds_in_minute
            # 分段 sleep，每秒检查一次退出标志
            end_time = now + wait
            while self._kline_timer_running and time.time() < end_time:
                remaining = end_time - time.time()
                time.sleep(min(remaining, 1.0))

    def _fetch_and_save_kline(self):
        """从 API 拉取最近 2 条 K 线，保存已关闭的那条"""
        if not self.api_client:
            return
        
        # 拉取最近 2 条（最后一条可能未关闭）
        klines = self.api_client.get_klines(self.symbol, '1m', limit=2)
        if not klines or len(klines) < 2:
            return
        
        # 取倒数第 2 条（已关闭的）
        k = klines[-2]
        ts = k[0]  # 开盘时间戳
        
        # 防重复
        if ts <= self._last_kline_ts:
            return
        
        # 检查日期是否变化，切换文件
        date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        if date_str != self.today:
            self.today = date_str
            self.klines_file = KLINES_DIR / self.symbol / f"{self.today}.jsonl"
        
        # 检查文件最后一条的时间戳（双重防重）
        if self.klines_file.exists():
            try:
                with open(self.klines_file, 'r', encoding='utf-8') as f:
                    last_line = None
                    for line in f:
                        if line.strip():
                            last_line = line
                    if last_line:
                        last_data = json.loads(last_line)
                        if last_data.get('timestamp', 0) >= ts:
                            return  # 已存在，跳过
            except Exception:
                pass  # 读取失败，继续写入
        
        kline_data = {
            'timestamp': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
            'turnover': float(k[7]),
            'trades': int(k[8]),
            'buy_volume': float(k[9]),
            'buy_turnover': float(k[10])
        }
        
        self.klines_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.klines_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(kline_data, ensure_ascii=False) + '\n')
        
        self._last_kline_ts = ts
        self._klines_saved += 1

    def save_kline(self, kline: Dict[str, Any]):
        """WebSocket K 线回调 — v1.5.3 不再写文件，仅供指标计算"""
        # K 线数据写入已改为定时 API 拉取，此方法保留接口兼容
        pass

    def _check_date_rollover(self):
        """检查日期是否变化，切换订单簿文件"""
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self.today:
            self.today = today
            self.klines_file = KLINES_DIR / self.symbol / f"{self.today}.jsonl"
            self.orderbook_file = ORDERBOOK_DIR / self.symbol / f"{self.today}.jsonl"

    def save_orderbook(self, bids: List, asks: List):
        """保存订单簿快照"""
        current_time = time.time()
        if current_time - self._last_orderbook_save < self.orderbook_interval:
            return
        
        # v1.5.5: 检查日期切换
        self._check_date_rollover()
        
        snapshot = {
            'timestamp': datetime.now().isoformat(),
            'symbol': self.symbol,
            'bids': [[str(p), str(q)] for p, q in bids],
            'asks': [[str(p), str(q)] for p, q in asks]
        }
        
        self.orderbook_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.orderbook_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(snapshot, ensure_ascii=False, default=str) + '\n')
        
        self._last_orderbook_save = current_time
        self._orderbooks_saved += 1

    def flush_all(self):
        """兼容旧接口"""
        pass

    def get_stats(self) -> Dict[str, int]:
        return {
            'klines_saved': self._klines_saved,
            'orderbooks_saved': self._orderbooks_saved
        }

    def __del__(self):
        self._kline_timer_running = False
