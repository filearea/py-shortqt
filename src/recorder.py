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
    def __init__(self, symbol: str = "ETHUSDC", orderbook_interval: int = 60, api_client=None, log_func=None):
        self.symbol = symbol
        self.orderbook_interval = orderbook_interval
        self.api_client = api_client  # v1.5.3: 用于 API 拉取 K 线
        self._log = log_func or (lambda msg: None)
        self.on_new_kline = None  # 回调函数，接收 kline dict
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
        self._recent_cache: Dict[int, dict] = {}  # ts → kline_dict，最近 ~20 条，用于运行时值比较修正

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
        """K 线轮询线程：对齐到每分钟第 10 秒拉取（给 API 充足时间最终确定数据）"""
        # 启动后等到下一分钟的第 10 秒
        now = time.time()
        seconds_in_minute = now % 60
        if seconds_in_minute < 10:
            time.sleep(10 - seconds_in_minute)
        else:
            time.sleep(70 - seconds_in_minute)

        while self._kline_timer_running:
            try:
                self._fetch_and_save_kline()
            except Exception:
                pass
            # 精确对齐到下一分钟的第 10 秒
            now = time.time()
            seconds_in_minute = now % 60
            if seconds_in_minute < 10:
                wait = 10 - seconds_in_minute
            else:
                wait = 70 - seconds_in_minute
            # 分段 sleep，每秒检查一次退出标志
            end_time = now + wait
            while self._kline_timer_running and time.time() < end_time:
                remaining = end_time - time.time()
                time.sleep(min(remaining, 1.0))

    def _is_kline_finalized(self, kline_ts: int) -> bool:
        """K 线是否已经超过 10 秒，数据应该已最终确定"""
        kline_close_time = kline_ts + 60000
        now_ms = int(time.time() * 1000)
        return (now_ms - kline_close_time) > 10000

    def _kline_ohlcv_changed(self, cached: dict, api_data: dict) -> bool:
        """比较核心 OHLCV 字段，判断 API 数据是否与缓存不一致"""
        for field in ('open', 'high', 'low', 'close', 'volume'):
            if abs(cached.get(field, 0) - api_data.get(field, 0)) > 1e-8:
                return True
        return False

    def _overwrite_kline_in_file(self, ts: int, api_data: dict):
        """覆写文件中指定时间戳的 K 线数据（用于运行时值修正）"""
        if not self.klines_file.exists():
            return
        try:
            with open(self.klines_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for i in range(len(lines) - 1, -1, -1):
                try:
                    d = json.loads(lines[i].strip())
                    if d.get('timestamp') == ts:
                        lines[i] = json.dumps(api_data, ensure_ascii=False) + '\n'
                        with open(self.klines_file, 'w', encoding='utf-8') as f:
                            f.writelines(lines)
                        return
                except (json.JSONDecodeError, KeyError):
                    continue
        except Exception:
            pass

    def _build_kline_dict(self, k: list) -> dict:
        """将 API 返回的 kline 数组转为存储字典"""
        return {
            'timestamp': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
            'turnover': float(k[7]) if len(k) > 7 else 0.0,
            'trades': int(k[8]) if len(k) > 8 else 0,
            'buy_volume': float(k[9]) if len(k) > 9 else 0.0,
            'buy_turnover': float(k[10]) if len(k) > 10 else 0.0
        }

    def _fetch_and_save_kline(self):
        """从 API 拉取最近 K 线，保存所有新闭合的 K 线（含追赶 + 数据校验 + 自动修正）"""
        if not self.api_client:
            return

        # 拉取最近 10 条（扩大追赶窗口，覆盖更长时间的网络抖动）
        klines = None
        for attempt in range(3):
            try:
                klines = self.api_client.get_klines(self.symbol, '1m', limit=10)
                break
            except Exception:
                if attempt < 2:
                    time.sleep(1)
        if klines is None:
            self._log("[Recorder] K线API拉取失败（3次重试均失败）")
            return

        if not klines or len(klines) < 2:
            return

        now_ms = int(time.time() * 1000)
        saved_count = 0
        corrected_count = 0
        for k in klines[:-1]:
            ts = k[0]

            if ts <= self._last_kline_ts:
                # v1.10.0: 运行时值比较 — 若 API 数据与缓存不一致，覆写文件
                cached = self._recent_cache.get(ts)
                if cached is not None:
                    api_data = self._build_kline_dict(k)
                    if self._kline_ohlcv_changed(cached, api_data):
                        self._overwrite_kline_in_file(ts, api_data)
                        self._recent_cache[ts] = api_data
                        corrected_count += 1
                        # 已修正的也需通知回调，让图表更新
                        if self.on_new_kline:
                            from decimal import Decimal
                            kline_dict = {
                                'timestamp': ts,
                                'open': Decimal(k[1]),
                                'high': Decimal(k[2]),
                                'low': Decimal(k[3]),
                                'close': Decimal(k[4]),
                                'volume': Decimal(k[5]),
                                'is_closed': True,
                            }
                            try:
                                self.on_new_kline(kline_dict)
                            except Exception:
                                pass
                continue

            # 数据校验：已闭合超过 10 秒的 K 线，buy_turnover 不应为 0（volume>0 时）
            # 注：文件保存可跳过脏数据（后续 _correct_recent_dirty_klines 补回），
            # 但回调和 _last_kline_ts 必须前进，否则 deque 永久缺失导致 K 线图有缺口
            buy_turnover = float(k[10]) if len(k) > 10 else 0.0
            volume = float(k[5]) if len(k) > 5 else 0.0
            skip_file_save = volume > 0 and buy_turnover <= 0 and self._is_kline_finalized(ts)

            # 日期切换
            date_str = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            if date_str != self.today:
                self.today = date_str
                self.klines_file = KLINES_DIR / self.symbol / f"{self.today}.jsonl"

            if not skip_file_save:
                # 文件级防重
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
                                    skip_file_save = True
                    except Exception:
                        pass

            kline_data = None
            if not skip_file_save:
                kline_data = self._build_kline_dict(k)
                self.klines_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.klines_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(kline_data, ensure_ascii=False) + '\n')
                self._klines_saved += 1

            self._last_kline_ts = ts
            saved_count += 1

            # 缓存最近数据（用于下次值比较）
            if kline_data is None:
                kline_data = self._build_kline_dict(k)
            self._recent_cache[ts] = kline_data
            # 剪裁缓存，只保留最近 20 条
            if len(self._recent_cache) > 20:
                keep = sorted(self._recent_cache.keys())[-20:]
                self._recent_cache = {k: self._recent_cache[k] for k in keep}

            # 回调通知（无论文件是否跳过，deque 必须保持连续）
            if self.on_new_kline:
                from decimal import Decimal
                kline_dict = {
                    'timestamp': ts,
                    'open': Decimal(k[1]),
                    'high': Decimal(k[2]),
                    'low': Decimal(k[3]),
                    'close': Decimal(k[4]),
                    'volume': Decimal(k[5]),
                    'is_closed': True,
                }
                try:
                    self.on_new_kline(kline_dict)
                except Exception:
                    pass

        if saved_count > 0 or corrected_count > 0:
            parts = []
            if saved_count > 0:
                parts.append(f"保存 {saved_count} 根")
            if corrected_count > 0:
                parts.append(f"修正 {corrected_count} 根")
            self._log(f"[Recorder] {'，'.join(parts)} (总计 {self._klines_saved})")

        # 每次轮询后检查并修正文件中已有的脏数据
        self._correct_recent_dirty_klines()

    def _correct_recent_dirty_klines(self):
        """检查文件中 buy_turnover=0 的条目，从 API 回补正确数据"""
        if not self.klines_file.exists():
            return

        try:
            # 读取文件最后 15 行（覆盖近 15 分钟）
            with open(self.klines_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()

            if not all_lines:
                return

            # 找出最近 15 条中 buy_turnover=0 的条目
            bad_indices = []
            check_start = max(0, len(all_lines) - 15)
            for i in range(check_start, len(all_lines)):
                try:
                    d = json.loads(all_lines[i].strip())
                    if d.get('buy_turnover', 0) <= 0 and d.get('volume', 0) > 0:
                        ts = d['timestamp']
                        if self._is_kline_finalized(ts):
                            bad_indices.append((i, ts))
                except (json.JSONDecodeError, KeyError):
                    continue

            if not bad_indices:
                return

            # 从 API 重取正确数据（一次 API 调用覆盖需要的范围）
            first_bad_ts = bad_indices[0][1]
            last_bad_ts = bad_indices[-1][1]
            try:
                fixed_klines = self.api_client.get_klines(
                    self.symbol, '1m', limit=len(bad_indices) + 5,
                    startTime=first_bad_ts - 60000, endTime=last_bad_ts + 120000
                )
            except Exception:
                return

            if not fixed_klines:
                return

            # 按时间戳索引 API 返回的数据
            api_map = {}
            for fk in fixed_klines:
                api_map[fk[0]] = fk

            # 替换脏数据
            corrected = 0
            for idx, ts in bad_indices:
                if ts in api_map:
                    fk = api_map[ts]
                    bt = float(fk[10]) if len(fk) > 10 else 0.0
                    if bt > 0:
                        all_lines[idx] = json.dumps(self._build_kline_dict(fk), ensure_ascii=False) + '\n'
                        corrected += 1

            if corrected > 0:
                with open(self.klines_file, 'w', encoding='utf-8') as f:
                    f.writelines(all_lines)
                # 同步更新缓存，避免下一轮重复修正
                for idx, ts in bad_indices:
                    if ts in api_map:
                        fk = api_map[ts]
                        bt = float(fk[10]) if len(fk) > 10 else 0.0
                        if bt > 0:
                            self._recent_cache[ts] = self._build_kline_dict(fk)
        except Exception:
            pass

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
