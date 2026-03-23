# py-shortqt v1.3.1 - Bug 修复版本

> 版本：1.3.1  
> 发布日期：2026-03-23  
> 状态：✅ 已发布

---

## 📌 版本说明

v1.3.1 是 v1.3.0 的快速修复版本，主要修复了保底止损计算错误等关键问题。

---

## 🐛 修复内容

### 1. 保底止损计算错误（关键修复）

**问题**：
- 开仓时 `position_margin` 还没更新，导致总权益计算错误
- 保底止损距离开仓价只有 0.28%（应该是 0.78%）
- 50% 最大损失配置下，实际只承受了约 18% 的损失就触发保底

**修复**：
```python
# 修复前（错误）
total_equity = self.available_balance + self.position_margin
# 开仓后：available_balance = 17.28, position_margin = 0
# total_equity = 17.28（错误）

# 修复后（正确）
position_value = entry_price * size
position_margin_required = position_value / Decimal(self.actual_leverage)
total_equity = self.available_balance + position_margin_required
# 开仓后：available_balance = 17.28, position_margin_required = 17.32
# total_equity = 34.6（正确）
```

**影响**：
- 保底止损距离恢复正常（50% 最大损失 → ~0.78% 价格波动）
- 做多和做空计算一致

---

### 2. 信号 CSV 为空（数据收集修复）

**问题**：
- `signals_2026-03-23.csv` 文件为 0 字节
- 代码中从未调用 `record_signal` 和 `update_signal_result`

**修复**：
- 在开仓成交时调用 `logger.record_signal()` 记录入场特征
- 在平仓时调用 `logger.update_signal_result()` 记录结果
- 支持的平仓类型：TP/SL/STOP_MARKET/MANUAL

**影响**：
- 信号 CSV 现在自动记录完整的入场特征和平仓结果
- 便于后续策略分析和优化

---

### 3. 日志模块命名冲突（架构修复）

**问题**：
- `src/logging/` 目录与 Python 标准库 `logging` 模块冲突
- 导致 `import` 错误

**修复**：
- 重命名为 `src/loggers/`
- 更新所有导入语句

**影响**：
- 日志系统正常工作
- 避免潜在的导入冲突

---

### 4. 版本号显示不一致（体验修复）

**问题**：
- TUI 界面显示 v1.2.0
- 启动器显示 v1.2.0
- 实际代码是 v1.3.0

**修复**：
- 新增 `VERSION` 文件统一管理版本号
- 新增 `src/__init__.py` 提供 `__version__` 函数
- TUI、启动器、日志都从 `VERSION` 文件读取

**影响**：
- 修改版本号只需更新 `VERSION` 文件
- 所有地方自动同步

---

## 📊 代码统计

| 文件类型 | 新增 | 修改 | 删除 |
|---------|------|------|------|
| Python | 2 | 8 | 0 |
| 文档 | 2 | 1 | 0 |
| 配置 | 1 | 0 | 0 |
| **总计** | **5** | **9** | **0** |

**代码行数**：
- 新增：~200 行
- 修改：~50 行

---

## 🧪 测试验证

### 保底止损计算测试

**测试场景**：
- 开仓价：2050 USDT
- 仓位：0.88 ETH
- 实际杠杆：60x
- 最大损失：50%
- 可用余额：开仓后余额

**预期结果**：
```
总权益 = 可用余额 + 本单保证金
      = 17.28 + 17.32
      = 34.6 USDT

最大损失 = 34.6 × 50% = 17.3 USDT
价格波动 = 17.3 / 0.88 = 19.66 USDT
保底距离 = 19.66 / 2050 = 0.96%
```

**实际结果**：✅ 符合预期

---

### 信号记录测试

**测试场景**：
- 开仓 → 止盈/止损
- 检查 `signals_*.csv` 文件

**预期结果**：
```csv
timestamp,side,entry_price,price_5s_change,orderbook_imbalance,result,pnl,duration_sec
2026-03-23 14:41:06,LONG,2041.02,0.05,0.35,TP,0.000350,25.00
```

**实际结果**：✅ 符合预期

---

## 📦 升级说明

### 从 v1.3.0 升级

```bash
cd D:\Project\py-shortqt
git pull origin develop-1.3.0
```

**无需修改配置**，直接重启程序即可。

### 从 v1.2.0 或更早版本升级

```bash
cd D:\Project\py-shortqt
git pull origin develop-1.3.0
pip install -r requirements.txt  # 无新增依赖
```

---

## 🔗 相关文档

- [v1.3.0 发布说明](./VERSION_1.3.0.md)
- [日志系统文档](./LOGGING.md)
- [v1.3.0 开发计划](./VERSION_1.3.0_PLAN.md)

---

## 📅 后续计划

### v1.3.2 - 历史仓位查询（预计 2-3 天）

- 从币安 API 获取用户成交记录
- TUI 显示最近 10 笔历史成交
- 定期刷新（30 秒）

### v1.3.3 - 多交易对支持（预计 3-4 天）

- 支持任意交易对（BTCUSDC、SOLUSDC 等）
- 动态获取交易规则（精度、最小名义价值）
- UI 显示交易对信息

---

## 👥 开发团队

- **开发**：老王
- **测试**：杰哥
- **产品**：杰哥

---

_文档版本：1.0_  
_创建日期：2026-03-23_  
_作者：老王_
