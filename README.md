# py-shortqt v1.2.0 - Maker Scalper 剥头皮交易系统

> 基于币安 Futures API 的 ETHUSDC 合约自动交易系统
> **支持实盘模式和模拟模式 | 新增 TUI 设置模块**

---

## 📌 版本信息

- **当前版本：** v1.2.0
- **发布日期：** 2026-03-21
- **核心功能：** 实盘交易 + 模拟交易 + TUI 设置模块
- **API 版本：** 币安 Futures 新 API (`developers.binance.com`)

---

## 🎯 v1.2.0 新功能

### TUI 设置模块

按 `S` 键进入设置面板，无需手动编辑配置文件：

- ✅ **止盈双模式** - 固定点数 / 百分比
- ✅ **止损双模式** - 触发价（固定/百分比）+ 实际止损价（同向价 1/自定义滑点）
- ✅ **保底止损** - 最大损失比例（基于开仓前保证金）
- ✅ **双杠杆设置** - API 杠杆 / 实际杠杆
- ✅ **配置验证** - 自动验证配置合理性
- ✅ **实时计算预览** - 显示预估 PnL 和盈亏比

### 其他新功能

- ✅ **WebSocket 状态显示** - 头部显示行情和订单连接状态
- ✅ **Z 键市价全平** - 一键市价全平，自动撤销所有挂单
- ✅ **H 键手动同步** - 手动同步持仓状态
- ✅ **启动器优化** - 支持选择交易模式和账户

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

1. 复制配置模板：
```bash
cp config/accounts.json.example config/accounts.json
```

2. 编辑 `config/accounts.json`，填入你的币安 API Key：
```json
{
    "accounts": [
        {
            "name": "主账号",
            "api_key": "YOUR_BINANCE_API_KEY",
            "api_secret": "YOUR_BINANCE_API_SECRET",
            "testnet": false,
            "note": "实盘交易账户"
        },
        {
            "name": "测试网账号",
            "api_key": "YOUR_TESTNET_API_KEY",
            "api_secret": "YOUR_TESTNET_API_SECRET",
            "testnet": true,
            "note": "测试网络，不产生真实交易"
        }
    ],
    "settings": {
        "default_account": "主账号",
        "risk_warning": "⚠️ 实盘交易有风险，请谨慎操作",
        "ip_whitelist_reminder": "请确保已在币安 API 设置中添加本机外网 IP 到白名单"
    }
}
```

3. **重要：** 在币安官网添加服务器 IP 到 API 白名单

### 3. 运行程序

**方式 1：双击启动脚本（推荐）**
```bash
# Windows
双击 启动.bat
```

**方式 2：命令行启动**
```bash
# 使用启动器（可选择模式和账户）
python launcher.py

# 直接启动实盘模式
python src/main_live.py

# 直接启动模拟模式
python src/main_sim.py
```

**启动流程：**
1. 运行启动脚本
2. 选择交易模式（1=实盘，2=模拟）
3. 选择账户（仅实盘需要）
4. 程序自动初始化并进入交易界面

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
| `Z` | 市价全平（有持仓时） |
| `Q` | 退出程序 |

### 设置界面

| 按键 | 功能 |
|------|------|
| `↑↓` | 切换配置字段 |
| `←→` | 调整数值或切换选项 |
| `Enter` | 进入编辑模式（数值字段） |
| `S` | 保存并退出 |
| `D` | 重置为默认配置 |
| `B` | 备份当前配置 |
| `Esc` | 退出（放弃修改） |

---

## 📊 交易策略

### 核心逻辑

1. **开仓：** Maker 挂单（同向价 1，0 手续费）
2. **止盈：** 可配置（固定点数/百分比）
3. **止损：** 可配置（触发价 + 实际止损价）
4. **保底止损：** 最大损失比例（自动计算）

### 订单类型

| 订单 | 类型 | 接口 | 保证 Maker |
|------|------|------|-----------|
| **开仓** | `LIMIT` | `/fapi/v1/order` | ✅ `priceMatch='QUEUE'` |
| **止盈** | `LIMIT` | `/fapi/v1/order` | ✅ 价格检查 + 自动重试 |
| **止损** | `STOP` | `/fapi/v1/algoOrder` | ✅ `priceMatch='QUEUE'` 或自定义滑点 |
| **保底止损** | `STOP_MARKET` | `/fapi/v1/algoOrder` | ❌ 市价确保成交 |

### 仓位管理

- **杠杆设置：** 双杠杆（API 杠杆 / 实际杠杆）
- **仓位计算：** 全仓进出，滚仓模式
- **最小开仓：** 20 USDC 名义价值（币安要求）

---

## ⚙️ 配置说明

### config/runtime.json

```json
{
    "take_profit": {
        "mode": "fixed",           // fixed | percentage
        "points": 1.00,            // 固定点数模式
        "percent": 0.36            // 百分比模式
    },
    "stop_loss": {
        "trigger_mode": "fixed",   // fixed | percentage
        "trigger_points": 3.00,    // 触发价（固定点数）
        "trigger_percent": 0.50,   // 触发价（百分比）
        "limit_mode": "queue",     // queue | custom
        "limit_offset": 10.50      // 自定义滑点
    },
    "stop_market": {
        "max_loss_percent": 30.00  // 最大损失比例
    },
    "leverage": {
        "api": 100,                // API 杠杆
        "actual": 25               // 实际杠杆
    },
    "order_timeout_seconds": 2.00
}
```

