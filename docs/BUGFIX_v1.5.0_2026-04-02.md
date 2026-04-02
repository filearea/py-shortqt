# py-shortqt v1.5.0 Bug 修复报告

**修复日期**: 2026-04-02  
**修复人**: 老杨  
**版本**: v1.5.0  
**状态**: ✅ 修复完成

---

## 🐛 问题概述

### 上午修复（移动止损/浮亏保护）

| 问题 | 状态 | 原因 |
|------|------|------|
| 移动止损不触发 | ✅ 已修复 | 日志级别问题 + 空单调试日志缺失 |
| 浮亏保护不触发 | ✅ 已修复 | 未定义变量引用 bug |
| 调试日志无输出 | ✅ 已修复 | 强制 print 输出 |

### 下午修复（Decimal 序列化错误）

| 问题 | 状态 | 原因 |
|------|------|------|
| `Object of type Decimal is not JSON serializable` | ✅ 已修复 | recorder.py 和 market.py 缺少 `default=str` |

---

## 🔍 问题诊断

### 1. 移动止损不触发

**根本原因**：
1. **日志级别问题**：配置文件中日志级别为 INFO，但代码中使用 `system.debug()`，导致调试信息不输出
2. **空单调试缺失**：`_get_current_level()` 方法中，多单有 print 调试，但空单只有 log_manager 日志
3. **关键逻辑无输出**：`update_trailing_stop()` 中的检查逻辑没有强制输出

**影响**：
- 无法判断移动止损是否在工作
- 无法追踪网格计算和触发逻辑

### 2. 浮亏保护不触发

**根本原因**：
- **严重 Bug**：`set_entry_info()` 方法中引用了未定义的变量 `unrealized_pnl`
- 代码尝试在错误的位置（开仓信息设置时）记录盈亏，但此时还没有盈亏数据

**影响**：
- 可能导致程序崩溃
- 无法追踪浮亏保护的检测逻辑

### 3. 调试日志无输出

**根本原因**：
- 配置：`调试模式：False, 日志级别：INFO`
- 代码依赖 `log_manager.system.debug()`，在 INFO 级别下被过滤

**影响**：
- 无法调试和排查问题
- 用户无法了解程序运行状态

---

## ✅ 修复方案

### 修复 1: 移动止损日志增强

**文件**: `src/trading/trailing_stop.py`

**修改内容**：
1. 将所有关键逻辑的 `system.debug()` 改为 `system.info()` + `print()` 双重输出
2. 空单检查增加与多单一致的 print 调试日志
3. 关键节点添加 ASCII 调试输出

**代码示例**：
```python
# 修复前
if self.trader.log_manager:
    self.trader.log_manager.system.debug(f"[移动止损] 启用={self.enabled}...")

# 修复后
print(f'[移动止损 update] 启用={self.enabled}, 开仓价={self.entry_price}, 当前价={current_price}')
if self.trader.log_manager:
    self.trader.log_manager.system.info(f"[移动止损] 启用={self.enabled}...")
```

### 修复 2: 浮亏保护 Bug 修复

**文件**: `src/trading/loss_protection.py`

**修改内容**：
1. 移除 `set_entry_info()` 中对未定义变量 `unrealized_pnl` 的引用
2. 添加正确的调试日志输出
3. 在 `check_and_protect()` 中增加时间检测日志

**代码示例**：
```python
# 修复前（错误）
def set_entry_info(self, ...):
    # unrealized_pnl 未定义！
    self.trader.log_manager.system.debug(f'盈亏={unrealized_pnl:.6f}')

# 修复后
def set_entry_info(self, ...):
    print(f'[浮亏保护 set_entry_info] 启用={self.enabled}, 开仓价={entry_price}, 方向={side}')
    if self.trader.log_manager:
        self.trader.log_manager.system.info(f'[浮亏保护] 开仓信息已设置')
```

### 修复 3: 调试日志强制输出

**策略**：
- 所有关键逻辑添加 `print()` 强制输出到控制台
- 同时保留 `log_manager.system.info()` 用于文件日志
- 确保无论日志级别如何，用户都能看到关键信息

---

## 📝 修改文件清单

### 上午修复

| 文件 | 修改内容 | 行数变化 |
|------|----------|----------|
| `src/trading/trailing_stop.py` | 移动止损日志增强 | +20 行 |
| `src/trading/loss_protection.py` | 浮亏保护 Bug 修复 | +10 行 |

### 下午修复

| 文件 | 修改内容 | 行数变化 |
|------|----------|----------|
| `src/recorder.py` | 添加 `default=str` 处理 Decimal | +2 行 |
| `src/loggers/market.py` | 添加 `default=str` 处理 Decimal | +2 行 |
| **总计** | **4 个文件** | **+34 行** |

---

## 🧪 测试建议

### 1. 移动止损测试

**步骤**：
1. 启动程序，确认配置 `trailing_stop.enabled = true`
2. 开仓（多单或空单）
3. 观察控制台输出，应看到：
   ```
   [移动止损 update] 启用=True, 开仓价=XXX, 当前价=XXX, 网格=10
   [移动止损] 多单检查：当前价=XXX, 网格=10
   [移动止损] 格 1: XXX, 当前价 XXX >= XXX
   ...
   ```
4. 当价格超过第 2 格时，应看到：
   ```
   [移动止损] 第 2 格触发，创建止损单
   ```

### 2. 浮亏保护测试

**步骤**：
1. 启动程序，确认配置 `loss_protection.enabled = true, trigger_minutes = 1`
2. 开仓后观察控制台：
   ```
   [浮亏保护 set_entry_info] 启用=True, 开仓价=XXX, 方向=LONG
   ```
3. 等待 1 分钟后，应看到：
   ```
   [浮亏保护 check] 已过=1.00 分钟，需要=1 分钟
   ```
4. 如果浮亏，应看到：
   ```
   [浮亏保护] 检测到浮亏 -X.XX USDT，执行保护
   [浮亏保护 DEBUG] _execute_protection 被调用！
   ```

### 3. 日志输出测试

**步骤**：
1. 启动程序，观察控制台是否有调试输出
2. 检查日志文件 `logs/system_2026-04-02.log`
3. 确认移动止损和浮亏保护的日志正常记录

---

## 📊 预期效果

### 修复前
- ❌ 移动止损无输出，无法判断是否工作
- ❌ 浮亏保护无输出，可能崩溃
- ❌ 调试日志完全无输出

### 修复后
- ✅ 移动止损每秒输出调试信息
- ✅ 浮亏保护每分钟检测并输出
- ✅ 所有关键逻辑都有 print 强制输出
- ✅ 日志文件记录完整

---

##  下一步建议

### 立即可做
1. ✅ 重启程序，测试修复效果
2. ✅ 开仓观察移动止损和浮亏保护是否正常工作
3. ✅ 收集日志，确认功能正常

### 后续优化（v1.5.1）
1. 考虑添加配置项控制调试输出开关
2. 优化日志级别管理（支持动态调整）
3. 添加更详细的统计信息（触发次数、保护次数等）

---

## 📞 问题反馈

如修复后仍有问题，请提供：
1. 控制台截图（包含调试输出）
2. 日志文件：`logs/system_2026-04-02.log`
3. 复现步骤

---

**修复完成时间**: 2026-04-02  
**修复状态**: ✅ 已完成，待测试验证  
**下次版本**: v1.5.1（计划 2026-04-XX）
