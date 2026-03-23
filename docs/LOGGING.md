# py-shortqt v1.3 日志系统文档

## 概述

v1.3 重构了日志系统，解决了以下问题：

1. **TUI 启动后控制台日志丢失** → 全部写入文件
2. **盘面数据记录不完整** → 新增 market.log 记录深度/价格/异动
3. **日志分散无法查询** → 按日期归档 + 日志索引 + 查看工具

## 日志文件结构

```
logs/
├── system_2026-03-23.log        # 系统运行日志（DEBUG 级别）
├── market_2026-03-23.jsonl      # 盘面数据（JSONL 格式）
├── trading_2026-03-23.jsonl     # 交易日志（订单/持仓/信号）
├── signals_2026-03-23.csv       # 信号特征与结果（CSV 格式）
└── index.json                   # 日志索引（便于查询）

src/
└── loggers/                     # 日志模块（避免与标准库 logging 冲突）
    ├── __init__.py
    ├── manager.py
    ├── system.py
    ├── market.py
    └── trading.py
```

## 日志级别

| 级别 | 控制台输出 | 文件记录 | 用途 |
|------|-----------|---------|------|
| `DEBUG` | ❌ | ✅ | 详细调试信息（订单簿、WS 消息） |
| `INFO` | ❌ | ✅ | 正常运行状态 |
| `WARNING` | ✅ | ✅ | 可恢复的异常 |
| `ERROR` | ✅ | ✅ | 需要关注的错误 |
| `CRITICAL` | ✅ | ✅ | 严重错误 |

## 配置

在 `config/settings.py` 中配置：

```python
# 日志配置
LOG_BASE_DIR = "logs"
LOG_DEBUG_MODE = False  # True=记录详细调试日志（订单簿、WS 消息等）
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

## 日志查看工具

### 列出所有日志文件

```bash
python tools/view_logs.py --list
```

### 查看最近的交易日志

```bash
python tools/view_logs.py
```

### 查看指定类型的日志

```bash
# 系统日志
python tools/view_logs.py --type system

# 市场日志
python tools/view_logs.py --type market

# 交易日志
python tools/view_logs.py --type trading

# 信号 CSV
python tools/view_logs.py --type signals
```

### 查看指定日期的日志

```bash
python tools/view_logs.py --type trading --date 2026-03-23
```

### 查看末尾 N 行

```bash
python tools/view_logs.py --type trading --tail --lines 50
```

### 过滤特定类型的日志

```bash
# 只看订单成交
python tools/view_logs.py --type trading --filter ORDER_FILLED

# 只看平仓记录
python tools/view_logs.py --type trading --filter POSITION_CLOSE
```

## 日志格式

### 系统日志（system_*.log）

```
2026-03-23 11:30:45.123 | INFO     | py-shortqt.system | main_live.py:88 | === py-shortqt v1.3.0 启动 ===
2026-03-23 11:30:45.456 | DEBUG    | py-shortqt.system | binance_client.py:95 | GET /fapi/v1/positionRisk
2026-03-23 11:30:46.789 | WARNING  | py-shortqt.system | live.py:142 | 订单超时，自动取消：oid_12345
2026-03-23 11:30:47.012 | ERROR    | py-shortqt.system | websocket.py:56 | WebSocket 连接失败：Connection reset
```

### 市场日志（market_*.jsonl）

```jsonl
{"ts":"2026-03-23T11:30:45.123Z","type":"BOOK","symbol":"ETHUSDC","bids":[[3456.78,1.23],...],"asks":[[3456.79,2.34],...]}
{"ts":"2026-03-23T11:30:45.456Z","type":"TRADE","symbol":"ETHUSDC","price":3456.78,"qty":0.5,"side":"BUY"}
{"ts":"2026-03-23T11:30:46.789Z","type":"SIGNAL","side":"BUY","price":3456.78,"features":{"imbalance":0.35,"spread":0.01,...}}
{"ts":"2026-03-23T11:30:50.012Z","type":"AMPLITUDE","symbol":"ETHUSDC","window":"1m","max":3460.12,"min":3455.23,"amp":0.14}
```

### 交易日志（trading_*.jsonl）

```jsonl
{"ts":"2026-03-23T11:30:45.123Z","type":"ORDER_NEW","order_id":"12345","side":"BUY","order_type":"LIMIT","price":3456.78,"qty":0.123}
{"ts":"2026-03-23T11:30:46.456Z","type":"ORDER_FILLED","order_id":"12345","avg_price":3456.75,"filled_qty":0.123,"commission":0.0001,"pnl":null}
{"ts":"2026-03-23T11:30:50.789Z","type":"POSITION_OPEN","side":"LONG","entry_price":3456.75,"size":0.123,"leverage":25}
{"ts":"2026-03-23T11:31:15.012Z","type":"POSITION_CLOSE","side":"LONG","exit_price":3457.75,"size":0.123,"pnl":0.00035,"pnl_pct":0.03,"reason":"TP"}
```

### 信号 CSV（signals_*.csv）

```csv
timestamp,side,entry_price,price_5s_change,price_10s_change,price_30s_change,orderbook_imbalance,spread,bid_depth_3,ask_depth_3,result,pnl,duration_sec
2026-03-23 11:30:45,BUY,3456.75,0.05,0.12,0.25,0.35,0.01,12.345,15.678,TP,0.000350,25.00
```

## 使用日志系统

### 在代码中使用

```python
from src.loggers import get_logger

