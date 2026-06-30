# -*- coding: utf-8 -*-
"""
一次性脚本：扫描并修复 kline 文件中 buy_turnover=0 的脏数据

用法：python scripts/fix_dirty_klines.py [--dry-run] [--date YYYY-MM-DD]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

KLINES_DIR = Path(__file__).parent.parent / "data" / "klines"
SYMBOL = "ETHUSDC"
FUTURES_API = "https://fapi.binance.com/fapi/v1/klines"


def fetch_klines_range(start_ms: int, end_ms: int) -> dict:
    """获取指定时间范围内的 K 线，返回 {ts: [kline_array]} 字典"""
    params = {
        "symbol": SYMBOL,
        "interval": "1m",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    resp = requests.get(FUTURES_API, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return {k[0]: k for k in data}


def process_file(file_path: Path, dry_run: bool = False):
    """处理单个 kline 文件"""
    if not file_path.exists():
        print(f"  文件不存在: {file_path}")
        return 0, 0

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if not lines:
        return 0, 0

    # 找出所有 buy_turnover=0 且 volume>0 的行
    bad_entries = []
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("buy_turnover", 0) <= 0 and d.get("volume", 0) > 0:
            bad_entries.append((i, d["timestamp"]))

    if not bad_entries:
        return 0, 0

    print(f"  发现 {len(bad_entries)} 条脏数据")

    # 分组：连续的脏数据一起请求
    groups = []
    group = [bad_entries[0]]
    for i in range(1, len(bad_entries)):
        prev_ts = bad_entries[i - 1][1]
        curr_ts = bad_entries[i][1]
        if curr_ts - prev_ts <= 180000:  # 3 分钟内的归为一组
            group.append(bad_entries[i])
        else:
            groups.append(group)
            group = [bad_entries[i]]
    groups.append(group)

    corrected = 0
    for group in groups:
        first_ts = group[0][1]
        last_ts = group[-1][1]
        print(f"    修复 {datetime.fromtimestamp(first_ts/1000)} ~ {datetime.fromtimestamp(last_ts/1000)} "
              f"({len(group)} 根) ...", end=" ")

        try:
            api_map = fetch_klines_range(first_ts - 60000, last_ts + 120000)
        except Exception as e:
            print(f"API 请求失败: {e}")
            continue

        group_corrected = 0
        for idx, ts in group:
            if ts in api_map:
                fk = api_map[ts]
                bt = float(fk[10]) if len(fk) > 10 else 0.0
                if bt > 0:
                    kd = {
                        "timestamp": fk[0],
                        "open": float(fk[1]),
                        "high": float(fk[2]),
                        "low": float(fk[3]),
                        "close": float(fk[4]),
                        "volume": float(fk[5]),
                        "turnover": float(fk[7]),
                        "trades": int(fk[8]),
                        "buy_volume": float(fk[9]),
                        "buy_turnover": float(fk[10]),
                    }
                    lines[idx] = json.dumps(kd, ensure_ascii=False) + "\n"
                    group_corrected += 1
            time.sleep(0.05)  # 微小延迟，避免 API 限流

        corrected += group_corrected
        print(f"修正 {group_corrected}/{len(group)}")

    if corrected > 0 and not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"  [OK] Written {corrected} corrections")

    return len(bad_entries), corrected


def main():
    parser = argparse.ArgumentParser(description="Fix dirty kline data (buy_turnover=0)")
    parser.add_argument("--dry-run", action="store_true", help="Check only, no write")
    parser.add_argument("--date", type=str, help="Specific date YYYY-MM-DD, default all")
    args = parser.parse_args()

    symbol_dir = KLINES_DIR / SYMBOL
    if not symbol_dir.exists():
        print(f"Kline dir not found: {symbol_dir}")
        sys.exit(1)

    if args.date:
        files = [symbol_dir / f"{args.date}.jsonl"]
    else:
        files = sorted(symbol_dir.glob("*.jsonl"))

    total_bad = 0
    total_corrected = 0
    for fp in files:
        print(f"{fp.name}:")
        bad, corr = process_file(fp, dry_run=args.dry_run)
        total_bad += bad
        total_corrected += corr

    print(f"\nTotal: {total_bad} dirty, corrected {total_corrected}")
    if args.dry_run:
        print("(dry-run, no changes made)")


if __name__ == "__main__":
    main()
