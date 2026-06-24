# py-shortqt v1.10.0 - Maker Scalper 剥头皮交易系统

> 基于币安 Futures API 的 ETHUSDC 合约自动交易系统
> **实盘交易 | 分批建仓 | 移动端 Web 看板 | 移动止损 | 浮亏保护 | TUI 设置 | 剥头皮评分**

---

## 版本信息

- **当前版本：** v1.10.0
- **发布日期：** 2026-06-24
- **核心功能：** 移动端 Web 看板与下单 + 分批建仓 + 主动成交比率 + ATR14 24h 百分位 + 音效节流
- **API 版本：** 币安 Futures 新 API (`developers.binance.com`)

---

## v1.10.0 新功能

### 移动端 Web 看板与下单

手机浏览器访问 PC 同进程 Web 服务，实时看 K 线、监控仓位、执行交易操作。

**核心特性：**
- 内嵌 aiohttp HTTP + WebSocket 服务（端口 8099），与 TUI 共享同一交易引擎
- TradingView Lightweight Charts 蜡烛图 + 成交量柱，支持触摸缩放/平移
- 底部三 Tab：行情交易 / 历史统计 / 设置
- 做多 / 做空 / 市价全平 / 撤单 / 提前平仓 — 全部触摸操作
- 市价全平二次确认弹窗（2 秒倒计时），其余操作即时 Toast 反馈
- 历史持仓懒加载无限滚动
- 设置面板 5 子标签，与 TUI 配置完整对齐
- 随机 token 认证，内网访问

### 主动成交比率 (Taker Buy/Sell Ratio)

- 基于 `@aggTrade` WebSocket 流，5 分钟滚动窗口
- 区分买方主动 (taker buy) vs 卖方主动 (taker sell) 成交量占比
- TUI 流动性指标行末 + Web 盘面指标区同步显示

### ATR(14) 24h 滚动百分位

- 每小时重算过去 24h 所有 1min K 线 ATR14% 分布 (P50/P75/P95)
- TUI 显示：`ATR(14): 2.14 (0.12%) [P72 🟡]`
- Web 盘面指标区同步显示当前百分位排名

### 音效节流 + Web 音效

- 分批模式下开仓/平仓音效分开 5 秒节流，避免频繁播放
- Web 端收到事件时通过 Web Audio API 播放方波提示音
- 有 Web 客户端连接时 TUI 端自动静音（手机独享）

### 其他改进

- `position_history` 改用 `deque(maxlen=500)` 限制内存
- TUI 系统设置新增代理和 Web 服务配置字段
- 本地托管 tv-charts.umd.js，断网可用
- 跨日 K 线补齐（凌晨启动 ATR14 不中断）

---

## v1.9.0 新功能

### 分批建仓模式

解决资金量增大后单一限价单无法吃下足够流动性的问题。

**核心特性：**
- 将一笔大单拆为 X 笔阶梯分布的限价单（2~50 笔可配）
- 数量分配：均分 / 递增 / 递减 / 随机
- 价格阶梯：固定点数 / 百分比 / ATR14 系数
- 逐笔成交后自动挂独立止盈 + 更新统一止损
- Maker 优先（GTX Post-Only），被拒后降级 GTC 补挂
- ← 键上下文感知撤单（区分挂单撤单 / 提前平仓撤单）
- ↑↓ 智能补单（基于当前行情重新计算阶梯）
- 全部止盈成交自动结束轮次，撤销剩余挂单
- 浮亏保护适配（禁止补单 + 均价止损）

### WebSocket 可靠性修复

- 应用层心跳 + 超时强制重连（60s 无消息）
- Listen Key 自动轮换（重连时获取新 key）
- REST 轮询兜底（WS 断开时 3s 间隔轮询）
- TUI 账户面板 WS 状态指示灯（🟢🟡🔴）

---

## v1.8.x 新功能

- **v1.8.1**：R 键重置脱敏基数（金额脱敏后重新随机偏移）
- **v1.8.0**：统计周期可配置（近 24h / 自然日）+ TUI 金额脱敏
- **v1.7.12**：BNB 手续费换算历史价格 + 空闲轮询降频 + BNB 价格增量缓存

---

## 完整功能列表

