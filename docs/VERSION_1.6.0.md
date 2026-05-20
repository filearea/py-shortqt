# py-shortqt v1.6.0 - 剥头皮评分系统

> **发布日期**: 2026-05-20  
> **核心功能**: 剥头皮评分系统 + Tick 级震荡检测 + K 线连续性分析 + 动态参数挂钩  
> **版本定位**: 技术指标全面升级，引入三维评分体系替代单一信号判断

---

## 📊 版本概览

v1.6.0 为交易系统引入全新的**剥头皮评分系统（Scalping Scorer）**，从三个维度综合评估开仓时机：

1. **趋势强度（40%）** — K 线连续性 + Tick 级震荡检测
2. **波动率匹配（30%）** — 当前振幅 vs 止盈目标距离
3. **深度充足度（30%）** — 反向挂单量 vs 开仓所需 ETH

**核心价值**:
- 🎯 三维评分替代单一指标，信号更可靠
- 📊 输出综合分数（0-100）+ 方向预测 + 置信度
- 🔬 Tick 级震荡检测，秒级捕捉反转行情
- 🔗 动态参数挂钩，评分随止盈止损/余额/杠杆自适应

---

## 🆕 核心功能

### 1. 剥头皮评分系统（Scalping Scorer）

#### 1.1 功能描述

综合趋势、波动率、流动性三个维度的数据，计算出 0-100 的综合评分，并给出方向建议（看多/看空/无方向）和置信度。

#### 1.2 评分维度

| 维度 | 权重 | 核心指标 | 说明 |
|------|------|----------|------|
| 趋势强度 | 40% | K 线连续性 + Tick 震荡 | 连续同方向 K 线越多、Tick 反转越少，分数越高 |
| 波动率匹配 | 30% | 振幅 vs 止盈距离 | 振幅需足以覆盖止盈目标，但不能过大 |
| 深度充足度 | 30% | 反向挂单量 vs 开仓需求 | 反向深度需足以支撑开仓 |

#### 1.3 输出结果

```
┌─────────────────────────────────────────────────────────┐
│  综合评分：72/100                                        │
│  预计方向：→ 看多                                        │
│  置信度：68%                                            │
│  分类评分：趋势 80 | 波动 65 | 深度 70                   │
│  信号灯：🟢 可开仓                                       │
└─────────────────────────────────────────────────────────┘
```

**信号灯规则**:
- 🟢 ≥ 70 分：可开仓
- 🟡 50-69 分：观望
- 🔴 < 50 分：不宜开仓

#### 1.4 评分算法详解

**趋势强度（40%）**:

```python
# K 线连续性得分
streak_score = min(abs(streak) / max_lookback, 1.0) * 100

# Tick 震荡惩罚
if tick_reversals > 5:
    tick_penalty = (tick_reversals - 5) * 10
    trend_score = max(0, trend_score - tick_penalty)

# 综合
trend_component = streak_score * (1 - tick_penalty_factor)
```

**波动率匹配（30%）**:

```python
# 振幅需要覆盖止盈目标但不能过大
if amplitude < tp_target_pct:
    vol_score = (amplitude / tp_target_pct) * 100 * 0.5
elif amplitude > tp_target_pct * 3:
    vol_score = 50  # 过大也扣分
else:
    vol_score = 80 + (1 - abs(amplitude - tp_target_pct) / tp_target_pct) * 20
```

**深度充足度（30%）**:

```python
# 反向挂单量需覆盖开仓需求
if side == 'LONG':
    depth = ask_depth  # 做空方向深度
else:
    depth = bid_depth  # 做多方向深度

depth_ratio = depth / required_eth
if depth_ratio >= 2:
    depth_score = 100
elif depth_ratio >= 1:
    depth_score = 60 + (depth_ratio - 1) * 40
else:
    depth_score = depth_ratio * 60
```

#### 1.5 置信度计算

```python
# 三个维度的一致性
if all scores are aligned (same direction):
    confidence = 80 + alignment_bonus
elif two out of three aligned:
    confidence = 50 + partial_bonus
else:
    confidence = 30  # 分歧大，置信度低
```

---

### 2. Tick 级震荡检测（Tick Tracker）

#### 2.1 功能描述

利用 bookTicker 实时价格流（每秒约 5 次），在 30 秒滑动窗口内记录价格变化，检测秒级反转行情。

