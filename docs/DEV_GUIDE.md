# py-shortqt 二次开发手册

> 面向接手开发者。覆盖 v1.9.0 / v1.10.0 核心架构、关键模式、常见陷阱。

---

## 一、项目概览

py-shortqt 是基于币安 Futures API 的 ETHUSDC 合约自动交易系统。核心能力：**TUI 实盘交易 + 移动端 Web 看板 + 分批建仓 + 移动止损 + 浮亏保护**。

- **语言**：Python 3.x（异步 asyncio）+ 原生 JavaScript（无框架）
- **TUI**：Rich 库
- **Web**：aiohttp HTTP + WebSocket，纯 HTML/CSS/JS 单文件 SPA
- **图表**：TradingView Lightweight Charts（本地托管）
- **数据存储**：JSONL 文件（按天分片），无数据库

---

## 二、目录结构与职责

```
src/
├── main_live.py          # 主控编排器 LiveTradingBot — 连接所有子系统
├── api/
│   ├── binance_client.py  # 币安 REST API 客户端（所有签名/未签名接口）
│   ├── signature.py       # HMAC-SHA256 签名 + 时间戳同步
│   ├── rate_limiter.py    # 1200 权重/min 滑动窗口限流
│   └── user_stream_ws.py  # 用户数据流 WS（ORDER_TRADE_UPDATE / ACCOUNT_UPDATE）
├── trading/
│   ├── live.py            # 交易引擎 — 4200+ 行，持仓管理/订单/分批/检测
│   ├── trailing_stop.py   # 移动止损（网格化条件单）
│   └── loss_protection.py # 浮亏保护（时间窗口触发）
├── indicators/
│   ├── manager.py          # 指标管理器 — 协调所有子指标
│   ├── volatility.py       # 波动率分析 + ATR14 百分位
│   ├── liquidity.py        # 订单簿深度分析
│   ├── scorer.py           # 剥头皮评分（三维评分+方向预测）
│   ├── tick_tracker.py     # Tick 级震荡检测
│   ├── taker_ratio.py      # 主动成交比率（5min 窗口）
│   └── price_range.py      # N 分钟最高/最低价追踪
├── web/
│   ├── server.py           # HTTP + WebSocket 服务（端口 8099）
│   └── static/
│       ├── index.html      # 移动端 SPA（~3000 行）
│       └── tv-charts.umd.js # K 线图库本地托管
├── ui/
│   ├── live_ui.py          # TUI 主界面渲染
│   └── settings_ui.py      # TUI 设置面板
├── config/
│   ├── manager.py          # 配置管理器（runtime.json 读写/升级/备份）
│   └── validator.py        # 配置校验器
├── websocket.py            # 行情 WebSocket（bookTicker + depth20 + @trade 合成 K 线）
├── recorder.py             # K 线定时拉取 + 订单簿快照保存（JSONL）
├── data_collector.py       # 历史 K 线回填（启动时补齐最近 14 天）
├── metrics_recorder.py     # 指标快照定时保存
└── loggers/                # 结构化日志系统（system / market / trading）
```

---

## 三、启动流程

```
launcher.py → subprocess → src/main_live.py --account <name>
  │
  ├─ 1. 加载 config/settings.py（编译期常量：SYMBOL, TESTNET, LEVERAGE）
  ├─ 2. 加载 config/accounts.json（API Key）
  ├─ 3. LiveTradingBot.__init__() — 创建所有子系统实例
  ├─ 4. _load_proxy_config() — 从 runtime.json 读取代理设置到环境变量
  ├─ 5. LiveTrader.initialize() — 时间同步、设杠杆、启动用户流 WS、清理旧订单
  ├─ 6. _init_historical_klines() — 从本地文件/API 回填 K 线，初始化 ATR14
  ├─ 7. BinanceListener.connect() — 启动行情 WS（双流并行）
  ├─ 8. start_web_server() — 如果 web_ui.enabled，启动 aiohttp 服务
  └─ 9. 主循环 — Rich TUI 刷新 + 键盘输入处理
```

