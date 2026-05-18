# -*- coding: utf-8 -*-
"""
历史数据收集模块

功能：
- 启动时自动补全缺失的 K 线数据（过去 14 天）
- 补全订单簿历史快照
- 支持多交易对
- API 限流保护
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from decimal import Decimal
from typing import List, Dict, Optional

# ==================== 配置 ====================

# 数据存放目录（项目根目录/data）
DATA_DIR = Path(__file__).parent.parent / "data"
KLINES_DIR = DATA_DIR / "klines"
ORDERBOOK_DIR = DATA_DIR / "orderbook"

# 币安 API
BINANCE_API = "https://fapi.binance.com"
KLINES_ENDPOINT = "/fapi/v1/klines"
DEPTH_ENDPOINT = "/fapi/v1/depth"

# 请求限制
RATE_LIMIT = 1200  # 权重/分钟
WEIGHT_PER_KLINES = 2  # 500 根 K 线权重 2
WEIGHT_PER_DEPTH = 5  # 深度查询权重 5

# 默认参数
DEFAULT_SYMBOLS = ["ETHUSDC"]  # 默认交易对列表
HISTORY_DAYS = 14  # 历史数据天数
KLINES_LIMIT = 500  # 每次获取 500 根 K 线
ORDERBOOK_LIMIT = 200  # 订单簿 200 档深度
ORDERBOOK_INTERVAL = 300  # 订单簿快照间隔（秒）= 5 分钟

# ==================== 工具函数 ====================

def ensure_dirs():
    """确保目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    KLINES_DIR.mkdir(parents=True, exist_ok=True)
    ORDERBOOK_DIR.mkdir(parents=True, exist_ok=True)

def get_symbol_dir(base_dir: Path, symbol: str) -> Path:
    """获取交易对目录"""
    symbol_dir = base_dir / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)
    return symbol_dir

def get_file_path(base_dir: Path, symbol: str, date_str: str) -> Path:
    """获取数据文件路径"""
    symbol_dir = get_symbol_dir(base_dir, symbol)
    return symbol_dir / f"{date_str}.jsonl"

def load_existing_data(file_path: Path) -> List:
    """加载已存在的数据"""
    if not file_path.exists():
        return []
    
    data = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                data.append(json.loads(line))
    return data

def save_data(file_path: Path, data: List, append: bool = True):
    """保存数据到 JSONL 文件"""
    mode = 'a' if append else 'w'
    with open(file_path, mode, encoding='utf-8') as f:
        for record in data:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

def rate_limit_sleep(last_request_time: float, weight: int) -> float:
    """根据权重计算需要等待的时间"""
    elapsed = time.time() - last_request_time
    min_interval = weight / (RATE_LIMIT / 60)  # 最小间隔（秒）
    
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    
    return time.time()

def get_date_from_timestamp(timestamp_ms: int) -> str:
    """从毫秒时间戳获取日期字符串"""
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d")

# ==================== K 线数据获取 ====================

def fetch_klines(symbol: str, start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = KLINES_LIMIT) -> List[Dict]:
    """
    获取 K 线数据

    Args:
        symbol: 交易对
        start_time: 开始时间（毫秒）
        end_time: 结束时间（毫秒），用于过滤超出当天的 K 线
        limit: 每次获取数量（最多 1500）

    Returns:
        K 线数据列表
    """
    params = {
        'symbol': symbol,
        'interval': '1m',
        'limit': limit
    }
    
    if start_time:
        params['startTime'] = start_time
    
    url = f"{BINANCE_API}{KLINES_ENDPOINT}"
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"[ERR] K 线获取失败：{response.status_code} {response.text}")
        return []
    
    klines = response.json()
    
    # 转换为字典格式
    result = []
    for k in klines:
        result.append({
            'timestamp': k[0],
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5]),
            'turnover': float(k[6]),
            'trades': int(k[8]),
            'buy_volume': float(k[10]),
            'buy_turnover': float(k[11])
        })

    # 按 end_time 过滤，确保只保留当天的数据
    if end_time:
        result = [k for k in result if k['timestamp'] < end_time]

    return result

