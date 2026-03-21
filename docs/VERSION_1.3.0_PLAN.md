# py-shortqt v1.3.0 - 开发计划

> 版本：1.3.0
> 规划日期：2026-03-21
> 状态：📋 规划中

---

## 📌 版本目标

v1.3.0 版本主要聚焦于三个核心改进：
1. **多交易对支持** - 从固定 ETHUSDC 扩展到任意交易对
2. **历史仓位查询** - TUI 中展示历史交易记录
3. **日志系统优化** - 增强调试和数据收集能力

---

## 1️⃣ 多交易对支持

### 需求分析

当前系统固定使用 `ETHUSDC`，v1.3 需要支持任意交易对。

### 影响范围评估

#### 1.1 配置文件

**修改：** `config/runtime.json`
```json
{
    "symbol": "ETHUSDC",  // ← 新增配置项
    "take_profit": { ... },
    "stop_loss": { ... },
    ...
}
```

#### 1.2 交易参数动态获取

**需要获取的交易所信息：**

| 参数 | 接口 | 用途 |
|------|------|------|
| `pricePrecision` | `/fapi/v1/exchangeInfo` | 价格精度（小数位数） |
| `quantityPrecision` | `/fapi/v1/exchangeInfo` | 数量精度（小数位数） |
| `minPrice` | `/fapi/v1/exchangeInfo` | 最小价格变动 |
| `maxPrice` | `/fapi/v1/exchangeInfo` | 最大价格限制 |
| `minQty` | `/fapi/v1/exchangeInfo` | 最小下单数量 |
| `maxQty` | `/fapi/v1/exchangeInfo` | 最大下单数量 |
| `stepSize` | `/fapi/v1/exchangeInfo` | 数量步长 |
| `tickSize` | `/fapi/v1/exchangeInfo` | 价格步长 |
| `minNotional` | `/fapi/v1/exchangeInfo` | 最小名义价值（当前 20 USDC） |

**实现：**
```python
# src/api/binance_client.py
def get_symbol_info(self, symbol: str) -> dict:
    """获取交易对信息"""
    return self._get('/fapi/v1/exchangeInfo', {'symbol': symbol})

def get_trading_rules(self, symbol: str) -> dict:
    """解析交易规则（精度、限制等）"""
    info = self.get_symbol_info(symbol)
    # 解析 filters 数组，提取价格/数量精度和限制
    return {
        'price_precision': ...,
        'quantity_precision': ...,
        'min_price': ...,
        'max_price': ...,
        'min_qty': ...,
        'max_qty': ...,
        'min_notional': ...,
        ...
    }
```

#### 1.3 配置管理器

**修改：** `src/config/manager.py`
```python
class ConfigManager:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.trading_rules = {}  # ← 新增：存储交易规则
        self.load()
        self.update_trading_rules()  # ← 启动时获取交易规则
    
    def update_trading_rules(self):
        """从交易所获取交易规则"""
        symbol = self.get('symbol', 'ETHUSDC')
        self.trading_rules = self.api.get_trading_rules(symbol)
    
    def validate_order(self, price: Decimal, size: Decimal) -> tuple[bool, str]:
        """验证订单是否符合交易所规则"""
        # 检查价格精度
        # 检查数量精度
        # 检查最小名义价值
        # ...
```

#### 1.4 UI 修改

**修改：** `src/ui/live_ui.py`
```python
def _render_header(self) -> Panel:
    """头部显示交易对"""
    return Panel(
        f"[bold cyan]{self.symbol}[/bold cyan]  |  价格：...",
        title=f"py-shortqt v1.3.0 - {self.symbol}"
    )
```

**修改：** `src/ui/settings_ui.py`
```python
# 添加交易对选择字段
{'key': 'symbol', 'label': '交易对', 'type': 'text', 'default': 'ETHUSDC'}
```

#### 1.5 订单计算

**修改：** 所有涉及价格和数量的计算
```python
# 之前：固定精度
size = (contract_value / self.last_price).quantize(Decimal("0.001"))

# 修改后：动态精度
precision = self.trading_rules['quantity_precision']
size = (contract_value / self.last_price).quantize(Decimal(f"0.{'0' * precision}"))
```

#### 1.6 止盈止损计算

**修改：** `src/config/manager.py`
```python
def get_take_profit_price(self, entry_price: Decimal, side: str = 'LONG') -> Decimal:
    """计算止盈价（考虑价格精度）"""
    # ... 原有计算 ...
    precision = self.trading_rules['price_precision']
    return tp_price.quantize(Decimal(f"0.{'0' * precision}"))
```

