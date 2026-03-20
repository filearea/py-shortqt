# py-shortqt v1.1.1 开发文档

> 实盘交易版本 - 基于币安 Futures 新 API 文档
> 开发日期：2026-03-20

---

## 📌 版本目标

### 核心功能

- ✅ 接入币安 Futures 实盘 API
- ✅ 支持模拟模式（回测/练习）
- ✅ Maker 挂单剥头皮策略
- ✅ 自动止盈止损
- ✅ 完整的日志系统

### 技术指标

| 指标 | 目标 | 实际 |
|------|------|------|
| 止盈 | +1 点 | ✅ |
| 止损 | -3 点 | ✅ |
| 保底止损 | 强平价 +1 | ✅ |
| 开仓方式 | Maker 挂单 | ✅ |
| 杠杆 | 25x（测试）/ 75x（实盘） | ✅ |

---

## 🏗️ 技术架构

### 模块划分

```
py-shortqt/
├── src/
│   ├── main.py              # 统一启动入口
│   ├── main_live.py         # 实盘模式
│   ├── main_sim.py          # 模拟模式
│   ├── trader.py            # 模拟交易核心
│   ├── websocket.py         # 行情 WebSocket
│   ├── logger.py            # 交易日志
│   ├── system_logger.py     # 系统日志
│   └── api/
│       ├── binance_client.py    # 币安 REST API
│       └── signature.py         # HMAC 签名
├── tests/                   # 测试脚本
├── logs/                    # 运行日志
└── config/                  # 配置文件
```

### 核心类关系

```
┌─────────────────┐
│  LiveTradingBot │  ← 实盘主控制器
├─────────────────┤
│ - listener      │  → BinanceListener (WebSocket)
│ - trader        │  → LiveTrader (实盘交易)
│ - ui            │  → LiveTradingUI (TUI 界面)
└─────────────────┘

┌─────────────────┐
│  LiveTrader     │  ← 实盘交易器
├─────────────────┤
│ + open_position()    → 开仓（QUEUE 保证 Maker）
│ + place_tp_sl()      → 下止盈止损
│ + cancel_early()     → 提前平仓
│ + _sync_position()   → 持仓同步
└─────────────────┘

┌─────────────────┐
│  BinanceClient  │  ← 币安 API 客户端
├─────────────────┤
│ + place_order()      → 普通订单（开仓、止盈）
│ + place_algo_order() → Algo 订单（止损、保底）
│ + cancel_all_open_orders() → 批量撤销
└─────────────────┘
```

---

## 📋 功能实现详情

### 1. 开仓（Maker 保证）

**接口：** `POST /fapi/v1/order`

**参数：**
```python
{
    'symbol': 'ETHUSDC',
    'side': 'BUY',  # 或 'SELL'
    'type': 'LIMIT',
    'priceMatch': 'QUEUE',  # ← 关键：同向价 1，保证 Maker
    'quantity': '0.010',
    'timeInForce': 'GTC',
    'positionSide': 'LONG'  # 双向持仓模式
}
```

**实现逻辑：**
```python
# 多单：自动挂买一价
# 空单：自动挂卖一价
# 不会立即成交，保证 Maker（0 手续费）
```

---

### 2. 止盈单

**接口：** `POST /fapi/v1/order`

**参数：**
```python
{
    'symbol': 'ETHUSDC',
    'side': 'SELL',  # 多单止盈
    'type': 'LIMIT',
    'price': '2149.00',  # 开仓价 +1
    'quantity': '0.010',
    'timeInForce': 'GTC',
    'positionSide': 'LONG'
}
```

**实现逻辑：**
```python
# 1. 计算目标止盈价：开仓价 +1
tp_price = entry_price + Decimal('1')

# 2. 检查是否会变成 Taker
if tp_price <= current_bid:  # 会立即被吃掉
    # 改用 QUEUE，挂买一价
    priceMatch='QUEUE'
else:
    # 用目标价
    price=str(tp_price)

# 3. 下单
```

