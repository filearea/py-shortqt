# py-shortqt v1.5.0 - Maker Scalper 剥头皮交易系统

> 基于币安 Futures API 的 ETHUSDC 合约自动交易系统
> **支持实盘/模拟 | 移动止损 | 浮亏保护 | TUI 设置 | 动态指标 | 完整日志**

---

## 📌 版本信息

- **当前版本：** v1.5.0
- **发布日期：** 2026-04-02
- **核心功能：** 实盘交易 + 模拟交易 + 移动止损 + 浮亏保护 + TUI 设置 + 完整日志 + 动态指标 + 实时数据记录
- **API 版本：** 币安 Futures 新 API (`developers.binance.com`)

---

## 🆕 v1.5.0 新功能

### 浮盈移动止损（Trailing Stop）

盈利时自动上移止损，逐级锁定利润，避免过早平仓。

**核心特性：**
- ✅ 开仓价 → 目标止盈价之间均分 N 格
- ✅ 价格每突破一格，止损自动上移一格
- ✅ 支持多单/空单对称逻辑
- ✅ 超过 8 格时自动滚动策略（币安条件单上限）
- ✅ 订单类型：Algo Order `STOP` + `priceMatch='QUEUE'`（Maker 成交）

**配置示例：**
```json
{
  "trailing_stop": {
    "enabled": true,
    "grid_count": 5
  }
}
```

**算法示意（多单，开仓价 2100，止盈 2115，5 格）：**
```
2115  ← 目标止盈
2112  ← 第4格（触发后止损设在 2109）
2109  ← 第3格（触发后止损设在 2106）
2106  ← 第2格（触发后止损设在 2103）
2103  ← 第1格（触发后止损设在 2100 保本）
2100  ← 开仓价
```

---

### 浮亏保护止损（Loss Protection）

开仓一段时间后仍浮亏，自动将止盈下移到开仓价，确保反弹时保本出场。

**核心特性：**
- ✅ 可配置触发时间（1-60 分钟）
- ✅ 仅在浮亏状态下触发
- ✅ 止盈单修改为开仓价（Maker 保本）
- ✅ 触发后不再重复操作

**配置示例：**
```json
{
  "loss_protection": {
    "enabled": true,
    "trigger_minutes": 5
  }
}
```

---

### TUI 设置面板增强

`S` 键进入设置面板，新增移动止损和浮亏保护配置项：
- 移动止损：开关 + 格数调节（3-10）
- 浮亏保护：开关 + 触发时间调节（1-60 分钟）

---

## 📋 完整功能列表

| 功能 | 版本 | 说明 |
|------|------|------|
| 移动止损 | v1.5.0 | 浮盈自动上移止损，锁定利润 |
| 浮亏保护 | v1.5.0 | 超时浮亏自动保本 |
| 动态指标系统 | v1.4.0 | 8 项指标 + 综合评分(0-100) + 交易建议 |
| 实时数据记录 | v1.4.1 | K线/订单簿/指标数据自动记录 |
| 账户余额日志 | v1.3.3 | 启动/平仓/关闭时记录余额 |
| 完整日志系统 | v1.3.0 | 系统/市场/交易/信号 CSV |
| TUI 设置模块 | v1.2.0 | S 键可视化配置 |
| Z 键市价全平 | v1.2.0 | 一键平仓 + 撤销挂单 |
| 双杠杆设置 | v1.2.0 | API 杠杆 / 实际杠杆分离 |
| Maker 挂单 | v1.1.0 | priceMatch='QUEUE' 确保 Maker |

---