# 获取日志管理器
log_manager = get_logger()

# 记录系统日志
log_manager.system.info("程序启动")
log_manager.system.error("发生错误", exc_info=True)

# 记录市场数据
log_manager.market.log_orderbook("ETHUSDC", bids, asks)
log_manager.market.log_trade("ETHUSDC", price, qty, side)

# 记录交易
log_manager.trading.log_order_new(order_id, side, order_type, price, qty)
log_manager.trading.log_position_close(side, exit_price, size, pnl, pnl_pct, reason)
```

## 调试模式

开启调试模式后，会记录更详细的信息：

- WebSocket 原始消息
- 订单簿完整快照（每笔更新）
- 价格变动详情
- 持仓定期快照

在 `config/settings.py` 中设置：

```python
LOG_DEBUG_MODE = True
```

**注意**：调试模式会产生大量日志，建议仅在排查问题时使用。

## 日志轮转

系统日志按日期自动轮转，保留最近 7 天：

- `system_2026-03-23.log`
- `system_2026-03-22.log`
- ...

交易日志和市场日志按日期分割，手动清理旧文件。

## 日志分析

### 使用 Python 分析

```python
import json
from pathlib import Path

log_dir = Path("logs")
trading_log = log_dir / "trading_2026-03-23.jsonl"

# 统计当日交易
total_pnl = 0
trade_count = 0

with open(trading_log, 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        if data.get('type') == 'POSITION_CLOSE':
            total_pnl += data.get('pnl', 0)
            trade_count += 1

print(f"当日交易：{trade_count} 笔")
print(f"总盈亏：{total_pnl:.6f} USDT")
```

### 使用 Excel 分析

1. 打开 `signals_*.csv` 文件
2. 使用 Excel 的筛选、透视表功能分析
3. 重点关注：
   - 胜率（result=TP 的比例）
   - 平均盈亏比
   - 不同特征下的表现

## 故障排查

### 问题：找不到日志文件

**解决**：检查 `config/settings.py` 中的 `LOG_BASE_DIR` 配置。

### 问题：日志文件为空

**解决**：
1. 检查 `LOG_LEVEL` 是否设置过高
2. 检查程序是否正常退出（日志在关闭时 flush）

### 问题：调试模式日志太多

**解决**：
1. 关闭 `LOG_DEBUG_MODE`
2. 使用 `--filter` 参数过滤查看

## 版本历史

### v1.3.0 (2026-03-23)

- ✅ 重构日志系统，统一入口
- ✅ 新增市场日志（market.log）
- ✅ 新增日志查看工具
- ✅ 支持日志级别动态配置
- ✅ 支持调试模式开关
- ✅ 按日期归档日志文件
