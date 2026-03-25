#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
历史数据收集 CLI 脚本

独立运行，用于定时收集盘面数据（即使不交易也收集）

用法：
    # 收集过去 14 天 K 线
    python collect_data.py --mode collect --days 14
    
    # 持续记录订单簿（后台运行）
    python collect_data.py --mode record --interval 300
    
    # 生成统计报告
    python collect_data.py --mode analyze --days 7

定时任务（Windows 任务计划程序）：
    K 线同步：每小时运行一次 --mode collect --days 1
    订单簿记录：每 5 分钟运行一次 --mode record --interval 300
    统计分析：每周日凌晨 --mode analyze --days 7
"""

import sys
import argparse
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.data_collector import (
    collect_historical_data,
    start_orderbook_recording,
    analyze_klines,
    DEFAULT_SYMBOLS,
    HISTORY_DAYS,
    ORDERBOOK_INTERVAL
)

def main():
    parser = argparse.ArgumentParser(
        description='历史数据收集 CLI 脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        '--symbols',
        nargs='+',
        default=DEFAULT_SYMBOLS,
        help=f'交易对列表（默认：{DEFAULT_SYMBOLS}）'
    )
    
    parser.add_argument(
        '--days',
        type=int,
        default=HISTORY_DAYS,
        help=f'历史数据天数（默认：{HISTORY_DAYS}）'
    )
    
    parser.add_argument(
        '--mode',
        choices=['collect', 'record', 'analyze'],
        default='collect',
        help='运行模式（默认：collect）'
    )
    
    parser.add_argument(
        '--interval',
        type=int,
        default=ORDERBOOK_INTERVAL,
        help=f'订单簿记录间隔（秒，默认：{ORDERBOOK_INTERVAL}）'
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("py-shortqt 历史数据收集工具")
    print("=" * 60)
    print(f"模式：{args.mode}")
    print(f"交易对：{', '.join(args.symbols)}")
    print(f"天数：{args.days}")
    print("=" * 60)
    
    if args.mode == 'collect':
        collect_historical_data(args.symbols, args.days)
    
    elif args.mode == 'record':
        print(f"\n开始记录订单簿（{args.interval}秒/次）")
        print("按 Ctrl+C 停止...\n")
        try:
            start_orderbook_recording(args.symbols, args.interval)
        except KeyboardInterrupt:
            print("\n[OK] 订单簿记录已停止")
    
    elif args.mode == 'analyze':
        for symbol in args.symbols:
            analyze_klines(symbol, args.days)
    
    print("\n[OK] 任务完成")

if __name__ == "__main__":
    main()