#### 2.2 核心指标

| 指标 | 说明 | 更新频率 |
|------|------|----------|
| Tick 反转次数 | 30 秒窗口内价格方向改变次数 | 实时 |
| 最大振幅 | 窗口内最高-最低价差的百分比 | 实时 |
| 动量方向 | 最近 5 个 tick 的线性回归斜率 | 实时 |

#### 2.3 震荡检测逻辑

```
示例：30 秒窗口内的价格走势
2010.0 → 2012.0 → 2010.0 → 2012.0 → 2010.0 → 2011.5 → 2010.5

反转检测:
  ↑ 反转1    ↑ 反转2    ↑ 反转3    ↑ 反转4

反转次数 = 4
→ 震荡行情，趋势强度扣分
```

#### 2.4 动量计算

```python
# 最近 5 个 tick 的线性回归
recent_prices = [p1, p2, p3, p4, p5]
slope = linear_regression_slope(recent_prices)
if slope > threshold:
    momentum = 'BULLISH'
elif slope < -threshold:
    momentum = 'BEARISH'
else:
    momentum = 'NEUTRAL'
```

---

### 3. K 线连续性分析（K-line Streak）

#### 3.1 功能描述

统计最近 N 根已收盘 K 线的连续涨跌方向，识别单边趋势强度。

#### 3.2 计算逻辑

```
最近 10 根 K 线收盘方向:
+ + + + - - + + + +

正向连续: 4 根
反向连续: 2 根
最长连续: 4（正向）

→ streak = +4（看多）
```

#### 3.3 参数配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| lookback | 10 | 回看 K 线根数 |
| min_streak | 3 | 最小连续数才认为有趋势 |

#### 3.4 示例场景

```
场景 A：强趋势
+ + + + + + + + + +  → streak = +10，趋势极强

场景 B：震荡
+ - + - + - + - + -  → streak = +1，无明显趋势

场景 C：转势
+ + + + - - - - - -  → streak = -6，趋势已反转
```

---

### 4. 动态参数挂钩

#### 4.1 功能描述

评分参数与交易系统实时状态联动，确保评分基于实际交易条件计算。

#### 4.2 联动参数

| 参数 | 来源 | 用途 |
|------|------|------|
| 止盈点数 | `take_profit.points` | 计算波动率匹配阈值 |
| 止损点数 | `stop_loss.trigger_points` | 计算风险收益比 |
| 杠杆倍数 | `leverage.actual` | 计算开仓所需 ETH |
| 账户余额 | 实时查询 | 计算开仓所需 ETH |

#### 4.3 开仓所需 ETH 计算

```python
required_eth = (balance_usdt * leverage) / current_price

示例:
余额 = 50 USDT
杠杆 = 50x
价格 = 2000 USDT
→ required_eth = (50 * 50) / 2000 = 1.25 ETH
```

#### 4.4 止盈目标距离

```python
tp_pct = (tp_points / current_price) * 100

示例:
tp_points = 1.00
价格 = 2000
→ tp_pct = 0.05%
```

---

## 🔧 技术实现

### 模块结构

```
src/indicators/
├── manager.py           # 指标管理器（统一入口）
├── scorer.py            # 剥头皮评分系统（新增）
├── tick_tracker.py      # Tick 级震荡检测（新增）
├── volatility.py        # 波动率分析（ATR 缓存修复）
└── liquidity.py         # 流动性分析
```

### 核心类设计

#### 1. ScalpingScorer

```python
class ScalpingScorer:
    """剥头皮评分器"""

    def __init__(self):
        self.weights = {
            'trend': 0.4,
            'volatility': 0.3,
            'depth': 0.3,
        }

    def score(self, params: dict) -> dict:
        """
        计算综合评分

        Args:
            params: 包含所有必要输入参数
                - kline_streak: K 线连续涨跌数
                - kline_streak_direction: 方向 (1/-1/0)
                - tick_reversals: Tick 反转次数
                - tick_amplitude_pct: Tick 振幅百分比
                - tick_momentum: Tick 动量方向
                - 1min_amplitude: 1 分钟振幅
                - tp_target_pct: 止盈目标距离
                - bid_depth: 买盘深度
                - ask_depth: 卖盘深度
                - required_eth: 开仓所需 ETH

        Returns:
            {
                'total_score': 72,
                'direction': 'LONG',
                'confidence': 68,
                'category_scores': {
                    'trend': 80,
                    'volatility': 65,
                    'depth': 70,
                },
                'recommendation': '可开仓',
                'signal_emoji': '🟢',
                'signal_color': 'green',
            }
        """
        trend_score = self._score_trend(params)
        vol_score = self._score_volatility(params)
        depth_score = self._score_depth(params)

        total = (trend_score * 0.4 + vol_score * 0.3 + depth_score * 0.3)

        return {
            'total_score': round(total),
            'direction': self._determine_direction(params),
            'confidence': self._calculate_confidence(trend_score, vol_score, depth_score),
            'category_scores': {...},
            ...
        }
```

