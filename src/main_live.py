# -*- coding: utf-8 -*-
"""
实盘交易主入口 - v1.2.0
支持 TUI 设置模块
"""

import asyncio
import sys
import os
import argparse
from pathlib import Path
from decimal import Decimal
from datetime import datetime

# 设置 UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    os.environ['PYTHONUTF8'] = '1'

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import SYMBOL, LEVERAGE_LIMIT, TESTNET
from src.api.binance_client import BinanceAPIError
from src.trading.live import LiveTrader
from src.websocket import BinanceListener
from src.ui.live_ui import LiveTradingUI
from src.ui.settings_ui import SettingsUI
from src.logger import TradeLogger
from src.system_logger import get_system_logger
from src.config.manager import ConfigManager

try:
    from rich.live import Live
    import msvcrt
except ImportError as e:
    print(f"缺少依赖库：{e}")
    print("请运行：pip install -r requirements.txt")
    sys.exit(1)


class LiveTradingBot:
    """实盘交易机器人 - v1.2.0"""
    
    def __init__(self, api_key: str, api_secret: str, account_name: str = "主账号"):
        self.symbol = SYMBOL
        self.running = True
        self.in_settings = False  # 是否在设置界面中
        
        # 获取系统日志
        self.sys_logger = get_system_logger()
        self.sys_logger.info(f"=== py-shortqt v1.2.0 启动 ===")
        self.sys_logger.info(f"使用账户：{account_name}")
        
        # 初始化交易日志（项目根目录/logs/）
        project_root = Path(__file__).parent.parent
        self.logger = TradeLogger(project_root / "logs")
        
        # 初始化配置管理器
        self.config_manager = ConfigManager(project_root / "config" / "runtime.json")
        
        # 获取杠杆配置
        api_lev, actual_lev = self.config_manager.get_leverage_config()
        
        # 初始化实盘交易器
        self.trader = LiveTrader(
            api_key=api_key,
            api_secret=api_secret,
            symbol=self.symbol,
            leverage_limit=api_lev,  # 使用配置中的 API 杠杆
            actual_leverage=actual_lev,  # 使用配置中的实际杠杆
            testnet=TESTNET,
            logger=self.logger,
            config_manager=self.config_manager  # 传入配置管理器
        )
        
        # 初始化行情 WebSocket
        self.listener = BinanceListener(self.symbol.lower(), "wss://fstream.binance.com/ws")
        self.listener.add_callback(self.on_market_data)
        
        # UI
        tp = self.config_manager.get_take_profit_price(Decimal('2150'))
        self.ui = LiveTradingUI(self.trader, api_lev, tp, Decimal('3'), actual_lev)
        
        # 设置 UI
        self.settings_ui = SettingsUI(self.config_manager, self.trader)
        
        self.sys_logger.info(f"交易对：{self.symbol}, API 杠杆：{api_lev}x, 实际杠杆：{actual_lev}x")
    
    async def on_market_data(self, event_type: str, data: dict):
        """市场数据回调"""
        try:
            if event_type == 'ticker':
                price = data['price']
                self.trader.update_price(price)
                
                # 记录价格
                self.logger.record_price(price)
            
            elif event_type == 'depth':
                bids = data.get('bids', [])
                asks = data.get('asks', [])
                self.trader.update_orderbook(bids, asks)
        except Exception:
            pass
    
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
    
    async def run(self):
        """运行主循环"""
        print("=" * 70)
        print("py-shortqt v1.1.1 - 实盘交易模式")
        print("=" * 70)
        
        # 1. 初始化实盘连接
        if not await self.trader.initialize():
            print("\n✗ 实盘初始化失败，退出")
            return
        
        # 2. 连接行情 WebSocket
        print("连接行情 WebSocket...")
        ws_task = asyncio.create_task(self.listener.connect())
        
        # 等待行情连接（最多 10 秒）
        print("等待连接...")
        for i in range(20):  # 10 秒 = 20 * 0.5 秒
            if self.listener.connected:
                print("✓ 行情已连接")
                break
            await asyncio.sleep(0.5)
        else:
            print("✗ 行情连接超时，退出")
            self.listener.running = False
            return
        
        print("=" * 70)
        print("操作：↑/W 做多  |  ↓/A 做空  |  ←撤单  |  →/D 平仓  |  S 设置  |  Q 退出")
        print("=" * 70)
        
        # 3. 主循环
        print("\n进入主循环...")
        try:
            # 使用自适应高度（不强制全屏）
            with Live(
                self.ui.render(),
                refresh_per_second=10,
                screen=False  # 不使用全屏，自适应终端高度
            ) as live:
                while self.running:
                    try:
                        if self.in_settings:
                            live.update(self.settings_ui.render())
                        else:
                            live.update(self.ui.render())
                    except Exception as e:
                        print(f"\n[UI 更新错误] {e}")
                        continue
                    
                    # 键盘输入
                    if msvcrt.kbhit():
                        try:
                            key = msvcrt.getch()
                            
                            # 在设置界面中
                            if self.in_settings:
                                if key == b'\xe0' or key == b'\x00':
                                    key = msvcrt.getch()
                                    if key == b'H':  # ↑
                                        self.settings_ui.handle_key('up')
                                    elif key == b'P':  # ↓
                                        self.settings_ui.handle_key('down')
                                    elif key == b'K':  # ←
                                        self.settings_ui.handle_key('left')
                                    elif key == b'M':  # →
                                        self.settings_ui.handle_key('right')
                                elif key.lower() == b's':
                                    # 退出设置
                                    self.in_settings = False
                                elif key.lower() == b'q':
                                    # 退出设置
                                    self.in_settings = False
                                else:
                                    # 其他按键交给设置界面处理
                                    result = self.settings_ui.handle_key(key.decode('gbk', errors='ignore').lower())
                                    if result == 'exit':
                                        self.in_settings = False
                                    elif result == 'save':
                                        # 保存配置
                                        success, errors = self.settings_ui.save_config()
                                        if success:
                                            self.trader._add_action("✓ 配置已保存", "")
                                        else:
                                            for err in errors:
                                                self.trader._add_action("⚠️ 配置错误", err)
                                        self.in_settings = False
                                    elif result == 'cancel':
                                        self.in_settings = False
                                    elif result == 'reset_confirm':
                                        # TODO: 显示确认对话框
                                        pass
                                continue
                            
                            # 主交易界面
                            if key == b'\xe0' or key == b'\x00':
                                key = msvcrt.getch()
                                if key == b'H':  # ↑
                                    await self.place_order('LONG')
                                elif key == b'P':  # ↓
                                    await self.place_order('SHORT')
                                elif key == b'M':  # →
                                    await self.close_position_early()
                                elif key == b'K':  # ←
                                    await self.cancel_order()
                            elif key.lower() == b'w':
                                await self.place_order('LONG')
                            elif key.lower() == b'a':
                                await self.place_order('SHORT')  # A 键开空
                            elif key.lower() == b's':
                                # 进入设置界面
                                if not self.try_enter_settings():
                                    pass  # 已在 try_enter_settings 中记录日志
                            elif key.lower() == b'd':
                                await self.close_position_early()  # D 键提前平仓
                            elif key.lower() == b'q':
                                await self.cancel_order()
                            elif key.upper() == b'Q':
                                self.running = False
                        except Exception as e:
                            print(f"\n[键盘输入错误] {e}")
                    
                    # 同步账户信息
                    self.trader.sync_account()
                    
                    await asyncio.sleep(0.05)
        
        except KeyboardInterrupt:
            print("\n用户中断")
        except Exception as e:
            print(f"\n[主循环异常] {e}")
            import traceback
            traceback.print_exc()
        
        # 4. 清理
        print("\n清理资源...")
        self.running = False
        self.listener.running = False
        await self.trader.cleanup()
        
        # 等待 WebSocket 任务结束（最多 2 秒）
        try:
            await asyncio.wait_for(ws_task, timeout=2.0)
        except asyncio.TimeoutError:
            print("WebSocket 任务超时，强制结束")
        
        self.logger.close()
        
        print("\n" + "=" * 70)
        print("交易结束")
        print(f"最终余额：{self.trader.available_balance:.4f} USDC")
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
        print("✗ 未找到 config/accounts.json")
        print("请先配置 API Key")
        return None, None, None
    
    with open(config_file, encoding='utf-8') as f:
        config = json.load(f)
    
    accounts = config.get('accounts', [])
    if not accounts:
        print("✗ 未配置账号")
        return None, None, None
    
    # 根据账户名称查找
    if account_name:
        for acc in accounts:
            if acc['name'] == account_name:
                return acc['api_key'], acc['api_secret'], acc['name']
        print(f"✗ 未找到账户 '{account_name}'")
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
    parser = argparse.ArgumentParser(description='py-shortqt v1.2.0 实盘交易')
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
