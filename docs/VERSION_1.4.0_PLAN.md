# py-shortqt v1.4.0 - 盘面技术指标分析版本

## 版本信息

- **版本号**：v1.4.0
- **计划日期**：2026-03-25
- **核心功能**：实时盘面技术指标分析 + 交易建议
- **目标用户**：交易决策者（King 团队）

---

## 🎯 需求背景

交易部门需要实时了解当前盘面是否适合剥头皮交易，系统需要：
1. 实时计算关键技术指标
2. 在 TUI 上直观展示
3. 提供交易建议（适合/观望/暂停）
4. 记录指标快照用于后续分析

---

## 📊 指标体系

### 1. 波动率指标（5 个）

| 指标 | 计算方式 | 更新频率 | 阈值告警 |
|------|---------|---------|---------|
| 1 分钟振幅 | (最高 - 最低) / 开盘 × 100% | 每秒 | < 0.03% 🟡 / > 0.3% 🔴 |
| 5 分钟振幅 | 近 5 根 K 线综合振幅 | 每分钟 | - |
| 3 小时平均振幅 | 近 180 根 K 线平均振幅 | 每分钟 | < 0.08% 🟡 |
| 振幅变化率 | 当前振幅 / 历史平均 | 每 3 小时 | < 0.8 减速 / > 1.2 加速 |
| ATR(14) | 14 周期平均真实波幅 | 每分钟 | - |

**数据来源**：WebSocket 实时 K 线（1 分钟线）

**参考代码**：`D:\project\Market_Analysis\volatility_monitor.py`

---

### 2. 流动性指标（3 个）

| 指标 | 计算方式 | 更新频率 | 阈值告警 |
|------|---------|---------|---------|
| 买卖价差 | ask[0] - bid[0] | 每秒 | - |
| 价差率 | 价差 / 中间价 × 100% | 每秒 | > 0.02% 🔴 |
| 订单簿深度 | bid 前 3 档 + ask 前 3 档总量 | 每秒 | < 500 ETH 🔴 |

**数据来源**：WebSocket 订单簿快照

---

### 3. 综合评分（1 个）

**盘面质量评分** (0-100 分)

计算逻辑：
- 波动率适中 (0.05%-0.15%)：+30 分
- 价差率低 (< 0.01%)：+30 分
- 订单簿深度高 (> 2000 ETH)：+20 分
- 振幅稳定 (变化率 0.8-1.2)：+20 分

**交易建议**：
- 评分 ≥ 70：✅ 适合交易
- 评分 40-69：⚠️ 观望
- 评分 < 40：❌ 暂停交易

---

## 🖥️ TUI 设计

### 布局结构

```
┌──────────────────────────────────────────────────┐
│  订单簿 (左)   │     账户信息 (右)              │
│  ratio=2       │     ratio=1                    │
│                │                                │
│  买 1-买 5      │     余额                       │
│  卖 1-卖 5      │     持仓                       │
│                │     未实现盈亏                 │
├──────────────────────────────────────────────────┤
│                  日志区域 (12 行)                │
├──────────────────────────────────────────────────┤
│              盘面指标区（新增）                  │
│  ┌────────────┬────────────┬────────────┐       │
│  │  波动率区  │  流动性区  │  综合评分  │       │
│  │  (5 个指标) │  (3 个指标) │  信号灯    │       │
│  └────────────┴────────────┴────────────┘       │
└──────────────────────────────────────────────────┘
```

**设计说明：**
- 指标区放在日志区下方
- 原订单簿右侧区域保留给后续历史仓位展示
- 综合评分用**信号灯**形式直观展示

---

### 盘面指标区内容

**波动率区：**
```
波动率
━━━━━━━━━━━━━━━━━━━━━━
1 分钟：0.107% [正常]
5 分钟：0.194%
3 小时：0.068% 🟡
变化率：0.75 [减速]
ATR(14)：1.2393
```

**流动性区：**
```
流动性
━━━━━━━━━━━━━━━━━━━━━━
价差：0.01 USDC
价差率：0.005%
深度：2,500 ETH
```

**综合评分（信号灯）：**
```
交易建议
━━━━━━━━━━━━━━━━━━━━━━
   🟢
 适合交易
 评分：75/100
```

**信号灯规则：**
| 评分 | 信号灯 | 文字 | 颜色 |
|------|--------|------|------|
| ≥ 70 | 🟢 | 适合交易 | 绿色 |
| 40-69 | 🟡 | 观望 | 黄色 |
| < 40 | 🔴 | 暂停交易 | 红色 |

---

## 📝 日志记录

### 日志文件

`logs/<run_id>/market.log` (JSONL 格式)

### 记录频率

- **核心指标**：每 1 分钟
- **完整快照**：每 3 小时
- **异常告警**：实时触发时

### 日志格式

