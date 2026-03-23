# -*- coding: utf-8 -*-
"""
日志系统测试脚本
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.loggers import get_logger
from decimal import Decimal


def test_logging():
    """测试日志系统"""
    print("=" * 70)
    print("py-shortqt v1.3 日志系统测试")
    print("=" * 70)
    print()
    
    # 初始化日志系统
    log_dir = project_root / "logs"
    log_manager = get_logger(log_dir, debug_mode=True)
    
    print(f"✓ 日志目录：{log_dir}")
    print(f"✓ 调试模式：True")
    print()
    
    # 测试系统日志
    print("1. 测试系统日志...")
    log_manager.system.debug("这是一条 DEBUG 日志")
    log_manager.system.info("这是一条 INFO 日志")
    log_manager.system.warning("这是一条 WARNING 日志")
    log_manager.system.error("这是一条 ERROR 日志")
    print("   ✓ 系统日志测试完成")
    print()
    
    # 测试市场日志
    print("2. 测试市场日志...")
    log_manager.market.log_orderbook(
        "ETHUSDC",
        bids=[[3456.78, 1.23], [3456.77, 2.34], [3456.76, 3.45]],
        asks=[[3456.79, 1.11], [3456.80, 2.22], [3456.81, 3.33]]
    )
    log_manager.market.log_trade("ETHUSDC", 3456.78, 0.5, "BUY")
    log_manager.market.log_signal(
        "BUY",
        3456.78,
        {
            'price_5s_change': 0.05,
            'price_10s_change': 0.12,
            'orderbook_imbalance': 0.35,
            'spread': 0.01
        }
    )
    log_manager.market.log_amplitude("ETHUSDC", "1m", 3460.12, 3455.23, 0.14, 3456.00, 3458.50)
    print("   ✓ 市场日志测试完成")
    print()
    
    # 测试交易日志
    print("3. 测试交易日志...")
    log_manager.trading.log_order_new("12345", "BUY", "LIMIT", 3456.78, 0.123, "LONG")
    log_manager.trading.log_order_filled("12345", 3456.75, 0.123, 0.0001, "USDC")
    log_manager.trading.log_position_open("LONG", 3456.75, 0.123, 25, 17.28)
    log_manager.trading.log_position_close("LONG", 3457.75, 0.123, 0.00035, 0.03, "TP", 3456.75, 25.0)
    log_manager.trading.log_signal_start(
        "BUY",
        3456.75,
        {
            'price_5s_change': 0.05,
            'price_10s_change': 0.12,
            'orderbook_imbalance': 0.35
        }
    )
    log_manager.trading.log_signal_result("TP", 0.00035, 25.0, 3457.75)
    print("   ✓ 交易日志测试完成")
    print()
    
    # 关闭日志
    log_manager.close()
    
    print("=" * 70)
    print("✓ 所有测试完成！")
    print()
    print("日志文件位置：")
    print(f"  {log_dir}")
    print()
    print("查看日志：")
    print(f"  python tools/view_logs.py --list")
    print(f"  python tools/view_logs.py --type trading --tail")
    print("=" * 70)


if __name__ == "__main__":
    test_logging()