### 配置验证规则

| 参数 | 最小值 | 最大值 | 说明 |
|------|--------|--------|------|
| 止盈点数 | 0.01 | 100 | 固定点数模式 |
| 止盈百分比 | 0.01 | 10 | 百分比模式 |
| 止损触发点数 | 0.01 | 100 | 固定点数模式 |
| 止损触发百分比 | 0.01 | 10 | 百分比模式 |
| 最大损失比例 | 0.1 | 80 | 百分比 |
| API 杠杆 | 1 | 125 | |
| 实际杠杆 | 1 | 125 | 不能大于 API 杠杆 |

---

## 📁 项目结构

```
py-shortqt/
├── launcher.py               # Python 启动器
├── 启动.bat                  # Windows 启动脚本
├── config/                   # 配置文件
│   ├── accounts.json         # API Key 配置（不提交）
│   ├── runtime.json          # 运行时配置（可修改）
│   └── runtime.json.example  # 配置模板
├── src/                      # 源代码
│   ├── config/
│   │   ├── manager.py        # 配置管理器
│   │   └── validator.py      # 配置验证器
│   ├── ui/
│   │   ├── live_ui.py        # 主交易界面
│   │   └── settings_ui.py    # 设置界面
│   ├── trading/
│   │   └── live.py           # 实盘交易核心
│   ├── api/
│   │   └── binance_client.py # 币安 API 封装
│   ├── main_live.py          # 实盘入口
│   ├── main_sim.py           # 模拟入口
│   ├── websocket.py          # 行情 WebSocket
│   └── logger.py             # 交易日志系统
├── tests/                    # 测试脚本
│   └── test_startup.py       # 启动测试
├── docs/                     # 文档
│   ├── VERSION_1.2.0.md      # v1.2.0 开发文档
│   └── TEST_PLAN_1.2.0.md    # 测试计划
├── logs/                     # 运行日志（不提交）
├── requirements.txt          # Python 依赖
├── README.md                 # 项目说明
└── .gitignore                # Git 忽略文件
```

---

## 📝 日志系统

### 日志位置

- **系统日志：** `logs/system.log`
- **交易日志：** `logs/YYYYMMDD_HHMMSS/`

### 日志文件

| 文件 | 内容 | 格式 |
|------|------|------|
| `trades.log` | 交易动作（开仓、止盈、止损） | JSON |
| `orders.log` | 订单信息（挂单、撤单） | JSON |
| `positions.log` | 持仓信息 | JSON |
| `pnl.log` | PnL 记录 | JSON |
| `signals.csv` | 信号特征（用于量化分析） | CSV |
| `system.log` | 系统运行日志（报错、调试） | 文本 |

### 日志示例

**trades.log：**
```json
{"timestamp":"2026-03-21 18:29:54.942","action":"开仓成交","details":"LONG @ 2153.78 x 0.011 | 手续费 0 USDC"}
{"timestamp":"2026-03-21 18:30:59.137","action":"平仓成交","details":"PnL: +0.003410 USDT", "pnl": 0.00341}
```

---

## 🧪 测试

运行启动测试：

```bash
python tests/test_startup.py
```

预期输出：
```
============================================================
py-shortqt v1.2.0 启动测试
============================================================

[OK] 测试 1: 语法检查...
  [OK] main_live.py
  [OK] live.py
  [OK] manager.py
  [OK] validator.py
  [OK] live_ui.py
  [OK] settings_ui.py

[OK] 测试 2: get_stop_loss_params 参数签名...
  [OK] 参数签名正确 (包含 symbol 参数)

[OK] 测试 3: API 参数完整性...
  [OK] 包含参数：symbol
  [OK] 包含参数：side
  [OK] 包含参数：type

[OK] 测试 4: 设置界面调用参数...
  [OK] 设置界面调用正确 (包含 symbol 参数)

============================================================
测试结果汇总
============================================================
  [OK] 通过：语法检查
  [OK] 通过：get_stop_loss_params 参数
  [OK] 通过：API 参数完整性
  [OK] 通过：设置界面调用参数

总计：4/4 通过

[OK] 所有测试通过！
```

---

## ⚠️ 风险提示

1. **实盘交易有风险** - 请使用闲置资金
2. **API Key 安全** - 不要泄露，不要提交到 Git
3. **止损是最后一道防线** - 极端行情可能滑点
4. **本软件仅供学习研究** - 不构成投资建议

---

## 📚 参考文档

- **币安 Futures API：** https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures
- **订单类型说明：** https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures/trade/rest-api
- **Algo Order API：** `/fapi/v1/algoOrder`
- **开发文档：** `docs/VERSION_1.2.0.md`

---

## 🔄 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.2.0 | 2026-03-21 | TUI 设置模块、Z 键全平、启动器优化 |
| v1.1.1 | 2026-03-20 | 实盘版（新 API 文档，Algo Order） |
| v1.0.x | 2026-03-19 | 模拟版（仅供测试） |

详细历史版本文档见 `docs/` 文件夹。

---

## 💼 开发团队

**Morita Entertainment  Lab.**

- 执行总裁：老王
- 技术顾问：杰哥

---

## 📄 许可证

MIT License

---

_最后更新：2026-03-21_