**关键时序**：代理配置在步骤 4 加载（模块级 `_load_proxy_config()`），因此 `runtime.json` 的代理设置必须在启动前配置好。启动后修改代理需要重启或触发 `_reinitialize()`。

---

## 四、核心架构模式

### 4.1 单事件循环模型

所有子系统在同一 asyncio 事件循环中运行：
- TUI（Rich Live）、Web 服务（aiohttp）、行情 WS、用户流 WS、定时轮询任务
- 键盘事件和 HTTP 请求在事件循环中自然串行化，不存在竞态
- Web 服务通过后台 asyncio task 推送状态

### 4.2 成交检测：四层防御

这是系统最核心的设计，需理解每一层的作用：

| 层级 | 机制 | 成本 | 触发条件 |
|------|------|------|---------|
| L1 | 用户流 WS ORDER_TRADE_UPDATE 事件 | 零 API 权重 | 实时事件推送 |
| L2 | bookTicker 价格穿透检测（每帧比较挂单价 vs BBO） | 零 API 权重 | 价格穿透挂单价 |
| L3 | 关键价格监控 _check_key_prices()（TP/SL/平仓价） | 零 API 权重 | 价格穿透止盈/止损价 |
| L4 | REST 兜底轮询（3s 间隔 get_open_orders） | 5 权重/次 | WS 60s 无消息时激活 |
| L4+ | 分批 TP 全局监控 _batch_tp_monitor_loop（5s 间隔） | 5 权重/次 | 分批模式持续运行 |

**关键陷阱**：L4 兜底仅在 WS 超时后激活。如果 WS 每隔几十秒收到一条消息（如 ACCOUNT_UPDATE），`last_msg_ts` 持续刷新，兜底永不激活。这就是 18.8 节描述的"TP 成交检测盲区"——需要 L4+ 全局 TP 监控来填补。

### 4.3 配置热更新

`runtime.json` 可通过 TUI 设置面板或 WebUI 设置页面修改：
- `_apply_settings()` 热更新杠杆、移动止损、浮亏保护、价格范围等
- 部分配置（代理、Web 端口）需要 `_reinitialize()` 或重启
- ConfigManager 使用点号路径访问嵌套键：`config.get('batch_mode.count')`

### 4.4 数据持久化

- K 线：`data/klines/ETHUSDC/YYYY-MM-DD.jsonl`，每行一个 JSON 对象
- 订单簿：`data/orderbook/ETHUSDC/YYYY-MM-DD.jsonl`
- 指标快照：`data/metrics/ETHUSDC_YYYY-MM-DD.jsonl`
- 无数据库，全靠文件系统
- Recorder 内置脏数据检测和自愈修正

---

## 五、行情 WebSocket 架构

**双流并行**（`src/websocket.py`）：

```
流 1: wss://fstream.binance.com/stream?streams=ethusdc@bookTicker/ethusdc@depth20@100ms
流 2: wss://fstream.binance.com/ws/ethusdc@trade
```

- 拆分为双流是因为代理/GWF 可能阻断组合流中的 `@aggTrade` 和 `@kline_1m`
- 流 2 的 `@trade` 逐笔成交在本地实时合成 1min OHLCV K 线
- 异常价保护：偏离参考价 >10% 的成交不参与合成
- 每条流有独立的重连循环，指数退避（3s–30s）

**用户数据流**（`src/api/user_stream_ws.py`）：
- Listen Key 机制，每 30 分钟 Keep-Alive
- 重连时自动获取新 Listen Key（不复用旧 key）
- 15s 心跳检测，60s 僵尸超时强制重连
- 使用币安 2026-04-23 版 `/private` 端点，白名单事件类型

---

## 六、分批建仓模式

### 6.1 状态机

```
IDLE → PENDING_ONLY（仅有挂单）→ PARTIAL_FILLED（部分成交）
     → ALL_FILLED（全部成交）→ 等待止盈/止损 → round_closed → IDLE
     → EARLY_CLOSE（提前平仓中）→ 可恢复或成交结束
```

### 6.2 核心数据结构