#### 2. TickTracker

```python
class TickTracker:
    """Tick 级震荡检测器"""

    def __init__(self, window_seconds: float = 30.0):
        self.window_seconds = window_seconds
        self.ticks: deque = deque()  # (timestamp, price)

    def add_tick(self, ts: float, price: float):
        """添加 tick 数据"""
        self.ticks.append((ts, price))
        self._cleanup(ts)

    def get_reversal_count(self) -> int:
        """获取窗口内反转次数"""
        ...

    def get_max_amplitude(self) -> float:
        """获取窗口内最大振幅（百分比）"""
        ...

    def get_tick_momentum(self) -> str:
        """获取动量方向: BULLISH/BEARISH/NEUTRAL"""
        ...

    def _cleanup(self, current_ts: float):
        """清理过期 tick"""
        cutoff = current_ts - self.window_seconds
        while self.ticks and self.ticks[0][0] < cutoff:
            self.ticks.popleft()
```

### 数据流

```
bookTicker 价格流 (每秒 ~5 次)
    ↓
TickTracker.add_tick()
    ↓
30 秒窗口计算
    ↓
ScalpingScorer (趋势维度)

WebSocket depth 事件 (100ms)
    ↓
LiquidityAnalyzer
    ↓
ScalpingScorer (深度维度)

K 线收盘 (每分钟)
    ↓
VolatilityAnalyzer + K-line Streak
    ↓
ScalpingScorer (波动维度)

最终输出 → TUI 显示 + 交易决策
```

---

## 🐛 Bug 修复

### 1. ATR 缓存修复

**问题**: `deque(maxlen=200)` 在填满后长度永远为 200，导致基于 `len(klines)` 的缓存判断失效，ATR 值永不更新。

**修复**: 改用 K 线时间戳判断缓存有效性。

```python
# 修复前
if self._atr_cache is not None and self._atr_kline_count == len(self.klines):
    return self._atr_cache  # 永远命中缓存

# 修复后
current_ts = self.current_kline['timestamp'] if self.current_kline else 0
if self._atr_cache is not None and self._atr_cached_at_ts == current_ts:
    return self._atr_cache  # 仅在 K 线未变化时命中
```

**影响**: ATR(14) 现在每分钟 K 线收盘时正确更新。

### 2. WebSocket 组合流修复

**问题**: 多流订阅使用 `/ws/a/b/c` URL 格式，币安不正确处理，导致 kline 数据丢失。

**修复**: 使用标准组合流格式 `/stream?streams=a/b/c`，并正确处理组合流消息格式 `{"stream":"...", "data":{...}}`。

```python
# 修复前
url = f"wss://fstream.binance.com/ws/{symbol}@bookTicker/{symbol}@depth20@100ms/{symbol}@kline_1m"

# 修复后
url = f"wss://fstream.binance.com/stream?streams={symbol.lower()}@bookTicker/{symbol.lower()}@depth20@100ms/{symbol.lower()}@kline_1m"

# 消息解包
stream_name = data.get('stream', '')
if stream_name and 'data' in data:
    data = data['data']  # 提取真实事件数据
```

**影响**: K 线数据可靠接收，指标系统能正确获取实时 K 线更新。

### 3. Recorder K 线回调

**问题**: 指标系统依赖 WebSocket K 线事件，但币安 WebSocket 不稳定，导致指标不更新。

**修复**: 在 recorder 的 API K 线拉取流程中添加回调机制，每分钟 API 拉取后自动通知指标系统。

```python
# recorder.py
self.on_new_kline = None  # 回调函数

# 拉取 K 线后触发
if self.on_new_kline:
    self.on_new_kline(kline_dict)

# main_live.py
self.recorder.on_new_kline = self.indicators.update_kline
```