### 实现步骤

1. **第 1 步：** 添加交易对配置项
2. **第 2 步：** 实现交易规则获取 API
3. **第 3 步：** 配置管理器集成交易规则
4. **第 4 步：** UI 支持交易对显示和切换
5. **第 5 步：** 订单计算使用动态精度
6. **第 6 步：** 全面测试（多交易对验证）

### 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 精度处理错误 | 订单被拒绝 | 添加验证和日志 |
| 最小名义价值检查 | 开仓失败 | 开仓前验证 |
| 旧配置兼容 | 配置加载失败 | 默认值处理 |

---

## 2️⃣ 历史仓位查询

### 需求分析

在 TUI 中间区域（账户详情和订单簿之间）增加历史仓位查询，只展示当前交易对的历史数据。

### 数据来源

**币安 API：**
- `/fapi/v1/userTrades` - 用户成交记录
- `/fapi/v1/allOrders` - 所有订单（含历史）

### UI 设计

```
┌──────────────────────────────────────────────────────────────┐
│ ETHUSDC | 价格：2152.19 ↑ | 行情：● 订单：● | 杠杆：75x/100x │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  订单簿                    历史仓位 (最近 10 笔)              │
│  2149.50  0.125          ┌────────────────────────────┐     │
│  2149.00  0.250          │ 时间    方向  价格   数量  │     │
│  2148.50  0.375          │ 18:30  LONG  2153  0.012  │     │
│  2148.17  (最新价)       │ 17:45  SHORT 2148  0.015  │     │
│  2148.00  0.500          │ 16:20  LONG  2145  0.020  │     │
│  2147.50  0.625          │ ...                       │     │
│  2147.00  0.750          └────────────────────────────┘     │
│                                                              │
│  账户                                        日志           │
│  可用：15.234 U                              ...            │
│  占用：19.766 U                                             │
└──────────────────────────────────────────────────────────────┘
```

### 实现方案

#### 2.1 API 封装

**新增：** `src/api/binance_client.py`
```python
def get_user_trades(self, symbol: str, limit: int = 100) -> list:
    """获取用户成交记录"""
    params = {
        'symbol': symbol,
        'limit': limit
    }
    return self._get('/fapi/v1/userTrades', params, signed=True)

def get_all_orders(self, symbol: str, limit: int = 100) -> list:
    """获取所有订单（含历史）"""
    params = {
        'symbol': symbol,
        'limit': limit
    }
    return self._get('/fapi/v1/allOrders', params, signed=True)
```

#### 2.2 数据缓存

**新增：** `src/trading/history.py`
```python
class PositionHistory:
    """历史仓位管理"""
    
    def __init__(self, api: BinanceClient, symbol: str):
        self.api = api
        self.symbol = symbol
        self.history = []  # 缓存最近 N 笔成交
    
    def refresh(self, limit: int = 100):
        """刷新历史数据"""
        trades = self.api.get_user_trades(self.symbol, limit)
        self.history = trades[:50]  # 只保留最近 50 笔
    
    def get_recent(self, count: int = 10) -> list:
        """获取最近 N 笔"""
        return self.history[:count]
```

#### 2.3 UI 渲染

**修改：** `src/ui/live_ui.py`
```python
def _render_account(self) -> Text:
    """渲染账户信息（包含历史仓位）"""
    # ... 原有账户信息 ...
    
    # 历史仓位
    if hasattr(self, 'position_history'):
        acc_text.append("\n", style="default")
        acc_text.append("─" * 30 + "\n", style="dim")
        acc_text.append("历史仓位 (最近 10 笔)\n", style="bold")
        
        for trade in self.position_history.get_recent(10):
            time_str = datetime.fromtimestamp(trade['time']/1000).strftime('%H:%M')
            side = "LONG" if trade['buyer'] else "SHORT"
            price = trade['price']
            qty = trade['qty']
            
            acc_text.append(f"{time_str}  {side:5}  {price}  {qty}\n")
    
    return acc_text
```

#### 2.4 定期刷新

**修改：** `src/main_live.py`
```python
# 主循环中定期刷新历史数据
if self._sync_counter % 300 == 0:  # 每 30 秒
    self.trader.position_history.refresh()
```

### 实现步骤

1. **第 1 步：** API 封装（获取成交记录）
2. **第 2 步：** 历史数据管理类
3. **第 3 步：** UI 渲染历史仓位
4. **第 4 步：** 定期刷新机制
5. **第 5 步：** 测试验证

---

## 3️⃣ 日志系统优化

### 当前问题