def get_last_timestamp(file_path: Path) -> Optional[int]:
    """获取文件中最后一条数据的时间戳"""
    if not file_path.exists():
        return None
    
    last_line = None
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                last_line = line
    
    if last_line:
        data = json.loads(last_line)
        return data.get('timestamp')
    
    return None

def fetch_missing_klines(symbol: str, days: int = HISTORY_DAYS) -> int:
    """
    获取缺失的 K 线数据（增量）
    
    Args:
        symbol: 交易对
        days: 获取天数
    
    Returns:
        获取的 K 线总数
    """
    print(f"\n[DATA] 开始获取 {symbol} 缺失的 K 线数据（{days}天）")
    print("=" * 60)
    
    ensure_dirs()
    
    now = datetime.now()
    # 从 N 天前的 00:00 开始，而不是从现在减去天数
    start_date = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # 按天获取
    current_date = start_date
    total_klines = 0
    last_request_time = 0
    
    # 一天应该有 1440 根 1 分钟 K 线
    EXPECTED_KLINES_PER_DAY = 1440
    
    while current_date <= now:
        date_str = current_date.strftime("%Y-%m-%d")
        file_path = get_file_path(KLINES_DIR, symbol, date_str)

        # v1.5.5 修复：先定义这些变量（移到if外面，避免文件不存在时报错）
        day_start_ms = int(current_date.timestamp() * 1000)
        day_end_ms = int((current_date + timedelta(days=1)).timestamp() * 1000)
        is_today = (current_date.date() == now.date())
        existing_data = []
        existing_count = 0

        # 检查是否已存在且完整
        if file_path.exists():
            existing_data = load_existing_data(file_path)

            # v1.5.5 修复：清洗跨日期数据（旧Bug导致的污染）
            # 只保留当天时间范围内的K线
            cleaned_data = [k for k in existing_data if day_start_ms <= k['timestamp'] < day_end_ms]
            if len(cleaned_data) != len(existing_data):
                removed_count = len(existing_data) - len(cleaned_data)
                print(f"[CLEAN] {date_str}: 清洗掉 {removed_count} 根跨日期污染数据")
                # 重新写入清洗后的数据
                save_data(file_path, cleaned_data, append=False)
                existing_data = cleaned_data

            existing_count = len(existing_data)

            # 判断是否需要补全
            is_complete = existing_count >= EXPECTED_KLINES_PER_DAY

            if not is_today and is_complete:
                print(f"[OK] {date_str}: 已完整 ({existing_count}根)")
                current_date += timedelta(days=1)
                continue
            elif not is_today and existing_count > 0:
                print(f"[WARN] {date_str}: 数据不完整 ({existing_count}/{EXPECTED_KLINES_PER_DAY}根)，需要补全")
            elif is_today:
                print(f"[INFO] {date_str}: 今天的数据 ({existing_count}根)，检查是否需要补全")
            else:
                print(f"[WARN] {date_str}: 无数据")
        else:
            print(f"[WARN] {date_str}: 文件不存在，需要创建")

        # 计算当天起始时间
        start_time = day_start_ms
        end_time = day_end_ms

        # 如果是今天，end_time 设为当前时间
        if is_today:
            end_time = int(now.timestamp() * 1000)

        # 检查已有数据，获取最后一条 K 线的时间
        last_timestamp = None
        if existing_data:
            last_timestamp = existing_data[-1].get('timestamp')
            print(f"[INFO] {date_str}: 最后一条 K 线时间：{datetime.fromtimestamp(last_timestamp/1000)}")
        
        # 获取缺失的 K 线
        day_klines = []
        current_start = last_timestamp + 60000 if last_timestamp else start_time
        
        # 如果已经有完整数据，跳过
        if current_start >= end_time:
            print(f"[OK] {date_str}: 数据已完整")
            current_date += timedelta(days=1)
            continue
        
        # 循环获取缺失的 K 线
        while current_start < end_time:
            # API 限流
            last_request_time = rate_limit_sleep(last_request_time, WEIGHT_PER_KLINES)
            
            klines = fetch_klines(symbol, start_time=current_start, end_time=end_time, limit=KLINES_LIMIT)
            
            if not klines:
                break
            
            day_klines.extend(klines)
            
            # 更新起始时间
            current_start = klines[-1]['timestamp'] + 60000  # +1 分钟
            
            # 避免重复
            if len(klines) < KLINES_LIMIT:
                break
            
            time.sleep(0.1)  # 小延迟
        
        # 保存数据
        if day_klines:
            # 如果文件已存在，追加模式；否则写入模式
            append_mode = file_path.exists()
            save_data(file_path, day_klines, append=append_mode)
            total_klines += len(day_klines)
            print(f"[OK] {date_str}: 补全 {len(day_klines)}根 (总计：{existing_count + len(day_klines) if file_path.exists() else len(day_klines)}根)")
        else:
            print(f"[INFO] {date_str}: 无需补全")
        
        current_date += timedelta(days=1)
    
    print("=" * 60)
    print(f"[OK] K 线获取完成：共 {total_klines}根")
    return total_klines

