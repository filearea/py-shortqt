# py-shortqt v1.8.0 — 统计周期可配置 + TUI 金额脱敏

> **发布日期：** 2026-06-10（v1.8.0）/ 2026-06-17（v1.8.1）
> **基于版本：** v1.7.14

---

## 版本目标

提升 TUI 可用性和隐私保护：统计周期从硬编码"近 24 小时"改为可配置模式，TUI 金额显示支持脱敏（以启动余额为基数的百分比）。

---

## 一、统计周期可配置

### 1.1 两种模式

| 模式 | 键 | 说明 |
|------|------|------|
| 近 24 小时滚动 | `stats_period.mode = "24h"` | 过去 24 小时滚动窗口 |
| 自然日 | `stats_period.mode = "calendar_day"` | 指定时区的当日 0 点 ~ 24 点 |

### 1.2 时区配置

自然日模式下可配置时区，受影响的功能：
- 24h 统计面板：开仓/平仓次数、胜率、盈亏比的统计时间边界
- 历史持仓面板：自然日下的持仓记录过滤

配置键：`stats_period.timezone`，支持 IANA 时区标识符（如 `Asia/Shanghai`）或 UTC 偏移（如 `+8`）。

---

## 二、TUI 金额脱敏

### 2.1 脱敏机制

- 启动时快照当前可用余额作为基数（`_privacy_baseline`）
- TUI 中所有金额转换为 `金额 / 基数 × 100%` 百分比显示
- 日志文件保留原始绝对值，不受脱敏影响
- 脱敏开关：`privacy.enabled`（默认关闭）

### 2.2 脱敏示例

| 原始值 | 基数 1000U 时显示 |
|--------|-----------------|
| 500.00 USDC | 50.000% |
| 15.00 USDC | 1.500% |
| -3.20 USDC | -0.320% |

### 2.3 重置脱敏基数（v1.8.1）

- `R` 键重置脱敏基数
- 将基数重新设为当前可用余额
- 仅在脱敏开启时生效
- TUI 头栏不展示 R 键提示（减少视觉噪音）

---

## 三、配置变更

```json
// runtime.json 新增
{
    "price_range": {
        "minutes": 30
    },
    "stats_period": {
        "mode": "24h",
        "timezone": "+8"
    },
    "privacy": {
        "enabled": true
    }
}
```

旧配置文件启动时自动通过 `_upgrade_config()` 补全新字段。

---

## 四、TUI 设置面板扩展

系统设置标签页新增字段类型支持：

| 新增类型 | 说明 |
|---------|------|
| `select` | 下拉选项（统计周期模式切换） |
| `string` | 文本输入（时区标识符） |

修复 `price_range.minutes` 在系统设置 tab 不渲染的 bug（原代码通过 `visible_idx` 索引映射遗漏了系统 tab 的字段索引偏移）。

---

## 五、改动文件

| 文件 | 改动 |
|------|------|
| `src/ui/live_ui.py` | 统计面板/历史面板适配周期模式；金额渲染走 format_money |
| `src/ui/settings_ui.py` | 系统 tab 新增统计周期、时区、脱敏字段；扩展 select/string 类型支持 |
| `src/trading/live.py` | format_money 脱敏逻辑；privacy_baseline 管理；R 键重置（v1.8.1） |
| `src/config/manager.py` | DEFAULT_CONFIG 新字段 + upgrade 逻辑 |
| `src/main_live.py` | R 键处理（v1.8.1） |
| `config/runtime.json.auto` | 新增 price_range / stats_period / privacy 配置节 |

---

## 六、v1.8.1 补充说明

v1.8.1 仅新增 R 键重置脱敏基数功能，改动极小（2 文件 20 行），合并到本文档不单独拆分。

---

**撰写人：** 老杨
**撰写时间：** 2026-06-24（补写）
**目标版本：** v1.8.0 / v1.8.1
