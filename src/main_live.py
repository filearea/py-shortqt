# -*- coding: utf-8 -*-
"""
实盘交易主入口 - v1.5.3
支持 TUI 设置模块
"""

import asyncio
import sys
import os
import time
import argparse
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta

# 设置 UTF-8 和窗口尺寸
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    os.environ['PYTHONUTF8'] = '1'
    # 设置控制台窗口尺寸（135 列 x 50 行）
    os.system('mode con: cols=135 lines=50')

    # 禁用控制台鼠标输入 + 关闭 QuickEdit，防止滚轮误触
    import ctypes
    try:
        hcon = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetConsoleMode(hcon, ctypes.byref(mode))
        # 清除 ENABLE_MOUSE_INPUT (0x0010) 和 ENABLE_QUICK_EDIT_MODE (0x0040)
        mode.value &= ~(0x0010 | 0x0040)
        ctypes.windll.kernel32.SetConsoleMode(hcon, mode)
    except Exception:
        pass

    # 发送 ANSI 转义序列禁用终端鼠标报告模式
    # 现代终端（Windows Terminal/ConEmu）可能启用了鼠标报告模式，
    # 滚轮会被转换为 \x1b[A / \x1b[B 等 ANSI 序列送入 stdin
    sys.stdout.write('\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l')
    sys.stdout.flush()

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent

# 配置代理（从 runtime.json 读取，优先于环境变量）
def _load_proxy_config():
    """从 runtime.json 读取代理配置并设置环境变量"""
    try:
        cfg_path = project_root / "config" / "runtime.json"
        if cfg_path.exists():
            import json as _json
            with open(cfg_path, 'r', encoding='utf-8') as _f:
                cfg = _json.load(_f)
            proxy = cfg.get('proxy', {})
            if proxy.get('enabled', False):
                host = proxy.get('host', '127.0.0.1')
                port = proxy.get('port', 7890)
                proxy_url = f'http://{host}:{port}'
                os.environ['HTTP_PROXY'] = proxy_url
                os.environ['HTTPS_PROXY'] = proxy_url
                return
    except Exception:
        pass
    # 代理未启用时清除环境变量（避免残留默认值影响连接）

_load_proxy_config()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import SYMBOL, LEVERAGE_LIMIT, TESTNET, LOG_DEBUG_MODE, LOG_LEVEL
from src.api.binance_client import BinanceAPIError
from src.trading.live import LiveTrader
from src.websocket import BinanceListener
from src.ui.live_ui import LiveTradingUI
from src.ui.settings_ui import SettingsUI
from src.logger import TradeLogger
from src.loggers import get_logger, LogManager
from src.loggers.market import MarketLogger
from src.config.manager import ConfigManager
from src.indicators import IndicatorsManager
from src.recorder import RealtimeRecorder
from src.metrics_recorder import MetricsRecorder
from src import __version__
from src.web import start_web_server

try:
    from rich.live import Live
    import msvcrt
except ImportError as e:
    print(f"缺少依赖库：{e}")
    print("请运行：pip install -r requirements.txt")
    sys.exit(1)


