# -*- coding: utf-8 -*-
"""
实盘交易主入口 - v1.5.3
支持 TUI 设置模块
"""

import asyncio
import sys
import os
import argparse
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta

# 设置 UTF-8 和窗口尺寸
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    os.environ['PYTHONUTF8'] = '1'
    # 设置控制台窗口尺寸（130 列 x 50 行）
    os.system('mode con: cols=130 lines=50')

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

# 配置代理（REST API 和 WebSocket 都走本地代理）
os.environ['HTTP_PROXY'] = 'http://127.0.0.1:7890'
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:7890'

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
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
        
        # 兼容旧日志（保留给 LiveTrader 使用）
        self.logger = TradeLogger(project_root / "logs")
        
        # 初始化指标管理器（v1.4.0 新增）
        self.indicators = IndicatorsManager()
        # 设置交易参数（默认值，后续从配置同步）
        self.indicators.set_trading_params(tp_points=0.99, sl_points=3.99, leverage=50, balance_usdt=50.0)
        
        # 初始化市场日志记录器（v1.4.0 新增）
        self.market_logger = MarketLogger(project_root / "logs")
        
        # 初始化实时数据记录器（v1.5.3 改造：K 线改为定时 API 拉取）
        self.recorder = RealtimeRecorder(symbol=self.symbol, orderbook_interval=60)
        self.log_manager.system.info(f"实时数据记录器已初始化（K 线定时 API 拉取，订单簿间隔：{self.recorder.orderbook_interval}秒）")
        
        # 初始化指标数据记录器（v1.4.2 新增）
        self.metrics_recorder = MetricsRecorder(symbol=self.symbol, save_interval=30)  # 30 秒
        self.log_manager.system.info(f"指标数据记录器已初始化（每{self.metrics_recorder.save_interval}秒保存一次指标快照）")
        
        # 初始化配置管理器
        self.log_manager.system.info("正在初始化配置管理器...")
        self.config_manager = ConfigManager(project_root / "config" / "runtime.json")
        
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
            log_manager=self.log_manager  # v1.5.0 修复：直接传入 log_manager
        )
        
        # 初始化行情 WebSocket
        self.listener = BinanceListener(self.symbol.lower(), "wss://fstream.binance.com/ws")
        self.listener.add_callback(self.on_market_data)
        
        # 将 listener 赋值给 trader，用于 UI 显示连接状态
        self.trader.listener = self.listener
        
        # v1.5.3: 注入 api_client 并启动 K 线定时拉取
        self.recorder.api_client = self.trader.api
        # 新 K 线收盘回调 → 更新指标（替代不可靠的 WebSocket kline 事件）
        self.recorder.on_new_kline = self.indicators.update_kline
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
        """v1.5.4 改造：拉取当天全部 K 线，对比后写入"""
        try:
            from src.data_collector import KLINES_DIR
            
            # 计算今天 00:00 的毫秒时间戳
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            start_ms = int(today.timestamp() * 1000)
            
            # 计算当前分钟的起始时间戳（只写入已关闭的 K 线）
            now = datetime.now()
            current_minute_start = now.replace(second=0, microsecond=0)
            current_minute_ms = int(current_minute_start.timestamp() * 1000)
            
            self.log_manager.system.info(f'从 API 获取今日全部 K 线（{today.strftime("%Y-%m-%d")} 00:00 起）...')
            self.log_manager.system.info(f'当前时间：{now.strftime("%H:%M:%S")}，只写入 {current_minute_start.strftime("%H:%M")} 之前的已关闭 K 线')
            
            # 币安最多返回 1500 根，当天最多 1440 根，一次拉完
            klines = self.trader.api.get_klines(self.symbol, '1m', limit=1500)
            if not klines:
                self.log_manager.system.warning("历史 K 线获取为空，跳过初始化")
                return
            
            # 只保留今天的数据
            today_klines = [k for k in klines if k[0] >= start_ms]
            
            # 获取文件已有的最后一条时间戳（防重复）
            date_str = today.strftime("%Y-%m-%d")
            kline_file = KLINES_DIR / self.symbol / f"{date_str}.jsonl"
            last_ts = 0
            if kline_file.exists():
                with open(kline_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            last_ts = data.get('timestamp', 0)
            
            count = 0
            kline_file.parent.mkdir(parents=True, exist_ok=True)
            # 跳过最后一条（未关闭，等定时器拉取关闭后的数据）
            closed_klines = today_klines[:-1] if today_klines else []
            with open(kline_file, 'a', encoding='utf-8') as f:
                for k in closed_klines:
                    if len(k) < 11:
                        continue
                    ts = k[0]
                    if ts <= last_ts:
                        continue
                    
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
                    f.write(json.dumps(kline_data, ensure_ascii=False) + '\n')
                    count += 1
            
            # 加载到指标管理器（用于 TUI 显示，同样跳过最后一条未关闭的）
            for k in closed_klines:
                if len(k) < 6:
                    continue
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
            
            # 更新 recorder 的防重时间戳
            if closed_klines:
                self.recorder._last_kline_ts = closed_klines[-1][0]
            
            self.log_manager.system.info(f'历史 K 线初始化完成：写入 {count} 根，指标加载 {len(closed_klines)} 根（跳过最后一条未关闭）')
        
        except Exception as e:
            error_msg = f"历史 K 线初始化失败：{e}"
            self.log_manager.system.warning(error_msg)
            self.error_log.append({'time': datetime.now(), 'msg': str(e)})
    
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
                if self.trader.position and self.trader.trailing_stop_manager:
                    await self.trader.trailing_stop_manager.update_trailing_stop(price)
                
                # v1.5.0 修复：移除高频日志，避免 TUI 抖动
                # 只在 check_and_protect 内部记录关键日志
                if self.trader.position and self.trader.loss_protection_manager.enabled:
                    # 计算未实现盈亏
                    if self.trader.position['side'] == 'LONG':
                        pnl = (price - self.trader.position['entry_price']) * self.trader.position['size']
                    else:
                        pnl = (self.trader.position['entry_price'] - price) * self.trader.position['size']
                    
                    # 调用 check_and_protect（内部会记录必要日志）
                    await self.trader.loss_protection_manager.check_and_protect(price, pnl)
            
            elif event_type == 'depth':
                bids = data.get('bids', [])
                asks = data.get('asks', [])

                # 首次收到深度数据时打印日志
                if not hasattr(self, '_depth_received'):
                    self._depth_received = True
                    self.log_manager.system.info(f"[调试] 首次收到深度数据：bids={len(bids)}, asks={len(asks)}")

                self.trader.update_orderbook(bids, asks)

                # 复用已转换的深度数据（update_orderbook 已做 Decimal 转换）
                ob = self.trader.orderbook
                bids_decimal = [(b[0], b[1]) for b in ob['bids']]
                asks_decimal = [(a[0], a[1]) for a in ob['asks']]
                self.indicators.update_orderbook(bids_decimal, asks_decimal)
                self.recorder.save_orderbook(bids_decimal, asks_decimal)
            
            elif event_type == 'kline':
                # WebSocket kline 事件（用于 current_kline 跟踪）
                # 指标更新已由 recorder 的 API 拉取回调驱动
                if not data or data.get('close') is None:
                    return

                self.indicators.update_kline(data)
                
                # v1.4.1 新增：实时保存 K 线数据
                self.recorder.save_kline(data)
                
                # v1.4.2 新增：每 30 秒保存指标快照
                self.metrics_recorder.save_snapshot(self.indicators, self.trader)
        except Exception as e:
            error_msg = f"市场数据处理异常：{e}"
            self.log_manager.system.debug(error_msg)
            # 添加到错误日志列表（TUI 显示）- v1.5.0 修复：Decimal 转字符串
            self.error_log.append({'time': datetime.now(), 'msg': str(e)})
            if len(self.error_log) > 10:
                self.error_log.pop(0)
    
    async def place_order(self, side: str):
        """开仓"""
        await self.trader.open_position(side)
    
    async def cancel_order(self):
        """撤单"""
        # 如果有提前平仓单，撤销并恢复止盈止损
        if self.trader.early_close_order:
            self.trader.cancel_early_close()
        # 否则撤销开仓挂单
        elif self.trader.pending_order:
            self.trader.cancel_open_order()
    
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
        
        # 可以进入
        self.in_settings = True
        return True
    
    async def _cleanup_resources(self, ws_task=None):
        """清理资源（确保 WebSocket 正确关闭）"""
        self.log_manager.system.info("正在清理资源...")
        self.running = False
        self.listener.running = False

        # 关闭用户数据流 WebSocket
        await self.trader.cleanup()

        # 等待行情 WebSocket 任务结束
        if ws_task:
            try:
                await asyncio.wait_for(ws_task, timeout=2.0)
            except asyncio.TimeoutError:
                self.log_manager.system.warning("WebSocket 任务超时，强制结束")

        self.log_manager.system.info("资源已清理")
    
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
        for retry in range(max_retries):
            try:
                if await self.trader.initialize():
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
                        print("\n[ERROR] 实盘初始化失败，退出")
                        return
            except Exception as e:
                error_msg = str(e)
                if "too many requests" in error_msg.lower() or "banned" in error_msg.lower():
                    print(f"\n[ERROR] API 请求频率过高，IP 可能被限制")
                    print(f"错误：{e}")
                    print("\n建议：")
                    print("1. 等待 1-2 分钟让 IP 解封")
                    print("2. 检查是否有多个程序同时运行")
                    print("3. 使用 WebSocket 订阅代替轮询")
                    return
                if retry < max_retries - 1:
                    print(f"初始化错误：{e}，{retry + 1}/{max_retries}，5 秒后重试...")
                    await asyncio.sleep(5)
                else:
                    print(f"\n[ERROR] 实盘初始化失败：{e}，退出")
                    return
        
        # 2. 连接行情 WebSocket
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
        
        # 5. 启动历史持仓轮询（每 20 秒刷新一次）
        async def _history_poll():
            while self.running:
                await asyncio.sleep(20)
                try:
                    await self.trader.fetch_position_history()
                except Exception:
                    pass

        history_task = asyncio.create_task(_history_poll())

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
                                                self.trader._add_action("[OK] 配置已保存", "")
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
                                        msg = "[OK] 配置已保存并退出"
                                        self.trader._add_action(msg, "")
                                        self.log_manager.system.info(f"设置操作：{msg}")
                                        
                                        # 更新配置
                                        api_lev, actual_lev = self.config_manager.get_leverage_config()
                                        
                                        # 更新 UI 的杠杆显示
                                        tp = self.config_manager.get_take_profit_price(Decimal('2150'))
                                        self.ui = LiveTradingUI(self.trader, api_lev, tp, Decimal('3'), actual_lev, self.config_manager, self.indicators)
                                        
                                        # 更新 trader 的杠杆设置
                                        self.trader.leverage_limit = api_lev
                                        self.trader.actual_leverage = actual_lev
                                        
                                        self.log_manager.system.info(f"杠杆已更新：API={api_lev}x, 实际={actual_lev}x")
                                        
                                        self.in_settings = False
                                        self._pending_confirm_exit = False
                                        self._pending_reset = False  # 清除重置标志
                                    else:
                                        for err in errors:
                                            self.trader._add_action("⚠️ 配置错误", err)
                                            self.log_manager.system.error(f"设置错误：{err}")
                                        self._pending_confirm_exit = False
                                        self._pending_reset = False  # 清除重置标志
                                else:
                                    # 先检查是否有待处理的确认
                                    if self._pending_reset and key_char == 'd':
                                        # 第二次按 D，直接执行重置
                                        self.config_manager.reset_to_defaults()
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
                                            self.trader._add_action("[OK] 配置已保存并退出", "")
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
                            elif key_char == 'q':
                                self.running = False
                    except Exception as e:
                        self.log_manager.system.error(f"键盘输入错误：{e}")
                    
                    # 成交检测：bookTicker 穿透 → REST 确认（零消耗 unless 穿透）
                    await self.trader.check_pending_order_filled()

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
            self.log_manager.system.info("用户中断（窗口关闭）")
            history_task.cancel()
            await self._cleanup_resources(ws_task)
        except Exception as e:
            self.log_manager.system.error(f"主循环异常：{e}", exc_info=True)
            history_task.cancel()
            await self._cleanup_resources(ws_task)
        finally:
            # 无论何种退出方式，都要记录余额日志
            self.log_manager.system.info("正在同步账户信息...")
            self.trader.sync_account()
            self.log_manager.system.info(f"当前余额：{self.trader.available_balance:.4f} USDC")

            # 输出数据记录统计
            stats = self.recorder.get_stats()
            self.log_manager.system.info(f"数据记录统计 - K线保存：{stats['klines_saved']} 根，订单簿快照：{stats['orderbooks_saved']} 次")

            self.log_manager.system.info("正在写入 shutdown 余额日志...")
            try:
                self.logger.log_balance('shutdown', self.trader.available_balance, {
                    'account': self.account_name,
                    'exit_type': 'finally_block'
                })
                self.log_manager.system.info("shutdown 余额日志已写入")
            except Exception as e:
                self.log_manager.system.warning(f"shutdown 余额日志写入失败：{e}")

            # 刷新数据记录器缓存
            if hasattr(self, 'recorder') and self.recorder:
                self.recorder.flush_all()

            # 输出指标记录器统计
            if hasattr(self, 'metrics_recorder') and self.metrics_recorder:
                stats = self.metrics_recorder.get_stats()
                self.log_manager.system.info(f"指标数据统计 - 已保存：{stats['records_saved']} 条快照，保存间隔：{stats['save_interval']}秒，数据目录：{stats['data_dir']}")
        
        # 正常退出时也清理（Q 键退出时 running=False，但仍需清理）
        if not self.running:
            await self._cleanup_resources(ws_task)
        
        # 关闭日志系统
        self.logger.close()
        if hasattr(self, 'log_manager') and self.log_manager:
            self.log_manager.close()
            self.log_manager.system.info(f"交易结束，最终余额：{self.trader.available_balance:.4f} USDC")
        
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
    bot = LiveTradingBot(api_key, api_secret, name)
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