1. **调试信息不足** - 关键操作没有详细日志
2. **日志分散** - trades.log、orders.log、pnl.log 分开，难以追踪完整流程
3. **缺少上下文** - 日志中没有请求 ID、响应时间等信息
4. **难以搜索** - JSON 格式不利于快速查看

### 设计方案

#### 3.1 统一日志格式

**采用结构化日志：**
```python
import logging
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler(f'logs/{datetime.now().strftime("%Y%m%d_%H%M%S")}.log', encoding='utf-8'),
        logging.StreamHandler()  # 控制台输出
    ]
)

logger = logging.getLogger('py-shortqt')
```

#### 3.2 日志级别定义

| 级别 | 用途 | 示例 |
|------|------|------|
| `DEBUG` | 详细调试信息 | API 请求参数、响应内容 |
| `INFO` | 正常业务流程 | 开仓、止盈、止损 |
| `WARNING` | 警告信息 | 配置验证失败、重试 |
| `ERROR` | 错误信息 | API 调用失败、异常 |
| `CRITICAL` | 严重错误 | 程序崩溃、数据损坏 |

#### 3.3 关键操作日志

**开仓流程：**
```python
logger.info(f"[OPEN] 开仓请求：side={side}, size={size}, price={price}")
logger.debug(f"[OPEN] API 参数：{params}")

try:
    order = api.place_order(...)
    logger.info(f"[OPEN] 开仓成功：orderId={order['orderId']}, status={order['status']}")
    logger.debug(f"[OPEN] 订单详情：{order}")
except Exception as e:
    logger.error(f"[OPEN] 开仓失败：{type(e).__name__}: {e}", exc_info=True)
    raise
```

**止盈止损流程：**
```python
logger.info(f"[TP/SL] 下止盈止损单：entry={entry_price}, tp={tp_price}, sl={sl_price}")
logger.debug(f"[TP/SL] 配置：mode={config['stop_loss']['limit_mode']}, offset={config['stop_loss']['limit_offset']}")

# 记录 API 调用
start_time = time.time()
sl_order = api.place_algo_order(**sl_algo_params)
elapsed = time.time() - start_time

logger.info(f"[TP/SL] 止损单已下：algoId={sl_order['algoId']}, elapsed={elapsed:.3f}s")
```

**市价全平：**
```python
logger.info(f"[CLOSE] Z 键市价全平：side={side}, size={size}")
logger.debug(f"[CLOSE] 撤销订单：tp_order={self.tp_order}, sl_order={self.sl_order}")

# 撤销所有订单
api.cancel_all_orders(symbol)
api.cancel_all_open_orders(symbol)
logger.info(f"[CLOSE] 已撤销所有挂单")

# 市价平仓
close_order = api.place_order(...)
pnl = calculate_pnl(...)
logger.info(f"[CLOSE] 市价全平完成：price={close_price}, pnl={pnl:+.6f} USDT")
```

#### 3.4 性能日志

**API 调用时间：**
```python
class BinanceClient:
    def _request(self, method, path, params, signed=False):
        start = time.time()
        try:
            response = ...
            elapsed = time.time() - start
            logger.debug(f"[API] {method} {path} - {response.status_code} - {elapsed:.3f}s")
            return response.json()
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[API] {method} {path} - FAILED - {elapsed:.3f}s - {e}")
            raise
```

**订单响应时间：**
```python
# 记录从下单到成交的时间
order_submit_time = time.time()
# ... 等待成交 ...
order_fill_time = time.time()
fill_latency = order_fill_time - order_submit_time
logger.info(f"[FILL] 成交延迟：{fill_latency:.3f}s")
```

#### 3.5 数据收集增强

**当前缺失的数据：**

| 数据 | 用途 | 收集方式 |
|------|------|---------|
| 滑点 | 策略优化 | 成交价 - 触发价 |
| 手续费 | PnL 计算 | 订单响应中的 commission |
| 成交延迟 | 性能分析 | 下单时间 - 成交时间 |
| 撤单原因 | 问题分析 | 订单状态变更日志 |
| API 错误率 | 稳定性监控 | 错误计数/总请求数 |

**实现：**
```python
# 滑点记录
if order_type == 'STOP_MARKET':
    slippage = fill_price - trigger_price
    logger.info(f"[SLIPPAGE] 止损滑点：{slippage:.2f} ({slippage/trigger_price*100:.2f}%)")

# 手续费记录
commission = Decimal(order.get('commission', '0'))
logger.info(f"[FEE] 手续费：{commission} {order.get('commissionAsset', 'USDC')}")

# 成交延迟
fill_latency = fill_time - submit_time
logger.info(f"[LATENCY] 成交延迟：{fill_latency:.3f}s")
```