| 功能 | 版本 | 说明 |
|------|------|------|
| 移动端 Web 看板与下单 | v1.10.0 | 手机浏览器 K 线 + 监控 + 交易 |
| 主动成交比率 | v1.10.0 | Taker Buy/Sell 5min 滚动窗口 |
| ATR14 24h 百分位 | v1.10.0 | P50/P75/P95 波动率评价体系 |
| 分批建仓 | v1.9.0 | 2~50 笔阶梯限价单 + 独立止盈 |
| WS 可靠性修复 | v1.9.0 | 心跳 + Listen Key 轮换 + REST 兜底 |
| 金额脱敏 | v1.8.0 | TUI 金额显示相对百分比 |
| 24h 交易统计 | v1.7.0 | 完整轮次追踪 + 胜率/盈亏比 |
| 历史持仓面板 | v1.7.0 | 最近 10 条持仓卡片 |
| 音效提醒 | v1.7.0 | 成交/止损/超时自动播放 |
| 剥头皮评分 | v1.6.0 | 三维评分 + 方向预测 |
| 移动止损 | v1.5.0 | 浮盈自动上移止损 |
| 浮亏保护 | v1.5.0 | 超时浮亏自动保本 |
| 动态指标系统 | v1.4.0 | 波动率/流动性/趋势实时更新 |
| TUI 设置面板 | v1.2.0 | S 键可视化配置 |
| Z 键市价全平 | v1.2.0 | 一键平仓 + 撤销挂单 |

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp config/accounts.json.example config/accounts.json
```

编辑 `config/accounts.json`，填入币安 API Key。**重要：** 在币安官网添加服务器 IP 到 API 白名单。

### 3. 运行

```bash
# Windows 双击启动（推荐）
双击 启动.bat