`self.batch_state` 字典包含：
- `batches[]`：每批次的 order_id、price、size、status、tp_order_id
- `sl_order_id / sm_order_id`：统一止损/保底算法单 ID
- `weighted_avg_entry`：已成交批次加权均价（仅开仓成交时重算）
- `tp_backup[]`：提前平仓时的止盈单备份
- `supplement_blocked`：浮亏保护触发后禁止补单
- `max_position_size`：补单量计算的基准（开仓时确定，补单时累加）

### 6.3 补单量公式

```
补单量 = max_position_size − 当前未止盈仓位 − 当前挂单中数量
```

不使用历史已止盈量，避免多次补仓后溢出。

### 6.4 关键陷阱（已修复）

1. **reduceOnly 参数**：双向持仓模式不允许此参数，所有传了的地方都静默失败——已在 v1.10.0 移除全部 6 处
2. **stopPrice vs triggerPrice**：Algo Order API 用 `triggerPrice`，普通订单用 `stopPrice`——混用会导致静默失败
3. **SL/SM 节流丢失**：3 秒节流命中时 `_pending_sl_update` 标志无代码检查——已在价格 tick 循环中增加兜底
4. **补单重复提交**：`_place_batch_orders` 会遍历全部批次而非只提交新增——已修复为仅提交 `status=='pending' and not order_id` 的批次

---

## 七、Web 服务架构

### 7.1 服务端

- aiohttp 嵌入现有事件循环
- Token 认证：配置为空时自动随机生成 32 位 hex，有值时固定持久复用（`web_ui.token` 配置项）
- 4 个后台广播循环：state（1Hz）、kline（1Hz）、depth（2Hz）、events（按需）
- 操作 API 统一路由到 `LiveTradingBot` 方法（与 TUI 按键走相同逻辑路径）

### 7.2 前端

- 纯 HTML/CSS/JS 单文件，零构建工具
- ApeBot 设计系统 CSS 变量 + 深色/浅色双主题
- 底部 3 Tab：行情交易 / 历史统计 / 设置
- Canvas 资产曲线图 + TradingView K 线图
- 颜色体系解耦：价格方向色（红涨绿跌）与语义状态色分离

### 7.3 资产曲线计算（重要）

后端 `_handle_asset_curve` 使用**反向行走算法**：

```
1. current_assets = 可用余额 + 未实现盈亏
2. 收集时间范围内的已平仓持仓记录（close_ts, net_pnl）
3. 生成采样时间点（固定间隔 + 每笔平仓时间点）
4. 从 now 向前走到 start_time：
   running_assets = current_assets
   对每个采样点 t（降序）：
     减去在 t 之后平仓的持仓的 net_pnl
5. 反转得到升序序列
```

平仓时间点合并到采样序列中，确保平仓时刻的盈亏拐点可被触摸查看。

---

## 八、前端 Canvas 图表开发注意事项

### 8.1 CSS 变量读取

Canvas API 无法直接使用 CSS 变量，必须通过 JS 读取：

```javascript
var cs = getComputedStyle(document.documentElement);
var brandColor = cs.getPropertyValue('--color-brand').trim() || '#fcd535';
```

### 8.2 移动端触摸

- Canvas 必须设置 `touch-action: none` 防止浏览器下拉刷新
- `e.preventDefault()` 在 touchstart/touchmove 中调用
- 使用 `canvas.clientWidth` 而非 `getBoundingClientRect().width` 获取稳定宽度

### 8.3 Y 轴方向

Canvas Y 轴：y=0 在顶部，y=max 在底部。数据映射公式：

```javascript
var yOf = function(v) {
    return margin.top + (maxVal - v) / (maxVal - minVal) * drawH;
};
```

Y 轴标签同理：`maxVal - (g/gridLines) * (maxVal - minVal)`

### 8.4 常见陷阱

- **fillStyle 赋值错误**：赋值为非颜色字符串（如数字）会导致渲染静默失败
- **变量名碰撞**：`tipText` 既用于颜色又用于文本内容会导致颜色渲染不可见
- **自然日采样**：必须从 start_time 向前生成，不能从 now 向后回溯（否则 X 轴漂移）

