# py-shortqt v1.3.0 - 发布说明

> 版本：1.3.0  
> 发布日期：2026-03-23  
> 状态：✅ 已发布

---

## 📌 版本亮点

**v1.3.0 聚焦于日志系统重构**，为后续策略优化和数据分析打下基础。

### 核心改进

1. **完整的日志系统** ✅
   - 系统日志、市场日志、交易日志分离
   - JSONL 格式，便于后续分析
   - 信号特征自动记录到 CSV

2. **日志查看工具** ✅
   - 支持按类型、日期过滤
   - 支持 tail 模式查看
   - 支持按事件类型筛选

3. **保底止损计算修复** ✅
   - 使用总权益而不是开仓后余额
   - 保底止损价更合理

---

## 🆕 新增功能

### 1. 日志系统重构

**新增模块**：
```
src/loggers/
├── __init__.py          # 模块入口
├── manager.py           # 日志管理器（单例模式）
├── system.py            # 系统运行日志
├── market.py            # 市场数据日志
└── trading.py           # 交易日志
```

**日志文件结构**：
```
logs/
├── system_2026-03-23.log        # 系统日志
├── market_2026-03-23.jsonl      # 市场数据（订单簿、价格）
├── trading_2026-03-23.jsonl     # 交易日志（订单/持仓）
├── signals_2026-03-23.csv       # 信号特征与结果
└── index.json                   # 运行索引
```

**配置项**（`config/settings.py`）：
```python
LOG_DEBUG_MODE = False  # 调试模式开关
LOG_LEVEL = "INFO"      # 日志级别
```

---

### 2. 日志查看工具

**使用方法**：
```bash
# 查看日志列表
python tools/view_logs.py --list

# 查看交易日志
python tools/view_logs.py --type trading --tail

# 过滤特定事件
python tools/view_logs.py --type trading --filter POSITION_CLOSE

# 查看系统日志
python tools/view_logs.py --type system --tail
```

---

### 3. 信号特征记录

**自动记录的数据**：
- 入场时间、方向、价格
- 价格变化（5s/10s/30s）
- 订单簿 imbalance
- 订单簿深度（前 3 档）
- 价差
- 平仓结果（TP/SL/MANUAL）
- PnL 和持仓时间

**CSV 格式**：
```csv
timestamp,side,entry_price,price_5s_change,price_10s_change,orderbook_imbalance,result,pnl,duration_sec
2026-03-23 12:18:43,BUY,2053.50,0.05,0.12,0.35,TP,0.000350,25.00
```

---

## 🐛 Bug 修复

### 1. 保底止损计算错误

**问题**：使用开仓后的可用余额计算保底止损，导致止损价偏高。

**修复**：改用总权益（可用余额 + 持仓保证金）计算。

**影响**：
- 修复前：34.6U 余额 → 保底止损价 2047.66（距离强平价 20 点）
- 修复后：34.6U 余额 → 保底止损价 2037.26（距离强平价 10 点）

---

### 2. 信号 CSV 为空

**问题**：代码中从未调用 `record_signal` 和 `update_signal_result`。

**修复**：在开仓成交和平仓时添加信号记录调用。

---

### 3. 日志模块命名冲突

**问题**：`src/logging/` 与 Python 标准库 `logging` 冲突。

**修复**：重命名为 `src/loggers/`。

---

## 📦 安装说明

### 依赖要求

```bash
pip install -r requirements.txt
```

**新增依赖**：无（使用 Python 标准库 `logging` 模块）

---

### 配置升级

**无需修改现有配置**，v1.3.0 向后兼容。

**可选配置**（`config/settings.py`）：
```python
# 日志配置
LOG_DEBUG_MODE = False  # True=记录详细调试日志
LOG_LEVEL = "INFO"      # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

---

## 🧪 测试验证

### 日志系统测试

```bash
# 运行测试脚本
python tests/test_logging.py

# 查看测试结果
python tools/view_logs.py --list
python tools/view_logs.py --type trading --tail
```

**预期结果**：
- ✅ 生成 `system_*.log`、`market_*.jsonl`、`trading_*.jsonl`、`signals_*.csv`
- ✅ 信号 CSV 包含完整的入场特征和平仓结果
- ✅ 日志查看工具正常工作

---

### 实盘验证

**测试场景**：
1. 开仓 → 检查信号特征是否记录
2. 止盈/止损 → 检查信号结果是否记录
3. 查看日志 → 确认数据完整性

---

## 📊 数据统计

### 代码变更

| 文件类型 | 新增 | 修改 | 删除 |
|---------|------|------|------|
| Python | 5 | 3 | 0 |
| 文档 | 2 | 1 | 0 |
| 测试 | 1 | 0 | 0 |
| **总计** | **8** | **4** | **0** |

### 代码行数

- **新增代码**：~1500 行
- **修改代码**：~50 行
- **测试代码**：~100 行

---

## 🔗 相关文档

- [日志系统文档](./LOGGING.md)
- [v1.3.0 开发计划](./VERSION_1.3.0_PLAN.md)
- [v1.2.0 发布说明](./VERSION_1.2.0.md)

---

## 📅 后续计划

### v1.3.1 - 历史仓位查询（预计 2-3 天）

- 从币安 API 获取用户成交记录
- TUI 显示最近 10 笔历史成交
- 定期刷新（30 秒）

### v1.3.2 - 多交易对支持（预计 3-4 天）

- 支持任意交易对（BTCUSDC、SOLUSDC 等）
- 动态获取交易规则（精度、最小名义价值）
- UI 显示交易对信息

---

## 👥 开发团队

- **开发**：老王
- **测试**：老王
- **产品**：杰哥

---

## 📝 升级建议

### 从 v1.2.0 升级

1. **拉取最新代码**
   ```bash
   git pull origin develop-1.3.0
   ```

2. **安装依赖**（无新增依赖）
   ```bash
   pip install -r requirements.txt
   ```

3. **配置文件**（无需修改）
   - 现有配置继续有效
   - 可选：添加日志配置项

4. **启动测试**
   ```bash
   python tests/test_logging.py
   python launcher.py
   ```

---

_文档版本：1.0_  
_创建日期：2026-03-23_  
_作者：老王_