# 命令行
python launcher.py       # 启动器（选择模式/账户）
python src/main_live.py  # 直接实盘
```

---

## 操作说明

### 主交易界面

| 按键 | 非分批模式 | 分批模式 |
|------|-----------|---------|
| `↑` | 做多 | 开仓/补单 |
| `↓` | 做空 | 开仓/补单 |
| `←` | 撤销挂单 | 上下文撤单（挂单/提前平仓） |
| `→` | 提前平仓 | 提前平仓 |
| `Z` | 市价全平 | 市价全平 |
| `H` | 手动同步 | 手动同步 |
| `S` | 设置面板 | 设置面板 |
| `R` | 重置脱敏基数 | 重置脱敏基数 |
| `Q` | 退出 | 退出 |

### 移动端 Web 操作

| PC 键盘 | 移动端等价操作 |
|---------|-------------|
| `↑` 做多 | [做多] 按钮 → Toast |
| `↓` 做空 | [做空] 按钮 → Toast |
| `→` 提前平 | [提前平] 按钮 → Toast |
| `←` 撤单 | [撤单] 按钮 → Toast |
| `Z` 市价全平 | [市价平] → 确认弹窗（2 秒倒计时） |
| `S` 设置 | 底部 Tab → 设置覆盖层 |
| `R` 脱敏重置 | 点击可用余额 → 确认弹窗 |

---

## 配置说明

### config/runtime.json

```json
{
    "take_profit": {
        "mode": "atr14",
        "points": 0.15,
        "percent": 2.0,
        "atr14_coefficient": 0.34
    },
    "stop_loss": {
        "trigger_mode": "atr14",
        "trigger_points": 2.0,
        "trigger_percent": 0.5,
        "limit_mode": "queue",
        "limit_offset": 25.0,
        "atr14_coefficient": 1.5
    },
    "stop_market": {
        "max_loss_percent": 25.0
    },
    "leverage": {
        "api": 5,
        "actual": 3
    },
    "order_timeout_seconds": 2.0,
    "trailing_stop": {
        "enabled": true,
        "grid_count": 6
    },
    "loss_protection": {
        "enabled": true,
        "trigger_minutes": 1.5
    },
    "sound": {
        "enabled": true
    },
    "price_range": {
        "minutes": 30
    },
    "stats_period": {
        "mode": "calendar_day",
        "timezone": "+8"
    },
    "privacy": {
        "enabled": true
    },
    "batch_mode": {
        "enabled": false,
        "count": 5,
        "distribution": "equal",
        "ladder_mode": "fixed",
        "ladder_min": 1.0,
        "ladder_max": 10.0
    },
    "proxy": {
        "enabled": false,
        "host": "127.0.0.1",
        "port": 7890
    },
    "web_ui": {
        "enabled": false,
        "port": 8099
    }
}
```

---

## 项目结构

```
py-shortqt/
├── launcher.py               # 启动器
├── 启动.bat                  # Windows 启动脚本
├── VERSION                   # 版本号
├── config/                   # 配置文件
│   ├── accounts.json         # API Key（不提交）
│   ├── runtime.json          # 运行时配置
│   └── runtime.json.auto     # 配置模板
├── sounds/                   # 音效文件
│   └── ding.wav              # 提示音
├── src/
│   ├── config/
│   │   ├── manager.py        # 配置管理器
│   │   └── validator.py      # 配置验证器
│   ├── indicators/
│   │   ├── manager.py        # 指标管理器（统一入口）
│   │   ├── volatility.py     # 波动率分析 + ATR14 百分位
│   │   ├── liquidity.py      # 流动性分析（买卖深度）
│   │   ├── scorer.py         # 剥头皮评分系统
│   │   ├── tick_tracker.py   # Tick 级震荡检测
│   │   ├── taker_ratio.py    # 主动成交比率（v1.10.0）
│   │   └── price_range.py    # 价格范围追踪
│   ├── ui/
│   │   ├── live_ui.py        # 主交易界面
│   │   └── settings_ui.py    # 设置界面
│   ├── trading/
│   │   ├── live.py           # 实盘交易核心（分批建仓）
│   │   ├── trailing_stop.py  # 移动止损模块
│   │   └── loss_protection.py # 浮亏保护模块
│   ├── web/                  # 移动端 Web UI（v1.10.0）
│   │   ├── server.py         # HTTP + WebSocket 服务
│   │   └── static/
│   │       ├── index.html    # 移动端 SPA
│   │       └── tv-charts.umd.js  # K 线图库（本地托管）
│   ├── main_live.py          # 实盘入口
│   ├── websocket.py          # 行情 WebSocket（组合流 + aggTrade）
│   ├── data_collector.py     # 数据收集器
│   └── logger.py             # 交易日志系统
├── data/                     # 运行数据（不提交）
│   ├── klines/               # K 线数据
│   ├── orderbook/            # 订单簿数据
│   └── metrics/              # 指标数据
├── logs/                     # 运行日志（不提交）
├── requirements.txt          # Python 依赖
└── README.md                 # 本文件
```

---

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.10.0 | 2026-06-24 | 移动端 Web 看板与下单 + 主动成交比率 + ATR14 24h 百分位 + 音效节流 |
| v1.9.0 | 2026-06-23 | 分批建仓模式 + WebSocket 可靠性修复 |
| v1.8.1 | 2026-06-22 | R 键重置脱敏基数 |
| v1.8.0 | 2026-06-22 | 统计周期可配置 + TUI 金额脱敏 |
| v1.7.14 | 2026-06-21 | 时间戳不同步修复 |
| v1.7.12 | 2026-06-20 | BNB 手续费换算 + 空闲轮询降频 |
| v1.7.0 | 2026-05-22 | 24h 交易统计 + 历史持仓 + 音效提醒 + ATR 指标 |
| v1.6.0 | 2026-05-20 | 剥头皮评分系统 + Tick 震荡检测 + WS 组合流修复 |
| v1.5.0 | 2026-04-02 | 移动止损 + 浮亏保护 |
| v1.4.0 | 2026-03-28 | 动态指标系统 |
| v1.2.0 | 2026-03-21 | TUI 设置模块 + Z 键全平 |
| v1.0.x | 2026-03-19 | 初始版本 |

---

## 风险提示

1. **实盘交易有风险** — 请使用闲置资金
2. **API Key 安全** — 不要泄露，不要提交到 Git
3. **止损非万能** — 极端行情可能滑点
4. **本软件仅供学习研究** — 不构成投资建议

---

## 开发团队

**Morita Entertainment Lab.**

- 执行总裁：老王
- 技术顾问：杰哥

---

## 许可证

MIT License

---

_最后更新：2026-06-24_
