# py-shortqt v1.3.2 - 止损记录缺失修复版本

> 版本：1.3.2  
> 发布日期：2026-03-24  
> 状态：✅ 已发布

---

## 📌 版本说明

v1.3.2 是 v1.3.1 的快速修复版本，修复了条件止损单触发后 PnL 和信号记录缺失的关键问题。

---

## 🐛 修复内容

### 1. 条件止损单触发后记录缺失（关键修复）

**问题描述**：
当用户的订单被条件止损单触发止损时，币安会自动生成一个限价平仓单，但系统没有正确记录：
- PnL 日志缺失
- 交易日志缺失
- `signals.csv` 没有该笔交易记录
- 导致交易日报分析数据不完整

**根因分析**：
在 `src/trading/live.py` 的 `_on_order_update()` 方法中：
1. 条件止损单触发后，币安生成**新的限价单**（不同订单 ID）
2. 新订单 ID 与 `self.sl_order.get('algoId')` 不匹配
3. 落入 Fallback 分支，但该分支缺失关键逻辑：
   - ❌ 未调用 `logger.update_signal_result()` 
   - ❌ 未清空订单状态（tp_order/sl_order/stop_market_order）
   - ❌ 未清空 position 状态

**修复方案**：
在 Fallback 分支中补充：
```python
# 计算持仓时长
duration = (datetime.now() - self.position['time']).total_seconds()

# 记录信号结果（修复 Bug：之前这里缺失导致 signals.csv 没有记录）
if self.logger:
    self.logger.update_signal_result('SL', float(pnl), duration)

# 清空订单状态
self._cancel_other_orders(exclude='none')
```

**修复验证**：
```
✅ trades.log    — "止损成交 PnL: -0.006360 USDT"
✅ signals.csv   — "result=SL, pnl=-0.006360, duration_sec=11.84"
✅ pnl.log       — "止损成交 pnl=-0.00636"
```

**影响范围**：
- 文件：`src/trading/live.py`
- 方法：`_on_order_update()` Fallback 分支
- 修复时间：2026-03-24 11:45

---

## 📊 代码统计

| 文件类型 | 新增 | 修改 | 删除 |
|---------|------|------|------|
| Python | 0 | 1 | 0 |
| 文档 | 1 | 0 | 0 |
| **总计** | **1** | **1** | **0** |

**代码行数**：
- 新增：~10 行
- 修改：~1 行

---

## 🧪 测试验证

### 实盘测试

**测试场景**：
- 小资金实盘测试
- 主动触发条件止损单

**测试结果**：
```
时间：2026-03-24 11:53:05
方向：SHORT @ 2138.13
止损触发：11:53:19
PnL: -0.006360 USDT
持仓时长：11.84 秒

日志检查：
✅ trades.log    — 有记录
✅ signals.csv   — 有记录 (result=SL)
✅ pnl.log       — 有记录
```

**结论**：✅ 修复验证通过

---

## 📦 升级说明

### 从 v1.3.1 升级

```bash
cd D:\Project\py-shortqt
git pull origin develop-1.3.0
```

**无需修改配置**，直接重启程序即可。

### 从 v1.3.0 或更早版本升级

```bash
cd D:\Project\py-shortqt
git pull origin develop-1.3.0
```

建议同时参考 [v1.3.1 发布说明](./VERSION_1.3.1.md) 了解之前的修复内容。

---

## 🔗 相关文档

- [v1.3.1 发布说明](./VERSION_1.3.1.md)
- [v1.3.0 发布说明](./VERSION_1.3.0.md)

---

## 📅 后续计划

### v1.3.3 - 历史仓位查询（预计 2-3 天）

- 从币安 API 获取用户成交记录
- TUI 显示最近 10 笔历史成交
- 定期刷新（30 秒）

### v1.3.4 - 多交易对支持（预计 3-4 天）

- 支持任意交易对（BTCUSDC、SOLUSDC 等）
- 动态获取交易规则（精度、最小名义价值）
- UI 显示交易对信息

---

## 👥 开发团队

- **开发**：老杨
- **测试**：杰哥
- **产品**：杰哥

---

_文档版本：1.0_  
_创建日期：2026-03-24_  
_作者：老杨_