**设计变更：**
- ❌ 最初设计：`postOnly=True`
- ✅ 实际实现：价格检查 + `priceMatch='QUEUE'`
- **原因：** 新 API 文档没有 `postOnly` 参数

---

### 3. 止损单

**接口：** `POST /fapi/v1/algoOrder`

**参数：**
```python
{
    'symbol': 'ETHUSDC',
    'side': 'SELL',  # 多单止损
    'type': 'STOP',
    'triggerPrice': '2137.00',  # 开仓价 -3
    'priceMatch': 'QUEUE',  # ← 关键：触发后挂同向价 1
    'quantity': '0.010',
    'timeInForce': 'GTC',
    'workingType': 'CONTRACT_PRICE',  # 最新价触发
    'positionSide': 'LONG'
}
```

**实现逻辑：**
```python
# 1. 触发价：开仓价 -3
trigger = entry_price - Decimal('3')

# 2. 限价：触发价 +0.01（保证 Maker）
# 通过 priceMatch='QUEUE' 自动实现

# 3. 下单（Algo Order API）
```

**设计变更：**
- ❌ 最初设计：`STOP_LOSS_LIMIT` + `stopPrice` + `price`
- ✅ 实际实现：`STOP` + `triggerPrice` + `priceMatch='QUEUE'`
- **原因：** 新 API 文档使用 Algo Order API，参数名变更

---

### 4. 保底止损

**接口：** `POST /fapi/v1/algoOrder`

**参数：**
```python
{
    'symbol': 'ETHUSDC',
    'side': 'SELL',  # 多单保底止损
    'type': 'STOP_MARKET',
    'triggerPrice': '2060.00',  # 强平价 +1
    'quantity': '0.010',
    'workingType': 'MARK_PRICE',  # 标记价触发
    'positionSide': 'LONG'
}
```

**实现逻辑：**
```python
# 1. 获取强平价（从持仓接口）
liquidation_price = Decimal(pos['liquidationPrice'])

# 2. 触发价：强平价 +1
trigger = liquidation_price + Decimal('1')

# 3. 下单（STOP_MARKET，触发后市价成交）
```

**注意：** 保底止损是市价单，不保证 Maker，但确保成交（最后防线）。

---

### 5. 提前平仓

**接口：** `POST /fapi/v1/order`

**参数：**
```python
{
    'symbol': 'ETHUSDC',
    'side': 'SELL',  # 多单提前平仓
    'type': 'LIMIT',
    'priceMatch': 'QUEUE',  # 同向价 1，保证 Maker
    'quantity': '0.010',
    'timeInForce': 'GTC',
    'reduceOnly': False,  # 双向持仓模式不接受此参数
    'positionSide': 'LONG'
}
```

**实现逻辑：**
```python
# 1. 撤销止盈单
# 2. 保留止损单（保护）
# 3. 挂反向 Maker 单平仓
# 4. 如果用户撤销提前平仓，恢复原止盈单
```

**设计变更：**
- ❌ 最初设计：撤销提前平仓后不恢复止盈单
- ✅ 实际实现：撤销提前平仓后自动恢复原止盈单
- **原因：** 用户体验优化

---

### 6. 持仓同步

**问题：** STOP 类型止损单触发后，会变成新的 LIMIT 订单，订单 ID 改变，导致检测不到成交。

**解决方案：**
```python
def _sync_position(self):
    """查询并同步实际持仓状态"""
    positions = self.api.get_position(self.symbol)
    
    # 汇总双向持仓
    total = sum(Decimal(pos['positionAmt']) for pos in positions)
    
    if total == 0 and self.position:
        # 持仓已清空，记录 PnL
        pnl = ...  # 从订单响应计算
        self.logger.log_pnl('平仓成交', pnl)
```