# ==================== 订单簿数据获取 ====================

def fetch_orderbook(symbol: str, limit: int = ORDERBOOK_LIMIT) -> Optional[Dict]:
    """
    获取订单簿快照
    
    Args:
        symbol: 交易对
        limit: 深度档位
    
    Returns:
        订单簿数据
    """
    params = {
        'symbol': symbol,
        'limit': limit
    }
    
    url = f"{BINANCE_API}{DEPTH_ENDPOINT}"
    response = requests.get(url, params=params)
    
    if response.status_code != 200:
        print(f"[ERR] 订单簿获取失败：{response.status_code}")
        return None
    
    data = response.json()
    
    return {
        'timestamp': int(time.time() * 1000),
        'bids': data.get('bids', []),  # 保持原始精度（价格 8 位，数量 8 位）
        'asks': data.get('asks', [])
    }

def fetch_missing_orderbook_snapshots(symbol: str, days: int = HISTORY_DAYS, interval: int = ORDERBOOK_INTERVAL) -> int:
    """
    获取缺失的订单簿快照（简化版：只获取当前快照）
    
    注意：历史订单簿无法通过 API 获取，只能从当前时间点开始记录
    这个函数用于初始化目录结构和配置文件
    
    Args:
        symbol: 交易对
        days: 天数（用于创建目录）
        interval: 记录间隔（秒）
    
    Returns:
        获取的快照数（当前为 0，仅初始化）
    """
    print(f"\n[DATA] 初始化 {symbol} 订单簿数据目录")
    print("=" * 60)
    
    ensure_dirs()
    
    # 创建交易对目录
    symbol_dir = get_symbol_dir(ORDERBOOK_DIR, symbol)
    
    # 创建配置文件
    config_file = symbol_dir / "config.json"
    config = {
        'symbol': symbol,
        'interval_seconds': interval,
        'depth_levels': ORDERBOOK_LIMIT,
        'created_at': datetime.now().isoformat()
    }
    
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print(f"[OK] 订单簿目录已初始化：{symbol_dir}")
    print(f"[OK] 配置文件：{config_file}")
    print("=" * 60)
    
    return 0

# ==================== 统计分析 ====================