---

## 九、K 线数据处理

### 9.1 数据流

```
币安 REST API → RealtimeRecorder（定时 60s 拉取）
  → 校验（跳过 buy_turnover=0 的脏数据）
  → JSONL 写入 + 内存缓存 + 回调通知指标系统
```

### 9.2 数据校验规则

- K 线闭合超过 10 秒才判定为"已最终确定"
- `volume > 0` 但 `buy_turnover <= 0` → 脏数据，跳过文件写入
- 运行时自愈：每轮轮询后检查文件最后 15 条，发现脏数据从 API 回补
- 回调通知**不跳过**脏数据（deque 必须保持连续，否则 K 线图出现永久缺口）

### 9.3 三层兜底（Web API）

```
内存 deque → JSONL 文件 → 币安 REST API
```

缺口检测基于时间连续性：相邻 K 线时间差 > 60s 视为缺口。

---

## 十、开发规范

### 10.1 版本号

格式 `1.x.x.abcdef`，每次改动本地 commit。详见 `docs/VERSION_1.9.0.md` 和 `docs/VERSION_1.10.0.md`。

### 10.2 环境隔离

- **D 盘开发环境**：所有代码修改在此进行
- **桌面生产环境**：不建议直接修改，先在开发环境验证
- 后端改动需重启服务器才能生效

### 10.3 测试

- 无自动化测试套件
- 修改后需要手动重启服务器并在真机（手机浏览器）验证关键路径
- 分批模式和非分批模式需分别验证

### 10.4 Git 提交

- `config/runtime.json` 和 `config/accounts.json` 不在版本控制中
- `config/runtime.json.auto` 在 `.gitignore` 中（2026-06-30 添加）
- `.bat` 文件除 `启动.bat` 外均不提交

---

## 十一、常见问题排查

| 症状 | 可能原因 | 检查位置 |
|------|---------|---------|
| API 连接超时 | 代理未配置或配置错误 | `runtime.json` proxy 节 |
| WebSocket 无法连接 | 代理不支持 WS 协议 | 检查代理类型（需 HTTP 代理支持 CONNECT） |
| Web 页面 403 | token 未携带或过期 | 检查 URL 中的 `?token=` 参数 |
| 止盈不触发 | reduceOnly 误传 / TP 监控盲区 | 检查 live.py 订单参数 + TP monitor 是否运行 |
| K 线有缺口 | 脏数据被跳过 + 回调正常执行 | 正常行为，deque 连续但文件有空白 |
| ATR14 为 None | 启动时 K 线不足 15 根 | 检查跨日补齐逻辑 |
| 补单量异常 | max_position_size 被覆盖 | 检查是否使用 `+=` 而非 `=` |
| 浮亏保护显示 undefined | entry_info 未设置 | 分批模式需在首笔成交时调用 set_entry_info |
| Web 操作返回"网络错误" | handler 调用了不存在的方法 | 确认调用了 LiveTradingBot 方法而非旧 TradeState |

---

## 十二、关键文件索引

| 想了解... | 看这个文件 |
|-----------|-----------|
| 系统如何启动和连接各组件 | `src/main_live.py` |
| 交易引擎所有逻辑 | `src/trading/live.py` |
| 分批建仓实现细节 | `src/trading/live.py` 中 `batch` 相关方法 |
| Web 后端 API | `src/web/server.py` |
| Web 前端 UI | `src/web/static/index.html` |
| 行情 WebSocket | `src/websocket.py` |
| 用户数据流 WS | `src/api/user_stream_ws.py` |
| K 线数据持久化 | `src/recorder.py` |
| 配置管理 | `src/config/manager.py` |
| 指标计算 | `src/indicators/manager.py` + 各子模块 |
| TUI 界面 | `src/ui/live_ui.py` |
| TUI 设置 | `src/ui/settings_ui.py` |
| 设计文档 v1.9.0 | `docs/VERSION_1.9.0.md` |
| 设计文档 v1.10.0 | `docs/VERSION_1.10.0.md` |

---

_最后更新：2026-06-30_