**Fallback 机制：**
```python
# 如果订单成交但没匹配到已知订单
elif order_status == 'FILLED' and self.position:
    order_side = order_data.get('S', '')
    if (self.position['side'] == 'LONG' and order_side == 'SELL'):
        # 可能是止损触发的限价单成交
        pnl = ...  # 计算 PnL
        self._add_action("订单成交", f"PnL: {pnl:+.6f} USDT")
```

---

## 📊 日志系统

### 日志分类

| 日志文件 | 内容 | 格式 |
|----------|------|------|
| `trades.log` | 交易动作 | JSON |
| `orders.log` | 订单信息 | JSON |
| `positions.log` | 持仓信息 | JSON |
| `pnl.log` | PnL 记录 | JSON |
| `signals.csv` | 信号特征 | CSV |
| `system.log` | 系统日志 | 文本 |

### 日志写入时机

```python
def _add_action(self, action: str, details: str):
    # 1. 内存日志（TUI 显示）
    self.action_log.append({...})
    
    # 2. 文件日志
    if self.logger:
        if '开仓' in action:
            self.logger.log_trade('开仓成交', {...})
            self.logger.log_position(side, price, size, 0)
        elif '止盈' in action:
            pnl = float(...)
            self.logger.log_pnl('止盈成交', pnl)
```

---

## ⚠️ 设计变更总结

### API 相关

| 项目 | 最初设计 | 实际实现 | 原因 |
|------|---------|---------|------|
| **API 文档** | binance-docs.github.io | developers.binance.com | 旧文档已废弃 |
| **Base URL** | `testnet.binancefuture.com` | `demo-fapi.binance.com` | 新测试网地址 |
| **止损接口** | `/fapi/v1/order` | `/fapi/v1/algoOrder` | 条件单移到 Algo API |
| **postOnly** | `postOnly=True` | `priceMatch='QUEUE'` | 新 API 无 postOnly |
| **reduceOnly** | `reduceOnly=True` | 不传此参数 | 双向持仓不接受 |
| **参数名** | `stopPrice` | `triggerPrice` | Algo API 参数名变更 |

### 功能相关

| 项目 | 最初设计 | 实际实现 | 原因 |
|------|---------|---------|------|
| **止盈单** | 开仓价 +1 | 价格检查 + QUEUE | 避免 Taker |
| **止损单** | 本地监控 | Algo Order API | 更可靠 |
| **撤销提前平仓** | 不恢复止盈 | 自动恢复止盈 | 用户体验 |
| **持仓同步** | 订单 ID 匹配 | 持仓查询 + Fallback | 止损单 ID 会变 |
| **日志位置** | `src/logs/` | `logs/` | 项目根目录 |

---

## 🧪 测试验证

### 测试用例

| 用例 | 预期结果 | 实际结果 |
|------|---------|---------|
| 开仓成功 | Maker 挂单成交 | ✅ |
| 止盈成交 | PnL 正确记录 | ✅ |
| 止损成交 | PnL 正确记录 | ✅ |
| 保底止损 | 强平价触发 | ✅ |
| 提前平仓 | Maker 成交 | ✅ |
| 撤销提前平仓 | 恢复止盈单 | ✅ |
| 日志写入 | 文件有内容 | ✅ |
| 空单持仓 | 正确识别 | ✅ |

### 已知问题

- ❌ 暂无（所有问题已修复）

---

## 📝 待优化项

### 策略优化

1. **胜率提升** - 当前约 33%，目标>50%
2. **盈亏比优化** - 当前 1:3，考虑 1:2 或动态止损
3. **入场信号** - 结合盘口特征、资金费率

### 功能优化

1. **自动重启** - 程序崩溃后自动恢复
2. **远程监控** - 飞书/微信通知
3. **回测系统** - 历史数据验证策略

---

## 🔗 参考链接

- **币安 Futures API：** https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures
- **订单类型：** https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures/trade/rest-api
- **Algo Order：** `/fapi/v1/algoOrder`

---

_文档版本：1.0_
_最后更新：2026-03-20_
_作者：老王_
