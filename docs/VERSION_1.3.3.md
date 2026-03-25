# py-shortqt v1.3.3 - 账户余额日志版本

## 版本信息

- **版本号**：v1.3.3
- **发布日期**：2026-03-25
- **Git 提交**：`f548f37`
- **Git 标签**：`v1.3.3`
- **基于版本**：v1.3.2

---

## 🆕 新增功能

### 账户余额日志（用于复合收益率计算）

交易部门需要计算复合收益率，在 `trades.log` 中增加余额记录功能。

#### 日志记录时机

| 事件类型 | 触发时机 | 状态 |
|---------|---------|------|
| `startup` | 软件启动后 | ✅ 正常 |
| `position_closed` | 每次彻底平仓后 | ✅ 正常 |
| `shutdown` | 用户关闭系统前 | ⚠️ 按 Q 键有效，关闭窗口可能无效 |

#### 支持的平仓场景（7 种）

1. **TP** - 止盈单成交
2. **SL** - 止损单成交
3. **STOP_MARKET** - 保底止损成交
4. **MANUAL** - 手动提前平仓
5. **SL_FALLBACK** - 未知订单止损
6. **sync_detected** - 持仓同步检测到平仓
7. **MARKET_CLOSE** - Z 键市价全平

#### 日志格式示例

**启动日志：**
```json
{"timestamp":"2026-03-25 13:13:32.059","type":"BALANCE","event":"startup","balance":3.50845185,"details":{"account":"储蓄账户","leverage":5}}
```

**平仓日志：**
```json
{"timestamp":"2026-03-25 13:14:00.123","type":"BALANCE","event":"position_closed","balance":3.55845185,"details":{"reason":"TP","pnl":0.05,"entry_price":2150.0,"close_price":2151.0}}
```

**关闭日志：**
```json
{"timestamp":"2026-03-25 13:15:00.456","type":"BALANCE","event":"shutdown","balance":3.55845185,"details":{"account":"储蓄账户","exit_type":"finally_block"}}
```

#### 日志位置

```
D:\Project\py-shortqt\logs\<run_id>\trades.log
```

#### 代码实现

**新增方法：** `src/logger.py`
```python
def log_balance(self, event: str, balance: Decimal, details: dict = None):
    """记录账户余额（用于复合收益率计算）"""
    # event: 'startup' | 'position_closed' | 'shutdown'
    # 输出到 trades.log，JSON 格式，带 flush() 确保立即写入
```

**调用位置：**
- `src/main_live.py` - 启动后、关闭前
- `src/trading/live.py` - 7 种平仓场景

---

## 📊 交易部门使用方法

### 计算单次收益率

```python
import json

def calculate_returns(trades_log_path: str):
    """计算单次收益率"""
    returns = []
    start_balance = None
    
    with open(trades_log_path) as f:
        for line in f:
            record = json.loads(line)
            if record['type'] == 'BALANCE':
                if record['event'] == 'startup':
                    start_balance = record['balance']
                elif record['event'] == 'position_closed':
                    if start_balance:
                        pnl = record['details'].get('pnl', 0)
                        single_return = pnl / start_balance
                        returns.append({
                            'timestamp': record['timestamp'],
                            'reason': record['details'].get('reason'),
                            'pnl': pnl,
                            'return': single_return,
                            'balance': record['balance']
                        })
                        start_balance = record['balance']
    
    return returns
```

### 计算复合收益率

```python
def calculate_compound_return(returns: list):
    """计算复合收益率"""
    compound = 1.0
    for r in returns:
        compound *= (1 + r['return'])
    return compound - 1
```

### 计算最大回撤

```python
def calculate_max_drawdown(returns: list):
    """计算最大回撤"""
    peak = 0
    max_dd = 0
    
    cumulative = 1.0
    for r in returns:
        cumulative *= (1 + r['return'])
        if cumulative > peak:
            peak = cumulative
        dd = (peak - cumulative) / peak
        if dd > max_dd:
            max_dd = dd
    
    return max_dd
```

---

## 🐛 Bug 修复

### 清理空日志文件

- 删除了 60 个空的日志文件（orders.log, pnl.log, positions.log, signals.csv, trades.log）
- 删除了空的日志目录

---

## ⚠️ 已知问题

### Windows 关闭窗口时 shutdown 日志可能无法写入

**原因：** Windows 关闭窗口时，Python 进程被强制终止，`finally` 块可能来不及执行。

**影响：** 按 Q 键退出时 shutdown 日志正常写入；直接关闭窗口时可能丢失。

**建议：** 用户养成按 Q 键退出的习惯。

---

## 📝 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `src/logger.py` | 新增 `log_balance()` 方法 |
| `src/main_live.py` | 启动后、关闭前调用 `log_balance()` |
| `src/trading/live.py` | 7 种平仓场景调用 `log_balance()` |
| `VERSION` | 更新为 1.3.3 |
| `README.md` | 更新版本信息和日志系统说明 |

---

## 🧪 测试建议

### 启动日志测试

1. 运行程序
2. 检查 `logs/<run_id>/trades.log`
3. 确认有 `event: startup` 记录

### 平仓日志测试

1. 开仓 → 止盈/止损
2. 检查 `trades.log`
3. 确认有 `event: position_closed` 记录，包含 `reason`、`pnl` 等字段

### 关闭日志测试

1. 按 Q 键退出
2. 检查 `trades.log`
3. 确认有 `event: shutdown` 记录

---

## 📚 相关文档

- [日志系统说明](LOGGING.md)
- [v1.3.2 版本文档](VERSION_1.3.2.md)

---

_文档创建时间：2026-03-25_