**影响**: 指标更新现在基于可靠的 API 拉取，而非不稳定的 WebSocket 事件。

---

## 🖥️ TUI 展示

### 指标面板（新增评分显示）

```
┌─────────────────────────────────────────────────────────┐
│  📊 技术指标                                            │
│  ── 波动率 ──                                           │
│  上根K线：0.125% 🟡                                      │
│  5 分钟：0.234%                                         │
│  1 小时：0.567% 🟢                                      │
│  变化率：1.23 正常                                      │
│  ATR(14): 1.4264                                        │
│                                                         │
│  ── 流动性 ──                                           │
│  价差：0.5000 USDC                                      │
│  价差率：0.0245% 🟢                                     │
│  买盘：125.30 ETH                                       │
│  卖盘：98.50 ETH                                        │
│                                                         │
│  ── 评分 ──                                             │
│  🟢 可开仓  72/100                                      │
│  方向：→ 看多  置信度：68%                              │
│  趋势 80 | 波动 65 | 深度 70                            │
└─────────────────────────────────────────────────────────┘
```

---

## ⚠️ 注意事项

### 1. 评分系统局限性

| 限制 | 说明 |
|------|------|
| 非预测工具 | 评分基于当前市场状态，不预测未来 |
| 延迟性 | 指标计算基于历史数据，存在分钟级延迟 |
| 极端行情 | 黑天鹅事件下评分可能失真 |

### 2. 性能影响

| 项目 | 影响 | 说明 |
|------|------|------|
| Tick 追踪 | 极低 | 仅追加到 deque，30 秒自动清理 |
| 评分计算 | 低 | 纯数学计算，微秒级 |
| K 线连续性 | 低 | 遍历 10 根 K 线，仅收盘时计算 |

### 3. 参数调优建议

| 参数 | 激进 | 保守 |
|------|------|------|
| 开仓分数阈值 | ≥ 60 | ≥ 75 |
| 置信度阈值 | ≥ 50% | ≥ 70% |
| Tick 反转阈值 | > 8 | > 3 |

---

## 📋 测试计划

### 单元测试

| 测试项 | 说明 | 预期结果 |
|--------|------|----------|
| 评分计算 | 各维度分数计算 | 0-100 范围内 |
| Tick 反转检测 | 价格反转计数 | 正确计数 |
| K 线连续性 | 连续涨跌识别 | streak 值正确 |
| 动态参数 | 参数联动 | 评分随参数变化 |
| ATR 缓存 | 时间戳缓存逻辑 | 每分钟更新 |

### 集成测试

| 测试项 | 说明 | 预期结果 |
|--------|------|----------|
| 完整数据流 | WebSocket + API → 评分 | TUI 正确显示 |
| 评分与交易 | 评分 → 开仓决策 | 高分开仓，低分观望 |
| 断网恢复 | WebSocket 断连后恢复 | 评分自动恢复 |

---

## 📈 版本对比

| 功能 | v1.5.8 | v1.6.0 |
|------|--------|--------|
| 剥头皮评分 | ❌ | ✅ 三维评分 |
| Tick 震荡检测 | ❌ | ✅ 30 秒窗口 |
| K 线连续性 | ❌ | ✅ 最多 10 根 |
| 动态参数挂钩 | ❌ | ✅ 止盈/杠杆/余额 |
| ATR 缓存 | Bug | ✅ 时间戳判断 |
| WebSocket K 线 | 不可靠 | ✅ 组合流修复 |
| K 线数据源 | WebSocket | ✅ API 回调 |

---

## 🎯 后续计划

### v1.6.x 补丁版本

- **v1.6.1** - 评分参数可视化调优
- **v1.6.2** - 评分历史回测

### 长期规划

- 机器学习评分优化
- 多币种支持
- 评分预警通知

---

## 📞 发布确认

- [x] 需求文档完成
- [x] 代码开发完成
- [ ] 单元测试通过
- [ ] 集成测试通过
- [x] 文档更新完成（README + VERSION doc）
- [ ] Git 标签创建
- [ ] 推送到远程仓库
- [ ] 通知交易部门（King 团队）

---

**撰写人**: 老杨（技术总监）  
**审核人**: 杰哥（CEO）  
**撰写时间**: 2026-05-20  
**发布时间**: 2026-05-20
