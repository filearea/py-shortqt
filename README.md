# py-shortqt - Quantitative Trading Framework

> 基于币安 ETHUSDC 合约的 Maker 挂单剥头皮模拟交易系统

## 🚀 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行程序

```powershell
# Windows
$env:PYTHONUTF8=1
python src/main.py

# Linux/Mac
export PYTHONUTF8=1
python src/main.py
```

## 📊 功能特性

- ✅ 实时监听币安 WebSocket 行情（aggTrade + depth20@100ms）
- ✅ Maker 挂单模式（0 手续费模拟）
- ✅ 10 档订单簿实时显示
- ✅ 止盈止损自动管理（+1 点 / -3 点）
- ✅ 提前平仓功能（挂单模式，0 手续费）
- ✅ 交易信号特征采集（用于量化分析）
- ✅ 市场快照记录（每秒）

## 🎮 操作说明

| 按键 | 功能 |
|------|------|
| `↑` / `W` | 做多（挂买单 @ 买一价） |
| `↓` / `S` | 做空（挂卖单 @ 卖一价） |
| `←` | 撤销挂单 |
| `→` | 提前平仓（挂单模式） |
| `Q` | 退出程序 |

## 📁 项目结构

```
py-shortqt/
├── config/              # 配置文件
│   ├── __init__.py
│   └── settings.py      # 交易参数配置
├── src/                 # 源代码
│   ├── __init__.py
│   ├── main.py          # 程序入口
│   ├── trader.py        # 交易逻辑核心
│   ├── websocket.py     # 币安 WebSocket 连接
│   ├── ui.py            # TUI 界面
│   └── logger.py        # 日志系统
├── utils/               # 工具函数
├── logs/                # 日志目录（不提交）
├── tests/               # 测试文件
├── docs/                # 文档
├── requirements.txt     # Python 依赖
├── README.md            # 项目说明
└── .gitignore           # Git 忽略文件
```

## 📝 交易规则

| 参数 | 值 |
|------|-----|
| 标的 | ETHUSDC 永续合约 |
| 杠杆 | 75 倍 |
| 初始保证金 | 10 USDT（模拟） |
| 止盈 | +1 点 |
| 止损 | -3 点 |
| 挂单方式 | Maker（买一/卖一价） |
| 手续费 | 0（模拟） |

## 📊 数据采集

每次开仓自动记录以下特征到 `logs/YYYYMMDD_HHMMSS/signals.csv`：

- 开仓前 5 秒/10 秒/30 秒价格变化率
- 订单簿不平衡度（imbalance）
- 价差（spread）
- 前 3 档/10 档买卖盘深度
- 平仓结果（TP/SL/EARLY）
- 盈亏和持仓时长

## ⚠️ 注意事项

- 本系统为**模拟交易**，不构成投资建议
- 实盘接入需自行承担风险
- API Key 等敏感信息请勿提交到 Git
- 日志目录 `logs/` 已加入 `.gitignore`

## 📈 开发计划

- [x] 手动模拟盘 → 数据采集
- [ ] 分析盘感特征 → 策略建模
- [ ] 自动模拟交易 → 验证优化
- [ ] 接入实盘 → 实盘数据
- [ ] 持续优化 → 正循环迭代

## 📄 License

Private - All rights reserved

## 👤 Author

- GitHub: [@filearea](https://github.com/filearea)
- Email: file_area@foxmail.com
