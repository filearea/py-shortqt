# py-shortqt v1.1.1 - Maker Scalper 剥头皮交易系统

> 基于币安 Futures API 的 ETHUSDC 合约自动交易系统
> **支持实盘模式和模拟模式**

---

## 📌 版本信息

- **当前版本：** v1.1.1
- **发布日期：** 2026-03-20
- **核心功能：** 实盘交易 + 模拟交易
- **API 版本：** 币安 Futures 新 API (`developers.binance.com`)

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

1. 复制配置模板：
```bash
cp config/accounts.example.json config/accounts.json
```

2. 编辑 `config/accounts.json`，填入你的币安 API Key：
```json
{
    "accounts": [
        {
            "name": "主账号",
            "api_key": "YOUR_API_KEY",
            "api_secret": "YOUR_SECRET",
            "testnet": false
        }
    ]
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
```powershell
# Windows - 统一入口（可选择模式）
python src/main.py

# 直接启动实盘模式
python src/main_live.py

# 直接启动模拟模式
python src/main_sim.py
```

**启动流程：**
1. 运行启动脚本
2. 选择交易模式（1=实盘，2=模拟）
3. 程序自动初始化并进入交易界面

---

## 🎮 操作说明

| 按键 | 功能 |
|------|------|
| `↑` / `W` | 做多（挂买单 @ 买一价） |
| `↓` / `S` | 做空（挂卖单 @ 卖一价） |
| `←` | 撤销挂单 |
| `→` | 提前平仓（挂单模式） |
| `Q` | 退出程序 |

---

## 📊 交易策略

### 核心逻辑

1. **开仓：** Maker 挂单（同向价 1，0 手续费）
2. **止盈：** 开仓价 +1 点（LIMIT 限价单）
3. **止损：** 开仓价 -3 点（STOP 条件限价单）
4. **保底止损：** 强平价 +1 点（STOP_MARKET 条件市价单）

### 订单类型

| 订单 | 类型 | 接口 | 保证 Maker |
|------|------|------|-----------|
| **开仓** | `LIMIT` | `/fapi/v1/order` | ✅ `priceMatch='QUEUE'` |
| **止盈** | `LIMIT` | `/fapi/v1/order` | ✅ 价格检查 + 自动重试 |
| **止损** | `STOP` | `/fapi/v1/algoOrder` | ✅ `priceMatch='QUEUE'` |
| **保底止损** | `STOP_MARKET` | `/fapi/v1/algoOrder` | ❌ 市价确保成交 |

### 仓位管理

- **杠杆设置：** API 设置 100x，实际使用 25x（测试）/ 75x（实盘）
- **仓位计算：** 全仓进出，滚仓模式
- **最小开仓：** 约 1 USDT（25x 杠杆）

---

## 📁 项目结构

```
py-shortqt/
├── 启动.bat              # Windows 启动脚本
├── config/               # 配置文件
│   ├── accounts.json     # API Key 配置（不提交）
│   └── settings.py       # 交易参数配置
├── src/                  # 源代码
│   ├── main.py           # 统一启动入口
│   ├── main_live.py      # 实盘模式
│   ├── main_sim.py       # 模拟模式
│   ├── trader.py         # 模拟交易核心
│   ├── websocket.py      # 币安 WebSocket 连接
│   ├── logger.py         # 交易日志系统
│   └── api/              # 币安 API 封装
│       ├── binance_client.py
│       └── signature.py
├── tests/                # 测试脚本
├── docs/                 # 历史版本文档
├── logs/                 # 运行日志（不提交）
├── requirements.txt      # Python 依赖
├── README.md             # 项目说明
└── .gitignore            # Git 忽略文件
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
{"timestamp":"2026-03-20 18:47:50.020","action":"开仓成交","details":"LONG @ 2148.17 x 0.012"}
{"timestamp":"2026-03-20 18:47:56.533","action":"止盈成交","pnl":0.00576}
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

---

## 🔄 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.1.1 | 2026-03-20 | 实盘版（新 API 文档，Algo Order） |
| v1.0.x | 2026-03-19 | 模拟版（仅供测试） |

详细历史版本文档见 `docs/` 文件夹。

---

## 💼 开发团队

**魔力塔互动娱乐**

- 执行总裁：老王
- 技术顾问：杰哥

---

## 📄 许可证

MIT License

---

_最后更新：2026-03-20_