def analyze_klines(symbol: str, days: int = 7) -> Dict:
    """
    分析 K 线数据，生成统计报告
    
    Args:
        symbol: 交易对
        days: 分析天数
    
    Returns:
        统计报告字典
    """
    print(f"\n[STATS] 开始分析 {symbol} 数据（{days}天）")
    print("=" * 60)
    
    now = datetime.now()
    start_date = now - timedelta(days=days)
    
    all_klines = []
    
    # 读取所有 K 线
    current_date = start_date
    while current_date <= now:
        date_str = current_date.strftime("%Y-%m-%d")
        file_path = get_file_path(KLINES_DIR, symbol, date_str)
        
        if file_path.exists():
            klines = load_existing_data(file_path)
            all_klines.extend(klines)
        
        current_date += timedelta(days=1)
    
    if not all_klines:
        print("[ERR] 没有数据可分析")
        return {}
    
    # 计算波动率指标
    amplitudes = []
    for k in all_klines:
        if k['open'] > 0:
            amplitude = (k['high'] - k['low']) / k['open'] * 100
            amplitudes.append(amplitude)
    
    # 统计分位数
    amplitudes.sort()
    n = len(amplitudes)
    
    stats = {
        'total_klines': n,
        '1min_amplitude': {
            'p10': amplitudes[int(n * 0.1)] if n > 0 else 0,
            'p25': amplitudes[int(n * 0.25)] if n > 0 else 0,
            'p50': amplitudes[int(n * 0.5)] if n > 0 else 0,
            'p75': amplitudes[int(n * 0.75)] if n > 0 else 0,
            'p90': amplitudes[int(n * 0.9)] if n > 0 else 0,
        },
        'recommendation': {
            'low_threshold': amplitudes[int(n * 0.25)] if n > 0 else 0.03,
            'normal_min': amplitudes[int(n * 0.4)] if n > 0 else 0.05,
            'normal_max': amplitudes[int(n * 0.6)] if n > 0 else 0.15,
            'high_threshold': amplitudes[int(n * 0.75)] if n > 0 else 0.3,
        }
    }
    
    # 保存统计报告
    analysis_dir = DATA_DIR / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    
    report_file = analysis_dir / f"klines_stats_{symbol}_{datetime.now().strftime('%Y%m%d')}.json"
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    
    print(f"[OK] 统计报告已保存：{report_file}")
    print(f"\n波动率阈值建议：")
    print(f"  低波动：< {stats['recommendation']['low_threshold']:.3f}%")
    print(f"  正常：  {stats['recommendation']['normal_min']:.3f}% - {stats['recommendation']['normal_max']:.3f}%")
    print(f"  高波动：> {stats['recommendation']['high_threshold']:.3f}%")
    
    return stats

# ==================== 主入口 ====================

def collect_historical_data(symbols: List[str] = None, days: int = HISTORY_DAYS):
    """
    收集历史数据（启动时调用）
    
    Args:
        symbols: 交易对列表
        days: 历史天数
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    
    print("\n" + "=" * 60)
    print("[DATA] 开始收集历史数据")
    print("=" * 60)
    
    total_klines = 0
    
    for symbol in symbols:
        # 获取 K 线
        klines_count = fetch_missing_klines(symbol, days)
        total_klines += klines_count
        
        # 初始化订单簿目录
        fetch_missing_orderbook_snapshots(symbol, days)
    
    print("\n" + "=" * 60)
    print(f"[OK] 历史数据收集完成：共 {total_klines}根 K 线")
    print("=" * 60)

def start_orderbook_recording(symbols: List[str] = None, interval: int = ORDERBOOK_INTERVAL):
    """
    启动订单簿定时记录（后台任务）
    
    Args:
        symbols: 交易对列表
        interval: 记录间隔（秒）
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS
    
    print(f"\n[DATA] 启动订单簿定时记录（{interval}秒/次）")
    last_request_time = 0
    
    while True:
        for symbol in symbols:
            # API 限流
            last_request_time = rate_limit_sleep(last_request_time, WEIGHT_PER_DEPTH)
            
            # 获取订单簿
            orderbook = fetch_orderbook(symbol, limit=ORDERBOOK_LIMIT)
            
            if orderbook:
                date_str = datetime.now().strftime("%Y-%m-%d")
                file_path = get_file_path(ORDERBOOK_DIR, symbol, date_str)
                save_data(file_path, [orderbook], append=True)
                print(f"[OK] {datetime.now().strftime('%H:%M:%S')} {symbol}: 记录订单簿")
        
        time.sleep(interval)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='历史数据收集脚本')
    parser.add_argument('--symbols', nargs='+', default=DEFAULT_SYMBOLS, help='交易对列表')
    parser.add_argument('--days', type=int, default=HISTORY_DAYS, help='历史天数')
    parser.add_argument('--mode', choices=['collect', 'record', 'analyze'], default='collect', help='模式')
    parser.add_argument('--interval', type=int, default=ORDERBOOK_INTERVAL, help='订单簿记录间隔（秒）')
    
    args = parser.parse_args()
    
    if args.mode == 'collect':
        collect_historical_data(args.symbols, args.days)
    elif args.mode == 'record':
        start_orderbook_recording(args.symbols, args.interval)
    elif args.mode == 'analyze':
        for symbol in args.symbols:
            analyze_klines(symbol, args.days)
