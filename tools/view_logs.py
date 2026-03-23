# -*- coding: utf-8 -*-
"""
日志查看工具
用法：python tools/view_logs.py [--type TYPE] [--date DATE] [--limit N] [--tail]
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta


def get_log_dir():
    """获取日志目录"""
    return Path(__file__).parent.parent / "logs"


def list_logs(log_dir: Path):
    """列出所有日志文件"""
    print(f"\n日志目录：{log_dir}\n")
    print(f"{'文件名':<50} {'大小':>10} {'修改时间':>20}")
    print("-" * 85)
    
    for f in sorted(log_dir.glob("*.log")):
        size = f.stat().st_size
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{f.name:<50} {size:>10,}B {mtime:>20}")
    
    for f in sorted(log_dir.glob("*.jsonl")):
        size = f.stat().st_size
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{f.name:<50} {size:>10,}B {mtime:>20}")
    
    for f in sorted(log_dir.glob("*.csv")):
        size = f.stat().st_size
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{f.name:<50} {size:>10,}B {mtime:>20}")
    
    # 索引文件
    index_file = log_dir / "index.json"
    if index_file.exists():
        print(f"\n\n运行记录索引：{index_file}")
        with open(index_file, 'r', encoding='utf-8') as f:
            index_data = json.load(f)
        
        print(f"\n{'Run ID':<20} {'开始时间':<25} {'结束时间':<25}")
        print("-" * 70)
        for run_id, info in sorted(index_data.items(), key=lambda x: x[1].get('start_time', ''), reverse=True)[:10]:
            start = info.get('start_time', 'N/A')[:19].replace('T', ' ')
            end = info.get('end_time', '运行中')[:19].replace('T', ' ') if info.get('end_time') else '运行中'
            print(f"{run_id:<20} {start:<25} {end:<25}")


def tail_log(file_path: Path, lines: int = 50):
    """查看日志末尾 N 行"""
    if not file_path.exists():
        print(f"文件不存在：{file_path}")
        return
    
    with open(file_path, 'r', encoding='utf-8') as f:
        all_lines = f.readlines()
    
    print(f"\n=== {file_path.name} (最后 {lines} 行) ===\n")
    for line in all_lines[-lines:]:
        print(line.rstrip())


def filter_logs(file_path: Path, log_type: str = None, limit: int = 100):
    """过滤并查看日志"""
    if not file_path.exists():
        print(f"文件不存在：{file_path}")
        return
    
    count = 0
    print(f"\n=== {file_path.name} ===\n")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if count >= limit:
                print(f"\n... (已显示 {limit} 条，共 {count + 1} 条)")
                break
            
            line = line.strip()
            if not line:
                continue
            
            # JSONL 格式
            if file_path.suffix == '.jsonl':
                try:
                    data = json.loads(line)
                    
                    # 按类型过滤
                    if log_type and data.get('type') != log_type:
                        continue
                    
                    # 格式化输出
                    ts = data.get('ts', 'N/A')[:19].replace('T', ' ')
                    log_type = data.get('type', 'N/A')
                    
                    if log_type == 'ORDER_NEW':
                        print(f"{ts} [{log_type}] {data.get('side')} {data.get('order_type')} @ {data.get('price')} x {data.get('qty')}")
                    elif log_type == 'ORDER_FILLED':
                        print(f"{ts} [{log_type}] {data.get('side')} @ {data.get('avg_price')} x {data.get('filled_qty')} | PnL: {data.get('pnl', 'N/A')}")
                    elif log_type == 'POSITION_OPEN':
                        print(f"{ts} [{log_type}] {data.get('side')} @ {data.get('entry_price')} x {data.get('size')}")
                    elif log_type == 'POSITION_CLOSE':
                        print(f"{ts} [{log_type}] {data.get('side')} {data.get('reason')} | PnL: {data.get('pnl'):.6f} ({data.get('pnl_pct'):.2f}%)")
                    elif log_type == 'SIGNAL':
                        print(f"{ts} [{log_type}] {data.get('side')} @ {data.get('price')} | 特征：{json.dumps(data.get('features', {}), ensure_ascii=False)[:100]}")
                    elif log_type == 'BOOK':
                        bids = data.get('bids', [])
                        asks = data.get('asks', [])
                        spread = asks[0][0] - bids[0][0] if bids and asks else 0
                        print(f"{ts} [{log_type}] 买一={bids[0][0] if bids else 'N/A'} 卖一={asks[0][0] if asks else 'N/A'} 价差={spread:.2f}")
                    else:
                        print(f"{ts} [{log_type}] {json.dumps(data, ensure_ascii=False)[:200]}")
                    
                    count += 1
                
                except json.JSONDecodeError:
                    print(f"{line[:200]}")
                    count += 1
            
            # 普通日志格式
            else:
                print(line)
                count += 1


def main():
    parser = argparse.ArgumentParser(description='py-shortqt 日志查看工具')
    parser.add_argument('--type', '-t', choices=['system', 'market', 'trading', 'signals'],
                        help='日志类型')
    parser.add_argument('--date', '-d', type=str, 
                        help='日期 (YYYY-MM-DD)，默认今天')
    parser.add_argument('--limit', '-l', type=int, default=100,
                        help='显示条数限制')
    parser.add_argument('--tail', action='store_true',
                        help='查看末尾 N 行')
    parser.add_argument('--lines', '-n', type=int, default=50,
                        help='tail 模式显示的行数')
    parser.add_argument('--list', action='store_true',
                        help='列出所有日志文件')
    parser.add_argument('--filter', '-f', type=str,
                        help='按类型过滤 (ORDER_NEW, ORDER_FILLED, POSITION_CLOSE, etc.)')
    
    args = parser.parse_args()
    
    log_dir = get_log_dir()
    
    # 列出所有日志
    if args.list:
        list_logs(log_dir)
        return
    
    # 确定日期
    date = args.date or datetime.now().strftime("%Y-%m-%d")
    
    # 确定文件
    if args.type == 'system':
        file_path = log_dir / f"system_{date}.log"
    elif args.type == 'market':
        file_path = log_dir / f"market_{date}.jsonl"
    elif args.type == 'trading':
        file_path = log_dir / f"trading_{date}.jsonl"
    elif args.type == 'signals':
        file_path = log_dir / f"signals_{date}.csv"
    else:
        # 默认显示最近的交易日志
        trading_logs = sorted(log_dir.glob("trading_*.jsonl"), reverse=True)
        if trading_logs:
            file_path = trading_logs[0]
        else:
            print("未找到日志文件")
            return
    
    # 查看模式
    if args.tail:
        tail_log(file_path, args.lines)
    else:
        filter_logs(file_path, args.filter, args.limit)


if __name__ == '__main__':
    main()