```json
{
  "timestamp": "2026-03-25T12:29:00Z",
  "type": "market_snapshot",
  "symbol": "ETHUSDC",
  "price": 2162.57,
  "volatility": {
    "1min_amplitude": 0.107,
    "5min_amplitude": 0.194,
    "3h_avg_amplitude": 0.068,
    "change_rate": 0.75,
    "atr_14": 1.2393
  },
  "liquidity": {
    "spread": 0.01,
    "spread_rate": 0.005,
    "orderbook_depth": 2500
  },
  "score": {
    "quality_score": 75,
    "recommendation": "适合交易"
  },
  "alerts": []
}
```

---

## 🏗️ 技术实现

### 文件结构

```
py-shortqt/
├── src/
│   ├── indicators/
│   │   ├── __init__.py          # 新模块
│   │   ├── volatility.py        # 波动率指标（复用现有脚本）
│   │   ├── liquidity.py         # 流动性指标
│   │   └── scorer.py            # 综合评分
│   ├── ui/
│   │   └── live_ui.py           # 修改：新增指标面板
│   ├── websocket.py             # 修改：订阅 K 线数据
│   └── loggers/
│       └── market.py            # 新增：市场日志记录
├── tests/
│   └── test_indicators.py       # 指标单元测试
└── docs/
    └── VERSION_1.4.0.md         # 本文档
```

### 核心类设计

**IndicatorsManager（指标管理器）**
```python
class IndicatorsManager:
    def __init__(self):
        self.volatility = VolatilityAnalyzer()
        self.liquidity = LiquidityAnalyzer()
        self.scorer = QualityScorer()
    
    def update_kline(self, kline: dict):
        """更新 K 线数据"""
        self.volatility.add_kline(kline)
    
    def update_orderbook(self, bids: list, asks: list):
        """更新订单簿"""
        self.liquidity.update_orderbook(bids, asks)
    
    def get_snapshot(self) -> dict:
        """获取完整指标快照"""
        return {
            'volatility': self.volatility.get_metrics(),
            'liquidity': self.liquidity.get_metrics(),
            'score': self.scorer.calculate(self.volatility, self.liquidity)
        }
```

**QualityScorer（质量评分器）**
```python
class QualityScorer:
    def calculate(self, volatility, liquidity) -> dict:
        score = 0
        details = []
        
        # 波动率评分 (30 分)
        amp = volatility.get('1min_amplitude', 0)
        if 0.05 <= amp <= 0.15:
            score += 30
            details.append('波动率适中 +30')
        elif amp < 0.03:
            details.append('波动率过低 +0')
        
        # 流动性评分 (50 分)
        spread_rate = liquidity.get('spread_rate', 0)
        if spread_rate < 0.01:
            score += 30
            details.append('价差率低 +30')
        
        depth = liquidity.get('orderbook_depth', 0)
        if depth > 2000:
            score += 20
            details.append('深度充足 +20')
        
        # 确定建议
        if score >= 70:
            recommendation = '适合交易'
        elif score >= 40:
            recommendation = '观望'
        else:
            recommendation = '暂停交易'
        
        return {
            'quality_score': score,
            'recommendation': recommendation,
            'details': details
        }
```

---

## 📋 开发任务清单

### 第一批（核心功能）

- [ ] 创建 `src/indicators/` 模块
- [ ] 迁移 `volatility_monitor.py` 到 `src/indicators/volatility.py`
- [ ] 实现 `src/indicators/liquidity.py`
- [ ] 实现 `src/indicators/scorer.py`（含信号灯逻辑）
- [ ] 修改 `src/ui/live_ui.py` 添加指标区（日志下方）
- [ ] 修改 `src/websocket.py` 订阅 K 线数据
- [ ] 创建 `src/loggers/market.py`
- [ ] 修改 `src/main_live.py` 初始化指标管理器

### 第二批（优化）

- [ ] 添加市场日志记录（每分钟）
- [ ] 单元测试
- [ ] 文档更新

---

## 🧪 测试计划

### 单元测试

- [ ] 波动率指标计算正确性
- [ ] 流动性指标计算正确性
- [ ] 综合评分算法
- [ ] 阈值告警触发

### 集成测试

- [ ] TUI 指标实时更新
- [ ] 日志记录完整性
- [ ] WebSocket 数据订阅

### 用户测试

- [ ] King 团队试用反馈
- [ ] 指标阈值调整

---

## 📚 参考资料

- 飞书文档：《py-shortqt 剥头皮策略核心指标体系 v1.0》
  https://my.feishu.cn/wiki/TKlpwYjeHi9uJUkrefycY7qhn8b

- 参考代码：`D:\project\Market_Analysis\volatility_monitor.py`

---

## 🔄 版本历史

| 日期 | 进度 | 说明 |
|------|------|------|
| 2026-03-25 13:30 | 需求确认 | 与 King 确认指标体系 |
| 2026-03-25 13:35 | 文档创建 | 完成 VERSION_1.4.0.md |
| 2026-03-25 14:00 | 开发中 | 预计开始时间 |

---

_文档创建时间：2026-03-25 13:35_
_维护：老杨（技术总监）_