#### 3.6 日志分析工具

**新增：** `tools/log_analyzer.py`
```python
"""日志分析工具"""

def analyze_pnl(log_file: str) -> dict:
    """分析 PnL 统计"""
    # 解析日志中的 PnL 记录
    # 返回：总盈亏、胜率、平均盈亏等

def analyze_latency(log_file: str) -> dict:
    """分析延迟统计"""
    # 解析 API 调用时间、成交延迟
    # 返回：平均延迟、最大延迟、P95 延迟等

def export_to_csv(log_file: str, output: str):
    """导出为 CSV"""
    # 便于 Excel 分析
```

### 实现步骤

1. **第 1 步：** 统一日志配置（logging 模块）
2. **第 2 步：** 关键操作添加详细日志
3. **第 3 步：** API 调用时间记录
4. **第 4 步：** 数据收集增强（滑点、手续费、延迟）
5. **第 5 步：** 日志分析工具

---

## 📅 开发计划

### 阶段 1：多交易对支持（预计 3-4 天）

| 任务 | 预计时间 | 优先级 |
|------|---------|--------|
| 交易规则 API 封装 | 2 小时 | 🔴 高 |
| 配置管理器集成 | 2 小时 | 🔴 高 |
| UI 支持交易对显示 | 2 小时 | 🟡 中 |
| 订单计算动态精度 | 4 小时 | 🔴 高 |
| 全面测试 | 4 小时 | 🔴 高 |

### 阶段 2：历史仓位查询（预计 2-3 天）

| 任务 | 预计时间 | 优先级 |
|------|---------|--------|
| API 封装（成交记录） | 2 小时 | 🔴 高 |
| 历史数据管理类 | 3 小时 | 🟡 中 |
| UI 渲染历史仓位 | 4 小时 | 🟡 中 |
| 定期刷新机制 | 2 小时 | 🟢 低 |

### 阶段 3：日志系统优化（预计 3-4 天）

| 任务 | 预计时间 | 优先级 |
|------|---------|--------|
| 统一日志配置 | 2 小时 | 🔴 高 |
| 关键操作日志 | 4 小时 | 🔴 高 |
| API 调用时间记录 | 2 小时 | 🟡 中 |
| 数据收集增强 | 4 小时 | 🟡 中 |
| 日志分析工具 | 4 小时 | 🟢 低 |

### 总计

- **预计工期：** 8-11 天
- **代码量：** 约 1500-2000 行
- **测试覆盖率：** 目标 80%+

---

## 🧪 测试计划

### 多交易对测试

| 交易对 | 测试项 | 预期结果 |
|--------|--------|---------|
| ETHUSDC | 开仓、止盈、止损 | 正常 |
| BTCUSDC | 精度处理 | 正确（5 位小数） |
| SOLUSDC | 最小名义价值 | 20 USDC 检查 |

### 历史仓位测试

| 场景 | 测试项 | 预期结果 |
|------|--------|---------|
| 新账户 | 无历史记录 | 显示"无历史数据" |
| 有历史 | 显示最近 10 笔 | 数据准确 |
| 切换交易对 | 只显示当前交易对 | 数据过滤正确 |

### 日志系统测试

| 场景 | 测试项 | 预期结果 |
|------|--------|---------|
| 开仓 | 完整日志记录 | DEBUG 级别详细信息 |
| API 错误 | 错误日志 | ERROR 级别 + 堆栈跟踪 |
| 性能分析 | 延迟记录 | 毫秒级精度 |

---

## 📋 验收标准

### 多交易对支持

- [ ] 可在配置文件中修改交易对
- [ ] 自动获取并应用交易规则（精度、限制）
- [ ] 开仓、止盈、止损计算正确
- [ ] UI 显示正确的交易对

### 历史仓位查询

- [ ] TUI 显示最近 10 笔历史成交
- [ ] 只显示当前交易对的数据
- [ ] 数据准确（与币安 APP 一致）
- [ ] 定期刷新（30 秒）

### 日志系统优化

- [ ] 统一日志格式（结构化）
- [ ] 关键操作有详细日志
- [ ] API 调用时间记录
- [ ] 滑点、手续费、延迟数据收集
- [ ] 日志分析工具可用

---

## 🔗 相关文档

- [v1.2.0 开发文档](./VERSION_1.2.0.md)
- [币安 API 文档](https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures)

---

_文档版本：1.0_
_创建日期：2026-03-21_
_作者：老王_