## 🚀 快速开始

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
python launcher.py      # 启动器（选择模式/账户）
python src/main_live.py  # 直接实盘
python src/main_sim.py   # 直接模拟
```

---

## 🎮 操作说明

### 主交易界面

| 按键 | 功能 |
|------|------|
| `↑` | 做多（挂买单 @ 买一价） |
| `↓` | 做空（挂卖单 @ 卖一价） |
| `←` | 撤销挂单 |
| `→` | 提前平仓（挂单模式） |
| `S` | 进入设置面板 |
| `H` | 手动同步持仓 |
| `Z` | 市价全平 |
| `Q` | 退出程序 |

### 设置界面

| 按键 | 功能 |
|------|------|
| `↑↓` | 切换配置字段 |
| `←→` | 调整数值或切换选项 |
| `Enter` | 进入编辑模式 |
| `S` | 保存并退出 |
| `D` | 重置为默认配置 |
| `B` | 备份当前配置 |
| `Esc` | 退出（放弃修改） |

---

## ⚙️ 配置说明

### config/runtime.json

```json
{
    "take_profit": {
        "mode": "fixed",
        "points": 1.00,
        "percent": 0.36
    },
    "stop_loss": {
        "trigger_mode": "fixed",
        "trigger_points": 3.00,
        "trigger_percent": 0.50,
        "limit_mode": "queue",
        "limit_offset": 10.50
    },
    "stop_market": {
        "max_loss_percent": 30.00
    },
    "trailing_stop": {
        "enabled": false,
        "grid_count": 5
    },
    "loss_protection": {
        "enabled": false,
        "trigger_minutes": 5
    },
    "leverage": {
        "api": 100,
        "actual": 25
    },
    "order_timeout_seconds": 2.00
}
```

---

## 📊 动态指标系统

实时计算 8 个关键市场指标，提供综合评分 + 交易建议：

| 类别 | 指标 | 说明 |
|------|------|------|
| 波动率 | 1分钟/5分钟振幅 | K 线综合振幅 |
| | 1小时平均振幅 | 近 60 根 K 线平均 |
| | 变化率 | 当前价 vs 1小时前 |
| | ATR(14) | 14 周期平均真实波幅 |
| 流动性 | 买卖价差/价差率 | ask - bid |
| | 市场深度 | 前 10 档挂单总量 |

**综合评分：**
- 🟢 **适合** (60-100) — 波动率和流动性良好
- 🟡 **观望** (30-59) — 部分指标不达标
- 🔴 **暂停** (0-29) — 市场条件不佳

---

## 📁 项目结构

```
py-shortqt/
├── launcher.py               # 启动器
├── 启动.bat                  # Windows 启动脚本
├── config/                   # 配置文件
│   ├── accounts.json         # API Key（不提交）
│   ├── runtime.json          # 运行时配置
│   └── runtime.json.example  # 配置模板
├── src/
│   ├── config/
│   │   ├── manager.py        # 配置管理器
│   │   └── validator.py      # 配置验证器
│   ├── ui/
│   │   ├── live_ui.py        # 主交易界面
│   │   └── settings_ui.py    # 设置界面
│   ├── trading/
│   │   ├── live.py           # 实盘交易核心
│   │   ├── trailing_stop.py  # 移动止损模块
│   │   └── loss_protection.py # 浮亏保护模块
│   ├── utils/
│   │   └── log.py            # 文件日志工具
│   ├── main_live.py          # 实盘入口
│   ├── main_sim.py           # 模拟入口
│   ├── websocket.py          # 行情 WebSocket
│   ├── data_collector.py     # 数据收集器
│   ├── metrics_recorder.py   # 指标记录器
│   ├── recorder.py           # 数据记录器
│   └── logger.py             # 交易日志系统
├── data/                     # 运行数据（不提交）
│   ├── klines/               # K 线数据
│   ├── orderbook/            # 订单簿数据
│   └── metrics/              # 指标数据
├── logs/                     # 运行日志（不提交）
├── docs/                     # 版本文档
├── tests/                    # 测试脚本
├── requirements.txt          # Python 依赖
└── README.md                 # 本文件
```

---

## 📝 日志系统

| 文件 | 内容 | 格式 |
|------|------|------|
| `trades.log` | 开仓、止盈、止损、余额 | JSON |
| `orders.log` | 挂单、撤单 | JSON |
| `positions.log` | 持仓信息 | JSON |
| `pnl.log` | PnL 记录 | JSON |
| `signals.csv` | 信号特征 | CSV |
| `system.log` | 系统运行日志 | 文本 |

---

## 🔄 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.5.0 | 2026-04-02 | 移动止损 + 浮亏保护 |
| v1.4.1 | 2026-03-29 | 实时数据记录（K线/订单簿/指标） |
| v1.4.0 | 2026-03-28 | 动态指标系统（8项指标+综合评分） |
| v1.3.3 | 2026-03-25 | 账户余额日志 |
| v1.3.0 | 2026-03-23 | 完整日志系统 |
| v1.2.0 | 2026-03-21 | TUI 设置模块、Z 键全平 |
| v1.1.1 | 2026-03-20 | 实盘版（Algo Order） |
| v1.0.x | 2026-03-19 | 模拟版 |

详细版本文档见 `docs/` 文件夹。

---

## ⚠️ 风险提示

1. **实盘交易有风险** — 请使用闲置资金
2. **API Key 安全** — 不要泄露，不要提交到 Git
3. **止损非万能** — 极端行情可能滑点
4. **本软件仅供学习研究** — 不构成投资建议

---

## 💼 开发团队

**Morita Entertainment Lab.**

- 执行总裁：老王
- 技术顾问：杰哥

---

## 📄 许可证

MIT License

---

_最后更新：2026-04-05_