class LiveTradingBot:
    """实盘交易机器人"""
    
    def __init__(self, api_key: str, api_secret: str, account_name: str = "主账号"):
        self.symbol = SYMBOL
        self.running = True
        self.in_settings = False  # 是否在设置界面中
        self._pending_reset = False  # 重置确认标志
        self._pending_confirm_exit = False  # 退出确认标志
        self.account_name = account_name  # 保存账户名称供 run() 使用
        
        # 初始化新日志系统（项目根目录/logs/）
        project_root = Path(__file__).parent.parent
        self.log_manager: LogManager = get_logger(project_root / "logs", LOG_DEBUG_MODE)
        self.log_manager.set_level(LOG_LEVEL)
        self.log_manager.system.info(f"=== py-shortqt v{__version__} 启动 ===")
        self.log_manager.system.info(f"使用账户：{account_name}")
        self.log_manager.system.info(f"调试模式：{LOG_DEBUG_MODE}, 日志级别：{LOG_LEVEL}")
        
        # 错误日志列表（用于 TUI 显示）
        self.error_log = []
        self._agg_trade_logged = False  # v1.10.0: aggTrade 诊断
        self.web_server = None  # v1.10.0 Web 服务
        self._needs_init = False  # v1.10.0: 标记是否需要重新初始化（首次启动失败时置为 True）
        
        # 兼容旧日志（保留给 LiveTrader 使用）
        self.logger = TradeLogger(project_root / "logs")
        
        # 初始化指标管理器（v1.4.0 新增）
        self.indicators = IndicatorsManager()
        # 设置交易参数（默认值，后续从配置同步）
        self.indicators.set_trading_params(tp_points=0.99, sl_points=3.99, leverage=50, balance_usdt=50.0)
        # 初始化市场日志记录器（v1.4.0 新增）
        self.market_logger = MarketLogger(project_root / "logs")
        
        # 初始化实时数据记录器（v1.5.3 改造：K 线改为定时 API 拉取）
        self.recorder = RealtimeRecorder(symbol=self.symbol, orderbook_interval=60,
                                         log_func=lambda msg: self.log_manager.system.info(msg))
        self.log_manager.system.info(f"实时数据记录器已初始化（K 线定时 API 拉取，订单簿间隔：{self.recorder.orderbook_interval}秒）")
        
        # 初始化指标数据记录器（v1.4.2 新增）
        self.metrics_recorder = MetricsRecorder(symbol=self.symbol, save_interval=30)  # 30 秒
        self.log_manager.system.info(f"指标数据记录器已初始化（每{self.metrics_recorder.save_interval}秒保存一次指标快照）")
        
        # 初始化配置管理器
        self.log_manager.system.info("正在初始化配置管理器...")
        self.config_manager = ConfigManager(project_root / "config" / "runtime.json")

        # 同步近X分钟价格范围窗口
        pr_minutes = self.config_manager.get('price_range.minutes', 30)
        self.indicators.price_range.set_window(float(pr_minutes))

        # 获取杠杆配置
        self.log_manager.system.info("正在读取杠杆配置...")
        api_lev, actual_lev = self.config_manager.get_leverage_config()
        self.log_manager.system.info(f"杠杆配置：API={api_lev}x, 实际={actual_lev}x")
        
        # 初始化实盘交易器
        self.trader = LiveTrader(
            api_key=api_key,
            api_secret=api_secret,
            symbol=self.symbol,
            leverage_limit=api_lev,  # 使用配置中的 API 杠杆
            actual_leverage=actual_lev,  # 使用配置中的实际杠杆
            testnet=TESTNET,
            logger=self.logger,
            config_manager=self.config_manager,  # 传入配置管理器
            log_manager=self.log_manager,  # v1.5.0 修复：直接传入 log_manager
            indicators_manager=self.indicators  # v1.7.8: 传入指标管理器用于 ATR14 止盈止损
        )
        
        # 初始化行情 WebSocket
        self.listener = BinanceListener(
            self.symbol.lower(), "wss://fstream.binance.com/ws",
            log_func=lambda msg: self.log_manager.system.info(msg)
        )
        self.listener.add_callback(self.on_market_data)
        
        # 将 listener 赋值给 trader，用于 UI 显示连接状态
        self.trader.listener = self.listener
        
        # v1.5.3: 注入 api_client 并启动 K 线定时拉取
        self.recorder.api_client = self.trader.api
        # 新 K 线收盘回调 → 更新指标（替代不可靠的 WebSocket kline 事件）
        def _on_kline_closed(kline):
            self.indicators.update_kline(kline)
            self.indicators.volatility.track_atr14_percentile()
        self.recorder.on_new_kline = _on_kline_closed
        self.recorder.start_kline_timer()
        self.log_manager.system.info("K 线定时拉取已启动（每分钟从 API 获取完整数据，收盘时更新指标）")
        
        # 传递 error_log 给 trader（TUI 显示）
        self.trader.error_log = self.error_log
        
        # UI
        tp = self.config_manager.get_take_profit_price(Decimal('2150'))
        self.ui = LiveTradingUI(self.trader, api_lev, tp, Decimal('3'), actual_lev, 
                                 self.config_manager, self.indicators)
        
        # 设置 UI
        try:
            self.settings_ui = SettingsUI(self.config_manager, self.trader)
            self.log_manager.system.info("设置 UI 初始化成功")
        except Exception as e:
            self.log_manager.system.error(f"设置 UI 初始化失败：{e}", exc_info=True)
            raise
        
        self.log_manager.system.info(f"交易对：{self.symbol}, API 杠杆：{api_lev}x, 实际杠杆：{actual_lev}x")
    
    def _init_historical_klines(self, limit: int = 499):
        """v1.10.0 改造：本地文件优先 → API fallback → 写入本地 + ATR14 回填"""
        try:
            from src.data_collector import KLINES_DIR

            now = datetime.now()
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            start_ms = int(today.timestamp() * 1000)
            current_minute_start = now.replace(second=0, microsecond=0)
            current_minute_ms = int(current_minute_start.timestamp() * 1000)
            now_ms = int(now.timestamp() * 1000)
            date_str = today.strftime("%Y-%m-%d")
            kline_file = KLINES_DIR / self.symbol / f"{date_str}.jsonl"

            # ── 第一步：从本地文件加载今日 K 线 ──
            local_raw = []  # 本地文件中的原始 kline 列表
            if kline_file.exists():
                with open(kline_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                            ts = d['timestamp']
                            if ts >= start_ms and ts < current_minute_ms:
                                local_raw.append(d)
                        except (json.JSONDecodeError, KeyError):
                            continue

            local_count = len(local_raw)
            # 预期应有 kline 数（00:00 到上一个完整分钟）
            expected = max(0, (current_minute_ms - start_ms) // 60000)

            # ── v1.10.0：本地数据完整性校验 ──
            file_corrupted = False
            if local_raw:
                # 1) 检测时间缺口（>1 分钟的间隔视为缺口）
                local_sorted = sorted(local_raw, key=lambda d: d['timestamp'])
                prev_ts = local_sorted[0]['timestamp']
                for i in range(1, len(local_sorted)):
                    ts = local_sorted[i]['timestamp']
                    if ts - prev_ts > 60000:
                        file_corrupted = True
                        break
                    prev_ts = ts

                # 2) 检测脏数据（已闭合 K 线 volume>0 但 buy_turnover=0）
                if not file_corrupted:
                    for d in local_raw:
                        vol = d.get('volume', 0)
                        bt = d.get('buy_turnover', 0)
                        ts = d['timestamp']
                        if vol > 0 and bt <= 0 and (now_ms - (ts + 60000)) > 10000:
                            file_corrupted = True
                            break

                # 3) 检测尾部缺数据（最后闭合 K 线距今 >1 分钟）
                if not file_corrupted and local_sorted:
                    last_ts = local_sorted[-1]['timestamp']
                    if current_minute_ms - last_ts > 120000:  # 缺 ≥2 根
                        file_corrupted = True
                # 4) 数量严重不足（缺 ≥2 根）
                if not file_corrupted and len(local_sorted) < expected - 1:
                    file_corrupted = True

            if file_corrupted:
                self.log_manager.system.info(
                    f'本地K线校验失败（{local_count}根），删除文件并从 API 重建...'
                )
                try:
                    kline_file.unlink()
                except Exception:
                    pass
                local_raw = []
                local_count = 0

            need_api = (
                not kline_file.exists() or
                local_count == 0 or
                local_count < max(1, expected - 3)
            )

            if need_api:
                self.log_manager.system.info(
                    f'本地K线不足（{local_count}根，预期～{expected}根），fallback 币安 API...'
                )
                raw_klines = self.trader.api.get_klines(self.symbol, '1m', limit=1500)
                if not raw_klines:
                    self.log_manager.system.warning("API 返回为空，仅使用本地数据")
                    raw_klines = []
                today_api = [k for k in raw_klines if k[0] >= start_ms]
                # 写回本地（完整覆盖今日文件，跳过最后一条未关闭 + 脏数据）
                kline_file.parent.mkdir(parents=True, exist_ok=True)
                write_count = 0
                skip_dirty = 0
                with open(kline_file, 'w', encoding='utf-8') as f:
                    for k in today_api[:-1]:
                        if len(k) < 11:
                            continue
                        # 数据校验：已闭合超过 10 秒的 K 线，buy_turnover 不应为 0
                        buy_turnover = float(k[10])
                        volume = float(k[5])
                        ts = k[0]
                        kline_close_time = ts + 60000
                        if volume > 0 and buy_turnover <= 0 and (now_ms - kline_close_time) > 10000:
                            skip_dirty += 1
                            continue
                        kd = {
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
                        f.write(json.dumps(kd, ensure_ascii=False) + '\n')
                        write_count += 1
                self.log_manager.system.info(f'API 返回 {len(today_api)} 根K线，写入 {write_count} 根到本地' +
                    (f'，跳过 {skip_dirty} 根未最终确定' if skip_dirty > 0 else ''))
            else:
                self.log_manager.system.info(f'本地K线充足（{local_count}根，预期～{expected}根），跳过 API')
                # 将本地数据转为 API 格式 [ts, o, h, l, c, v, 0, turnover, trades, buy_vol, buy_turnover]
                raw_klines = []
                for d in local_raw:
                    raw_klines.append([
                        d['timestamp'], d['open'], d['high'], d['low'], d['close'],
                        d['volume'], 0, d.get('turnover', 0), d.get('trades', 0),
                        d.get('buy_volume', 0), d.get('buy_turnover', 0)
                    ])
                today_api = [k for k in raw_klines if k[0] >= start_ms]

            # ── 刷新最近 10 根已闭合 K 线（修复重启导致的本地脏数据）──
            try:
                refresh_n = 10
                refresh_raw = self.trader.api.get_klines(self.symbol, '1m', limit=refresh_n + 1)
                if refresh_raw:
                    api_map = {k[0]: k for k in refresh_raw if len(k) >= 6}
                    replaced = 0
                    for i, k in enumerate(today_api):
                        ts = k[0]
                        if ts in api_map and ts < current_minute_ms:
                            today_api[i] = api_map[ts]
                            replaced += 1
                    if replaced > 0:
                        self.log_manager.system.info(
                            f'API 刷新覆盖 {replaced} 根本地 K 线（修复脏数据）'
                        )
                        # 同步更新本地文件
                        self._overwrite_kline_file(kline_file, today_api)
            except Exception as e:
                self.log_manager.system.info(f'刷新最近K线失败（非致命）：{e}')

            # ── 第二步：喂给指标管理器 ──
            closed = [k for k in today_api if k[0] < current_minute_ms and len(k) >= 6]
            for k in closed:
                kline = {
                    'timestamp': k[0],
                    'open': Decimal(k[1]),
                    'high': Decimal(k[2]),
                    'low': Decimal(k[3]),
                    'close': Decimal(k[4]),
                    'volume': Decimal(k[5]),
                    'is_closed': True
                }
                self.indicators.update_kline(kline)

            # ── 第三步：跨日补齐 ──
            need_count = 15
            if len(self.indicators.volatility.klines) < need_count:
                yesterday = today - timedelta(days=1)
                yesterday_file = KLINES_DIR / self.symbol / f'{yesterday.strftime("%Y-%m-%d")}.jsonl'
                if yesterday_file.exists():
                    self.log_manager.system.info(
                        f'当天K线不足({len(self.indicators.volatility.klines)}根)，从昨日文件补齐...'
                    )
                    yk = []
                    with open(yesterday_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                d = json.loads(line)
                                yk.append({
                                    'timestamp': d['timestamp'],
                                    'open': Decimal(str(d['open'])),
                                    'high': Decimal(str(d['high'])),
                                    'low': Decimal(str(d['low'])),
                                    'close': Decimal(str(d['close'])),
                                    'volume': Decimal(str(d.get('volume', 0))),
                                    'is_closed': True
                                })
                            except (json.JSONDecodeError, KeyError):
                                continue
                    yk.sort(key=lambda k: k['timestamp'])
                    needed = need_count - len(self.indicators.volatility.klines)
                    for k in yk[-needed:]:
                        self.indicators.update_kline(k)
                    self.log_manager.system.info(f'从昨日补入 {min(needed, len(yk))} 根K线')

            # ── 第四步：ATR14 24h 历史回填 ──
            self._backfill_atr14_history(KLINES_DIR)

            # ── 第五步：价格范围 ──
            self._seed_price_range(closed)

            # 更新 recorder 防重时间戳
            if closed:
                self.recorder._last_kline_ts = closed[-1][0]

            self.log_manager.system.info(
                f'历史 K 线初始化完成：指标加载 {len(closed)} 根（跳过最后一条未关闭）'
            )

        except Exception as e:
            error_msg = f"历史 K 线初始化失败：{e}"
            self.log_manager.system.warning(error_msg)
            self.error_log.append({'time': datetime.now(), 'msg': str(e)})

    @staticmethod
    def _overwrite_kline_file(filepath: Path, klines: list):
        """用 klines 列表覆盖写入本地文件（跳过未闭合的最后一根）"""
        closed = [k for k in klines if len(k) >= 6]
        if not closed:
            return
        with open(filepath, 'w', encoding='utf-8') as f:
            for k in closed:
                kd = {
                    'timestamp': k[0],
                    'open': float(k[1]),
                    'high': float(k[2]),
                    'low': float(k[3]),
                    'close': float(k[4]),
                    'volume': float(k[5]),
                    'turnover': float(k[7]) if len(k) > 7 else 0,
                    'trades': int(k[8]) if len(k) > 8 else 0,
                    'buy_volume': float(k[9]) if len(k) > 9 else 0,
                    'buy_turnover': float(k[10]) if len(k) > 10 else 0,
                }
                f.write(json.dumps(kd, ensure_ascii=False) + '\n')

    def _backfill_atr14_history(self, klines_dir: Path):
        """从本地文件回填过去 24h 的 ATR14% 到 volatility 历史队列"""
        vol = self.indicators.volatility

        # 已有足够样本（≥60 = 1h），跳过回填
        if len(vol._atr14_percentile_history) >= 60:
            return

        # 加载过去 2 天的本地 kline 文件
        all_klines = []
        now = datetime.now()
        for days_ago in range(2):
            d = now - timedelta(days=days_ago)
            fpath = klines_dir / self.symbol / f'{d.strftime("%Y-%m-%d")}.jsonl'
            if not fpath.exists():
                continue
            with open(fpath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        all_klines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        if len(all_klines) < 15:
            return

        all_klines.sort(key=lambda k: k['timestamp'])

        # 只取最近 24h，往前多取 14 根做 ATR 预热
        cutoff_24h = int((now - timedelta(hours=24)).timestamp() * 1000)
        start_idx = 0
        for i, k in enumerate(all_klines):
            if k['timestamp'] >= cutoff_24h:
                start_idx = max(0, i - 14)
                break

        batch = all_klines[start_idx:]
        tr_values = []
        prev_close = None

        for k in batch:
            high = float(k['high'])
            low = float(k['low'])
            close = float(k['close'])

            if prev_close is not None:
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                tr_values.append(tr)
            prev_close = close

            if len(tr_values) >= 14 and close > 0:
                atr14 = sum(tr_values[-14:]) / 14
                atr14_pct = (atr14 / close) * 100
                vol._atr14_percentile_history.append(atr14_pct)

        if len(vol._atr14_percentile_history) > 0:
            vol.recompute_atr14_percentile()
            self.indicators._update_snapshot()  # v1.10.0：刷新快照，否则 atr14_ref 停留在默认 'normal'
            self.log_manager.system.info(
                f'ATR14 24h 历史回填完成：{len(vol._atr14_percentile_history)} 个样本'
                f' | 当前={vol.get_atr14_pct():.4f}% | 百分位={vol._atr14_percentile} 分级={vol._atr14_ref}'
            )

        # v1.10.0: 回填 _klines deque（Web UI K线图数据源）
        if batch:
            vol._klines.clear()
            for i, k in enumerate(batch):
                kline_dict = {
                    'timestamp': k['timestamp'],
                    'open': Decimal(str(k['open'])),
                    'high': Decimal(str(k['high'])),
                    'low': Decimal(str(k['low'])),
                    'close': Decimal(str(k['close'])),
                    'volume': Decimal(str(k.get('volume', 0))),
                }
                if i < len(batch) - 1:
                    vol._klines.append(kline_dict)
                else:
                    vol.current_kline = kline_dict
            self.log_manager.system.info(f'K线 deque 回填完成：{len(vol._klines)} 根（含第{len(batch)}根为 current）')

    async def on_market_data(self, event_type: str, data: dict):
        """市场数据回调 - v1.5.0 新增移动止损和浮亏保护"""
        # 移除调试日志，避免 TUI 抖动
        try:
            if event_type == 'ticker':
                price = data['price']
                self.trader.update_price(price)
                # 记录价格
                self.logger.record_price(price)

                # tick级价格流追踪（剥头皮评分用）
                if self.indicators:
                    self.indicators.update_tick(price)
                
                # v1.5.0 新增：更新移动止损和浮亏保护
                # v1.9.0：分批模式下移动止损禁用
                if self.trader.position and self.trader.trailing_stop_manager:
                    await self.trader.trailing_stop_manager.update_trailing_stop(price)

                # v1.5.0 修复：移除高频日志，避免 TUI 抖动
                # 只在 check_and_protect 内部记录关键日志
                if self.trader.position and self.trader.loss_protection_manager and self.trader.loss_protection_manager.enabled:
                    # 计算未实现盈亏
                    if self.trader.position['side'] == 'LONG':
                        pnl = (price - self.trader.position['entry_price']) * self.trader.position['size']
                    else:
                        pnl = (self.trader.position['entry_price'] - price) * self.trader.position['size']

                    # 调用 check_and_protect（内部会记录必要日志）
                    await self.trader.loss_protection_manager.check_and_protect(price, pnl)

                # v1.9.0：分批模式浮亏保护
                if self.trader.batch_state and self.trader.batch_state.get('enabled') and not self.trader.batch_state.get('round_closed'):
                    self.trader._check_batch_loss_protection()
                    # v1.10.0：兜底处理被节流拦截的 SL/SM 更新
                    if self.trader.batch_state.get('_pending_sl_update') and time.time() - self.trader.batch_state.get('last_sl_update_ts', 0) >= 3.0:
                        self.trader._schedule_sl_sm_update()
            
            elif event_type == 'depth':
                bids = data.get('bids', [])
                asks = data.get('asks', [])

                # 首次收到深度数据时打印日志
                if not hasattr(self, '_depth_received'):
                    self._depth_received = True
                    self.log_manager.system.debug(f"[调试] 首次收到深度数据：bids={len(bids)}, asks={len(asks)}")

                self.trader.update_orderbook(bids, asks)

                # 复用已转换的深度数据（update_orderbook 已做 Decimal 转换）
                ob = self.trader.orderbook
                bids_decimal = [(b[0], b[1]) for b in ob['bids']]
                asks_decimal = [(a[0], a[1]) for a in ob['asks']]
                self.indicators.update_orderbook(bids_decimal, asks_decimal)
                self.recorder.save_orderbook(bids_decimal, asks_decimal)

                # v1.10.0：推送到 Web UI
                if self.web_server:
                    self.web_server.update_depth(bids_decimal, asks_decimal)
            
            elif event_type == 'kline':
                # WS @trade 合成 K 线 — 仅更新 current_kline 跟踪，不写入历史队列
                # 指标更新（ATR/振幅等）由 recorder 的 REST API 拉取回调驱动
                if not data or data.get('close') is None:
                    return

                self.indicators.volatility.set_current_kline(data)

                # v1.4.1 新增：实时保存 K 线数据
                self.recorder.save_kline(data)
                
                # v1.4.2 新增：每 30 秒保存指标快照
                self.metrics_recorder.save_snapshot(self.indicators, self.trader)

                # v1.10.0：推 K 线缓存到 Web UI
                if self.web_server and hasattr(self.indicators.volatility, '_klines'):
                    self.web_server.update_klines_cache(list(self.indicators.volatility._klines))

            elif event_type == 'aggTrade':
                # v1.10.0：主动成交比率 + 缓存最新成交价
                trade_price = data.get('price')
                if trade_price is not None:
                    self.trader.last_trade_price = float(trade_price)
                if not self._agg_trade_logged:
                    self._agg_trade_logged = True
                    self.log_manager.system.info(f'[诊断] 首个 aggTrade 回调触发: m={data.get("m")} qty={data.get("qty")}')
                self.indicators.update_agg_trade(data)
                ratio = self.indicators.get_taker_ratio()
                if self.web_server:
                    self.web_server.update_taker_ratio(
                        ratio['buy_pct'], ratio['sell_pct'],
                        trade_count=ratio.get('trade_count', 0),
                        last_update=ratio.get('last_update', 0)
                    )
        except Exception as e:
            error_msg = f"市场数据处理异常：{e}"
            self.log_manager.system.debug(error_msg)
            # 添加到错误日志列表（TUI 显示）- v1.5.0 修复：Decimal 转字符串
            self.error_log.append({'time': datetime.now(), 'msg': str(e)})
            if len(self.error_log) > 10:
                self.error_log.pop(0)
    
    async def place_order(self, side: str):
        """开仓"""
        ok = await self.trader.open_position(side)
        if not ok:
            raise RuntimeError("开仓失败 — 请检查保证金/杠杆/设置")
    
    async def cancel_order(self):
        """撤单（v1.9.0：含分批模式）"""
        # v1.9.0：分批模式
        if self.trader.batch_state and self.trader.batch_state.get('enabled') and not self.trader.batch_state.get('round_closed'):
            await self.trader.cancel_open_order()
            return
        # 如果有提前平仓单，撤销并恢复止盈止损
        if self.trader.early_close_order:
            self.trader.cancel_early_close()
        # 否则撤销开仓挂单
        elif self.trader.pending_order:
            await self.trader.cancel_open_order()
    
    async def close_position_early(self):
        """提前平仓"""
        await self.trader.close_position_early()
    
    def try_enter_settings(self) -> bool:
        """
        尝试进入设置界面
        返回：是否成功进入
        """
        # 安全检测：有挂单或持仓时禁止进入
        if self.trader.pending_order:
            self.trader._add_action("⚠️ 禁止进入", "请先撤销挂单（按 ←）")
            return False

        if self.trader.position:
            self.trader._add_action("⚠️ 禁止进入", "请先平仓（按 →）")
            return False

        # v1.9.0：分批模式下禁止进入设置
        if self.trader.batch_state and self.trader.batch_state.get('enabled') and not self.trader.batch_state.get('round_closed'):
            self.trader._add_action("⚠️ 禁止进入", "分批建仓进行中，请先完成或撤销")
            return False
        
        # 可以进入
        self.in_settings = True
        return True

    def _seed_price_range(self, klines: list = None):
        """
        从历史 K 线填充 PriceRangeTracker
        klines: 可选，已有的 K 线数据。为 None 时从 API 获取。
        """
        pr_minutes = self.config_manager.get('price_range.minutes', 30)
        cutoff_ms = int((time.time() - pr_minutes * 60) * 1000)

        source = klines
        if source is None:
            try:
                limit = pr_minutes + 5
                source = self.trader.api.get_klines(self.symbol, '1m', limit=limit)
                if not source:
                    return
            except Exception:
                return

        self.indicators.price_range.clear()
        seeds = []
        for k in source:
            if len(k) < 6:
                continue
            if k[0] < cutoff_ms:
                continue
            ts_sec = k[0] / 1000.0
            seeds.append((ts_sec, float(k[2]), float(k[3])))
        if seeds:
            self.indicators.price_range.seed_from_klines(seeds)
            self.log_manager.system.debug(
                f'价格范围已刷新：{len(seeds)} 根 K 线（{pr_minutes} 分钟）'
            )

    def _apply_settings(self):
        """设置保存后生效：杠杆同步 + 移动止损/浮亏保护配置重读"""
        self.trader._add_action("[OK] 配置已保存并退出", "")
        self.log_manager.system.debug("设置操作：配置已保存并退出")

        # 1. 更新杠杆
        api_lev, actual_lev = self.config_manager.get_leverage_config()
        self.trader.leverage_limit = api_lev
        self.trader.actual_leverage = actual_lev

        # 同步到币安 API
        try:
            self.trader.api.set_leverage(self.trader.symbol, api_lev)
            self.log_manager.system.debug(f"杠杆已同步到交易所：{api_lev}x")
        except Exception as e:
            self.log_manager.system.debug(f"杠杆同步失败：{e}")

        # 更新 UI 杠杆显示
        tp = self.config_manager.get_take_profit_price(Decimal('2150'))
        self.ui = LiveTradingUI(self.trader, api_lev, tp, Decimal('3'), actual_lev, self.config_manager, self.indicators)

        # 2. 刷新移动止损配置
        if self.trader.trailing_stop_manager:
            ts_config = self.config_manager.get_trailing_stop_config()
            self.trader.trailing_stop_manager.refresh_config(ts_config)
            self.log_manager.system.debug(
                f"移动止损配置已刷新：{'启用' if ts_config.get('enabled') else '关闭'}"
            ) if self.log_manager else None

        # 3. 刷新浮亏保护配置
        if self.trader.loss_protection_manager:
            lp_config = self.config_manager.get_loss_protection_config()
            self.trader.loss_protection_manager.refresh_config(lp_config)
            self.log_manager.system.debug(
                f"浮亏保护配置已刷新：{'启用' if lp_config.get('enabled') else '关闭'}"
            ) if self.log_manager else None

        # 4. 刷新近X分钟价格范围（重设窗口 + 从API拉K线填充历史数据）
        pr_minutes = self.config_manager.get('price_range.minutes', 30)
        if self.indicators and hasattr(self.indicators, 'price_range'):
            self.indicators.price_range.set_window(float(pr_minutes))
            self._seed_price_range()  # 重新从API拉K线，确保新时间范围有数据
            self.log_manager.system.debug(f"价格范围窗口已更新：{pr_minutes} 分钟"
                                          ) if self.log_manager else None

        # 5. v1.10.0：动态启停 Web 服务
        asyncio.ensure_future(self._apply_web_ui_setting())

        # 6. v1.10.0：代理变更 — 重新应用代理配置，需要时触重重连
        self._reapply_proxy_env(self.config_manager)
        if self._needs_init:
            asyncio.ensure_future(self._reinitialize())
        else:
            self.trader._add_action("ℹ️ 代理已更新", "WebSocket 重连后生效（需重启或等待自动重连）")

        self.log_manager.system.debug(f"杠杆已更新：API={api_lev}x, 实际={actual_lev}x")

    async def _apply_web_ui_setting(self):
        """动态启停 Web 服务"""
        web_enabled = self.config_manager.is_web_ui_enabled()
        web_cfg = self.config_manager.get_web_ui_config()
        cfg_token = web_cfg.get('token', '')
        current_token = self.web_server.token if self.web_server else ''

        if web_enabled and not self.web_server:
            try:
                self.web_server = await start_web_server(
                    trader=self.trader,
                    host=web_cfg.get('host', '0.0.0.0'),
                    port=web_cfg.get('port', 8099),
                    log_manager=self.log_manager,
                    token=cfg_token,
                    app=self
                )
                self.trader.web_server = self.web_server
                # 新生成的随机 token 写回 config
                if not cfg_token:
                    self.config_manager.set('web_ui.token', self.web_server.token)
                    self.config_manager.save()
                    self.log_manager.system.info(f'Web UI token 已保存到 config: {self.web_server.token}')
                url = f"http://{self.web_server._get_local_ip()}:{web_cfg.get('port', 8099)}?token={self.web_server.token}"
                self.log_manager.system.info(f"Web UI 已启动: {url}")
                self.trader._add_action("Web UI 已启动", url)
            except Exception as e:
                self.log_manager.system.error(f"Web 服务启动失败: {e}")
                self.trader._add_action("Web 服务启动失败", str(e))
        elif web_enabled and self.web_server and cfg_token and cfg_token != current_token:
            # token 变更 → 重启 Web 服务
            try:
                await self.web_server.stop()
            except Exception:
                pass
            try:
                self.web_server = await start_web_server(
                    trader=self.trader,
                    host=web_cfg.get('host', '0.0.0.0'),
                    port=web_cfg.get('port', 8099),
                    log_manager=self.log_manager,
                    token=cfg_token,
                    app=self
                )
                self.trader.web_server = self.web_server
                url = f"http://{self.web_server._get_local_ip()}:{web_cfg.get('port', 8099)}?token={self.web_server.token}"
                self.log_manager.system.info(f"Web UI 已重启（token 已更新）: {url}")
                self.trader._add_action("Web UI Token 已更新", url)
            except Exception as e:
                self.log_manager.system.error(f"Web 服务重启失败: {e}")
                self.trader._add_action("Web 服务重启失败", str(e))
        elif not web_enabled and self.web_server:
            try:
                await self.web_server.stop()
                self.trader._add_action("Web UI 已停止", "")
            except Exception as e:
                self.log_manager.system.warning(f"Web 服务停止异常: {e}")
            self.web_server = None
            self.trader.web_server = None

    @staticmethod
    def _reapply_proxy_env(config_manager):
        """根据当前配置重新设置代理环境变量"""
        proxy_cfg = config_manager.get('proxy', {})
        if proxy_cfg.get('enabled', False):
            host = proxy_cfg.get('host', '127.0.0.1')
            port = proxy_cfg.get('port', 7890)
            proxy_url = f'http://{host}:{port}'
            os.environ['HTTP_PROXY'] = proxy_url
            os.environ['HTTPS_PROXY'] = proxy_url
        else:
            os.environ.pop('HTTP_PROXY', None)
            os.environ.pop('HTTPS_PROXY', None)

    async def _reinitialize(self):
        """重新初始化连接（代理变更后调用）"""
        self.log_manager.system.info("正在重新初始化连接...")
        self.trader._add_action("🔄 重新连接中", "正在初始化...")

        # 重新应用代理配置
        self._reapply_proxy_env(self.config_manager)

        try:
            if await self.trader.initialize():
                self._needs_init = False
                # 启动 WebSocket
                self.log_manager.system.info("正在连接行情 WebSocket...")
                asyncio.create_task(self.listener.connect())
                # 拉取历史数据
                asyncio.create_task(self.trader.fetch_position_history())
                self.trader._add_action("✅ 重连成功", "连接已恢复，功能正常")
                self.log_manager.system.info("重连成功")
            else:
                self.trader._add_action("❌ 重连失败", "请检查代理配置或网络连接")
                self.log_manager.system.warning("重连失败，初始化返回 False")
        except Exception as e:
            self.trader._add_action("❌ 重连失败", f"{e}")
            self.log_manager.system.error(f"重连失败: {e}")

    async def _cleanup_resources(self, ws_task=None):
        """清理资源（确保 WebSocket 正确关闭）"""
        self.log_manager.system.debug("正在清理资源...")
        self.running = False
        self.listener.running = False

        # v1.10.0：停止 Web 服务
        if self.web_server:
            try:
                await self.web_server.stop()
                self.log_manager.system.debug("Web 服务已停止")
            except Exception as e:
                self.log_manager.system.warning(f"Web 服务停止异常: {e}")

        # 关闭用户数据流 WebSocket
        await self.trader.cleanup()

        # 等待行情 WebSocket 任务结束
        if ws_task:
            try:
                await asyncio.wait_for(ws_task, timeout=2.0)
            except asyncio.TimeoutError:
                self.log_manager.system.warning("WebSocket 任务超时，强制结束")

        self.log_manager.system.debug("资源已清理")
    
    async def run(self):
        """运行主循环"""
        # v1.5.0 修复：在 TUI 启动前记录关键日志
        msg = "=" * 70
        print(msg)
        self.log_manager.system.info(msg)
        
        msg = f"py-shortqt v{__version__} - 实盘交易模式"
        print(msg)
        self.log_manager.system.info(msg)
        
        print(msg)
        self.log_manager.system.info(msg)
        
        # 1. 初始化实盘连接（带重试机制）
        max_retries = 3
        init_ok = False
        for retry in range(max_retries):
            try:
                if await self.trader.initialize():
                    init_ok = True
                    # 记录启动时账户余额（用于复合收益率计算）
                    self.logger.log_balance('startup', self.trader.available_balance, {
                        'account': self.account_name,
                        'leverage': self.trader.actual_leverage
                    })

                    # v1.4.0: 获取 499 根（约 8 小时）历史 K 线（8 小时数据，带速率限制）
                    # v1.5.0 修复：在后台线程中执行，避免阻塞主线程
                    await asyncio.sleep(1)  # 等待 1 秒，避免与初始化请求冲突
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._init_historical_klines(limit=300)
                    )

                    # v1.6.6: 拉取历史持仓
                    asyncio.create_task(self.trader.fetch_position_history())

                    break
                else:
                    if retry < max_retries - 1:
                        print(f"初始化失败，{retry + 1}/{max_retries}，5 秒后重试...")
                        await asyncio.sleep(5)
                    else:
                        print("\n[WARN] 实盘初始化失败，进入离线模式（可进入设置配置代理后重试）")
                        self.trader._add_action("❌ 连接失败", "请进入 系统设置 → 启用代理 → 保存重试")
                        self._needs_init = True
            except Exception as e:
                error_msg = str(e)
                if "too many requests" in error_msg.lower() or "banned" in error_msg.lower():
                    print(f"\n[ERROR] API 请求频率过高，IP 可能被限制")
                    print(f"完整错误：{e}")
                    print("\n建议：")
                    print("1. 等待 1-2 分钟让 IP 解封")
                    print("2. 检查是否有多个程序同时运行")
                    print("3. 使用 WebSocket 订阅代替轮询")
                    self.trader._add_action("❌ API 限流", "IP 可能被限制，等待 1-2 分钟后重试")
                    self._needs_init = True
                    break
                if retry < max_retries - 1:
                    print(f"初始化失败：{e}，{retry + 1}/{max_retries}，5 秒后重试...")
                    await asyncio.sleep(5)
                else:
                    print(f"\n[WARN] 实盘初始化失败：{e}，进入离线模式")
                    self.trader._add_action("❌ 连接失败", "请进入 系统设置 → 启用代理 → 保存重试")
                    self._needs_init = True

        # 2. 连接行情 WebSocket（仅在初始化成功时）
        ws_task = None
        if init_ok:
            self.log_manager.system.info("正在连接行情 WebSocket...")
            ws_task = asyncio.create_task(self.listener.connect())

            # 等待行情连接（最多 15 秒）
            for i in range(30):  # 15 秒 = 30 * 0.5 秒
                if self.listener.connected:
                    self.log_manager.system.info("行情已连接")
                    break
                await asyncio.sleep(0.5)
            else:
                self.log_manager.system.warning("行情连接超时，程序继续运行，WebSocket 将在后台重试")

        # 3. v1.4.0 新增：补全缺失的历史数据（WebSocket 连接后）
        if init_ok:
            self.log_manager.system.info("正在补全缺失的历史数据（过去 14 天）...")
            try:
                from src.data_collector import collect_historical_data
                # 使用 asyncio 运行同步函数，避免阻塞
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: collect_historical_data([self.symbol], days=14)
                )
                self.log_manager.system.info("历史数据补全完成")
            except Exception as e:
                self.log_manager.system.warning(f"历史数据补全失败：{e}，程序将继续运行")

        # v1.10.0：启动 Web 服务（移动端 Web UI）
        if self.config_manager.is_web_ui_enabled():
            try:
                web_cfg = self.config_manager.get_web_ui_config()
                cfg_token = web_cfg.get('token', '')
                self.web_server = await start_web_server(
                    trader=self.trader,
                    host=web_cfg.get('host', '0.0.0.0'),
                    port=web_cfg.get('port', 8099),
                    log_manager=self.log_manager,
                    token=cfg_token,
                    app=self
                )
                self.trader.web_server = self.web_server
                # 新生成的随机 token 写回 config
                if not cfg_token:
                    self.config_manager.set('web_ui.token', self.web_server.token)
                    self.config_manager.save()
                self.log_manager.system.info(f"Web UI 已启动: http://{self.web_server._get_local_ip()}:{web_cfg.get('port', 8099)}?token={self.web_server.token}")
                self.trader._add_action("Web UI 已启动", f"http://{self.web_server._get_local_ip()}:{web_cfg.get('port', 8099)}?token={self.web_server.token}")
            except Exception as e:
                self.log_manager.system.error(f"Web 服务启动失败: {e}")

        # 5. 启动历史持仓轮询
        #    持仓变更由事件驱动 _refresh_history_delayed 实时刷新，
        #    空闲时每 5 分钟兜底同步一次，避免无谓 API 消耗。
        async def _history_poll():
            while self.running:
                await asyncio.sleep(300)
                try:
                    await self.trader.fetch_position_history()
                except Exception:
                    pass

        history_task = asyncio.create_task(_history_poll())

        # v1.10.0：ATR14 24h 百分位每小时重算
        async def _atr14_percentile_recompute():
            # 初始延迟 60 秒，等待 K 线数据积累
            await asyncio.sleep(60)
            while self.running:
                try:
                    self.indicators.volatility.recompute_atr14_percentile()
                except Exception:
                    pass
                await asyncio.sleep(3600)

        atr14_pct_task = asyncio.create_task(_atr14_percentile_recompute())

        # 6. 主循环
        self.log_manager.system.info("进入主循环")

        try:
            with Live(
                self.ui.render(),
                refresh_per_second=10,  # 与深度数据更新频率一致（100ms），满足剥头皮盘感需求
                screen=True,
                redirect_stdout=True,
                redirect_stderr=True,
                transient=False
            ) as live:
                while self.running:
                    try:
                        # 先刷新 UI（Rich Live 内部 refresh_per_second=2 自动节流）
                        console_h = live.console.height
                        if self.in_settings:
                            live.update(self.settings_ui.render())
                        else:
                            live.update(self.ui.render(console_height=console_h))
                    except Exception as e:
                        self.log_manager.system.error(f"UI 更新错误：{e}")
                        continue
                    
                    try:
                        # 键盘输入（非阻塞）
                        if msvcrt.kbhit():
                            key = msvcrt.getch()

                            # 过滤 \x00 前缀事件（功能键/鼠标滚轮）
                            if key == b'\x00':
                                msvcrt.getch()  # 消费第二字节
                                continue

                            try:
                                key_char = key.decode('utf-8', errors='ignore').lower()
                            except:
                                key_char = key.decode('gbk', errors='ignore').lower()

                            # 特殊键映射
                            if key == b'\r' or key == b'\n':  # Enter 键
                                key_char = 'enter'
                            elif key == b'\x08':  # Backspace
                                key_char = 'backspace'
                            elif key == b'\x1b':  # Esc
                                key_char = 'escape'
                            elif key == b'\x09':  # Tab
                                key_char = 'tab'
                            
                            # 在设置界面中
                            if self.in_settings:
                                if key == b'\xe0' or key == b'\x00':  # 方向键前缀
                                    key = msvcrt.getch()
                                    if key in (b'I', b'i', b'Q', b'q'):
                                        pass  # 鼠标滚轮，忽略
                                    elif key == b'H':  # ↑
                                        self.settings_ui.handle_key('up')
                                    elif key == b'P':  # ↓
                                        self.settings_ui.handle_key('down')
                                    elif key == b'K':  # ←
                                        self.settings_ui.handle_key('left')
                                    elif key == b'M':  # →
                                        self.settings_ui.handle_key('right')
                                elif key == b'\x1b':  # Esc
                                    if self._pending_confirm_exit:
                                        # 二次确认：直接退出，放弃修改
                                        self.in_settings = False
                                        self._pending_confirm_exit = False
                                        self.trader._add_action("[OK] 已放弃修改并退出", "")
                                    else:
                                        result = self.settings_ui.handle_key('escape')
                                        if result == 'exit':
                                            self.in_settings = False
                                            self.trader._add_action("[OK] 已退出设置", "")
                                        elif result == 'save':
                                            success, errors = self.settings_ui.save_config()
                                            if success:
                                                self._apply_settings()
                                                self.in_settings = False
                                            else:
                                                for err in errors:
                                                    self.trader._add_action("⚠️ 配置错误", err)
                                        elif result == 'confirm_exit':
                                            self._pending_confirm_exit = True
                                            self.trader._add_action("⚠️ 有未保存的修改", "再按 Esc 放弃修改退出")
                                elif key_char == 's':
                                    # S 保存并退出
                                    success, errors = self.settings_ui.save_config()
                                    if success:
                                        self._apply_settings()
                                        self.in_settings = False
                                        self._pending_confirm_exit = False
                                        self._pending_reset = False
                                    else:
                                        for err in errors:
                                            self.trader._add_action("⚠️ 配置错误", err)
                                            self.log_manager.system.error(f"设置错误：{err}")
                                        self._pending_confirm_exit = False
                                        self._pending_reset = False
                                else:
                                    # 先检查是否有待处理的确认
                                    if self._pending_reset and key_char == 'd':
                                        # 第二次按 D，直接执行重置
                                        self.config_manager.reset_to_defaults()
                                        self._apply_settings()
                                        self.trader._add_action("[OK] 配置已重置为默认值", "")
                                        self._pending_reset = False
                                        continue
                                    
                                    # 其他按键交给设置界面处理
                                    result = self.settings_ui.handle_key(key_char)
                                    if result == 'exit':
                                        self.in_settings = False
                                        self.trader._add_action("[OK] 已退出设置", "")
                                    elif result == 'save':
                                        success, errors = self.settings_ui.save_config()
                                        if success:
                                            self._apply_settings()
                                            self.in_settings = False
                                        else:
                                            for err in errors:
                                                self.trader._add_action("⚠️ 配置错误", err)
                                    elif result == 'confirm_exit':
                                        self.trader._add_action("⚠️ 有未保存的修改", "按 S 保存退出 或 再按 Esc 放弃")
                                    elif result == 'reset_confirm':
                                        # 第一次按 D，显示提示
                                        self._pending_reset = True
                                        self.trader._add_action("⚠️ 确认重置", "再次按 D 确认重置为默认值")
                                    elif result == 'backed_up':
                                        backup_path = self.config_manager.backup_config()
                                        self.trader._add_action("[OK] 备份已创建", backup_path)
                                    elif result == 'restored':
                                        self.trader._add_action("[OK] 配置已恢复", "从备份恢复")
                                    elif result == 'deleted':
                                        self.trader._add_action("[OK] 备份已删除", "")
                                    elif result == 'enter_edit':
                                        self.trader._add_action("ℹ️ 编辑模式", "数字输入或←→调整，Enter 确认")
                                continue
                            
                            # 主交易界面 - 只用方向键
                            if key == b'\xe0' or key == b'\x00':
                                key = msvcrt.getch()
                                if key in (b'I', b'i', b'Q', b'q'):
                                    pass  # 鼠标滚轮，忽略
                                elif key == b'H':  # ↑
                                    await self.place_order('LONG')
                                elif key == b'P':  # ↓
                                    await self.place_order('SHORT')
                                elif key == b'M':  # →
                                    await self.close_position_early()
                                elif key == b'K':  # ←
                                    await self.cancel_order()
                            elif key_char == 's':
                                # 进入设置界面
                                if not self.try_enter_settings():
                                    pass  # 已在 try_enter_settings 中记录日志
                                else:
                                    self.trader._add_action("[OK] 已进入设置", "↑↓切换 Enter 编辑 S 保存退出")
                            elif key_char == 'z':
                                # Z 键：市价全平
                                if self.trader.position:
                                    self.trader._add_action("Z 键市价全平", "执行中...")
                                    await self.trader.close_position_market()
                                else:
                                    self.trader._add_action("⚠️ Z 键无效", "无持仓")
                            elif key_char == 'h':
                                # 手动触发持仓同步
                                await self.trader.sync_position_from_exchange()
                            elif key_char == 'r':
                                # 重置脱敏基数（仅在脱敏开启时生效）
                                self.trader.reset_privacy_baseline()
                            elif key_char == 'q':
                                self.running = False
                    except Exception as e:
                        self.log_manager.system.error(f"键盘输入错误：{e}")
                    
                    # 成交检测：bookTicker 穿透 → REST 确认（零消耗 unless 穿透）
                    await self.trader.check_pending_order_filled()
                    await self.trader.check_batch_orders_filled()

                    # 关键价格检测：订单簿 vs 关键价格比对 → REST 确认平仓
                    await self.trader._check_key_prices()

                    # 节流：同步账户和持仓（避免每帧 REST 阻塞事件循环）
                    if not hasattr(self, '_sync_counter'):
                        self._sync_counter = 0
                    self._sync_counter += 1

                    # 有持仓或挂单：每 100 帧（~5 秒）同步账户 + 持仓
                    if (self.trader.pending_order or self.trader.position) and self._sync_counter % 100 == 0:
                        self.trader.sync_account()
                        self.indicators.set_trading_params(balance_usdt=float(self.trader.available_balance))
                        await self.trader.sync_position_from_exchange()
                    # 无持仓无挂单：每 300 帧（~15 秒）同步账户，每 600 帧（~30 秒）同步持仓
                    elif self._sync_counter % 300 == 0:
                        self.trader.sync_account()
                        self.indicators.set_trading_params(balance_usdt=float(self.trader.available_balance))
                    elif self._sync_counter % 600 == 0:
                        await self.trader.sync_position_from_exchange()
                    
                    await asyncio.sleep(0.05)
        
        except KeyboardInterrupt:
            self.log_manager.system.debug("用户中断（窗口关闭）")
            history_task.cancel()
            await self._cleanup_resources(ws_task)
        except Exception as e:
            self.log_manager.system.error(f"主循环异常：{e}", exc_info=True)
            history_task.cancel()
            await self._cleanup_resources(ws_task)
        finally:
            # 无论何种退出方式，都要记录余额日志
            self.log_manager.system.debug("正在同步账户信息...")
            self.trader.sync_account()
            self.log_manager.system.debug(f"当前余额：{self.trader.available_balance:.4f} USDC")

            # 输出数据记录统计
            stats = self.recorder.get_stats()
            self.log_manager.system.debug(f"数据记录统计 - K线保存：{stats['klines_saved']} 根，订单簿快照：{stats['orderbooks_saved']} 次")

            self.log_manager.system.debug("正在写入 shutdown 余额日志...")
            try:
                self.logger.log_balance('shutdown', self.trader.available_balance, {
                    'account': self.account_name,
                    'exit_type': 'finally_block'
                })
                self.log_manager.system.debug("shutdown 余额日志已写入")
            except Exception as e:
                self.log_manager.system.warning(f"shutdown 余额日志写入失败：{e}")

            # 刷新数据记录器缓存
            if hasattr(self, 'recorder') and self.recorder:
                self.recorder.flush_all()

            # 输出指标记录器统计
            if hasattr(self, 'metrics_recorder') and self.metrics_recorder:
                stats = self.metrics_recorder.get_stats()
                mr = self.metrics_recorder
                self.log_manager.system.debug(f"指标数据统计 - 已保存：{stats['records_saved']} 条快照，保存间隔：{mr.save_interval}秒")
        
        # 正常退出时也清理（Q 键退出时 running=False，但仍需清理）
        if not self.running:
            await self._cleanup_resources(ws_task)
        
        # 关闭日志系统
        self.logger.close()
        if hasattr(self, 'log_manager') and self.log_manager:
            self.log_manager.close()
            self.log_manager.system.debug(f"交易结束，最终余额：{self.trader.available_balance:.4f} USDC")
        
        print("\n" + "=" * 70)
        print("交易结束")
        print(f"最终余额：{self.trader.available_balance:.4f} USDC")
        if hasattr(self, 'log_manager') and self.log_manager:
            print(f"日志已保存至：{self.log_manager.log_dir}")
        print("=" * 70)
        
        # 防止窗口立即关闭
        print("\n按回车键退出...")
        try:
            input()
        except:
            pass
        print(f"最终余额：{self.trader.available_balance:.4f} USDC")
        print("=" * 70)


def load_account(account_name: str = None):
    """加载账号配置"""
    import json
    config_file = Path(__file__).parent.parent / "config" / "accounts.json"
    
    if not config_file.exists():
        print("[ERROR] 未找到 config/accounts.json")
        print("请先配置 API Key")
        return None, None, None
    
    with open(config_file, encoding='utf-8') as f:
        config = json.load(f)
    
    accounts = config.get('accounts', [])
    if not accounts:
        print("[ERROR] 未配置账号")
        return None, None, None
    
    # 根据账户名称查找
    if account_name:
        for acc in accounts:
            if acc['name'] == account_name:
                return acc['api_key'], acc['api_secret'], acc['name']
        print(f"[ERROR] 未找到账户 '{account_name}'")
        return None, None, None
    
    # 使用默认账号或第一个账号
    default_name = config.get('settings', {}).get('default_account')
    for acc in accounts:
        if acc['name'] == default_name:
            return acc['api_key'], acc['api_secret'], acc['name']
    
    return accounts[0]['api_key'], accounts[0]['api_secret'], accounts[0]['name']


async def main(account_name: str = None):
    # 加载账号
    api_key, api_secret, name = load_account(account_name)
    if not api_key:
        return
    
    print(f"\n使用账号：{name}")
    print(f"交易对：{SYMBOL}")
    print()
    
    # 创建并运行机器人
    try:
        bot = LiveTradingBot(api_key, api_secret, name)
    except Exception as e:
        print(f"\n[ERROR] 机器人初始化失败：{e}")
        return
    await bot.run()


if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description=f'py-shortqt v{__version__} 实盘交易')
    parser.add_argument('--account', type=str, default=None, help='账户名称（从 config/accounts.json 中选择）')
    args = parser.parse_args()
    
    try:
        # 添加崩溃日志
        import traceback
        import sys
        
        def log_exception(exc_type, exc_value, exc_traceback):
            with open('crash.log', 'w', encoding='utf-8') as f:
                f.write(f"崩溃时间：{datetime.now()}\n")
                f.write(f"异常类型：{exc_type.__name__}\n")
                f.write(f"异常消息：{exc_value}\n")
                f.write("\n堆栈跟踪:\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)
        
        sys.excepthook = log_exception
        
        asyncio.run(main(args.account))
    except KeyboardInterrupt:
        print("\n已中断")



