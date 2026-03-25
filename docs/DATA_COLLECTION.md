# 历史数据收集使用说明

## 📁 目录结构

```
py-shortqt/
├── collect_data.py              # 独立 CLI 脚本
├── data/                         # 数据存储目录
│   ├── klines/
│   │   ├── ETHUSDC/
│   │   │   ├── 2026-03-11.jsonl
│   │   │   └── ...
│   │   └── BTCUSDC/
│   ├── orderbook/
│   │   ├── ETHUSDC/
│   │   └── ...
│   └── analysis/
│       └── klines_stats_ETHUSDC_20260325.json
```

## 🚀 快速开始

### 1. 手动收集历史数据

```bash
cd D:\Project\py-shortqt

# 收集过去 14 天 K 线
python collect_data.py --mode collect --days 14

# 收集特定交易对
python collect_data.py --mode collect --days 7 --symbols ETHUSDC BTCUSDC
```

### 2. 持续记录订单簿

```bash
# 后台运行，每 5 分钟记录一次
python collect_data.py --mode record --interval 300
```

### 3. 生成统计报告

```bash
# 分析过去 7 天数据
python collect_data.py --mode analyze --days 7
```

## ⏰ 定时任务配置

### Windows 任务计划程序

**K 线同步（每小时）：**
1. 打开"任务计划程序"
2. 创建基本任务
3. 名称：`py-shortqt - Klines Sync`
4. 触发器：每天，重复间隔 1 小时
5. 操作：启动程序
   - 程序：`python.exe`
   - 参数：`collect_data.py --mode collect --days 1`
   - 起始目录：`D:\Project\py-shortqt`

**订单簿记录（每 5 分钟）：**
1. 创建基本任务
2. 名称：`py-shortqt - Orderbook`
3. 触发器：每天，重复间隔 5 分钟
4. 操作：启动程序
   - 程序：`python.exe`
   - 参数：`collect_data.py --mode record --interval 300`

**统计分析（每周日凌晨）：**
1. 创建基本任务
2. 名称：`py-shortqt - Weekly Stats`
3. 触发器：每周日，凌晨 2:00
4. 操作：启动程序
   - 程序：`python.exe`
   - 参数：`collect_data.py --mode analyze --days 7 --symbols ETHUSDC`

### 批处理脚本

创建 `sync_hourly.bat`：
```batch
@echo off
chcp 65001 >nul
cd /d D:\Project\py-shortqt
python collect_data.py --mode collect --days 1
```

创建 `analyze_weekly.bat`：
```batch
@echo off
chcp 65001 >nul
cd /d D:\Project\py-shortqt
python collect_data.py --mode analyze --days 7 --symbols ETHUSDC BTCUSDC
```

## 📊 数据格式

### K 线数据（JSONL）

每行一个 JSON 对象：
```json
{
  "timestamp": 1711353600000,
  "open": 2150.50,
  "high": 2151.20,
  "low": 2150.00,
  "close": 2150.80,
  "volume": 1250.5,
  "turnover": 2689500.0,
  "trades": 350,
  "buy_volume": 680.2,
  "buy_turnover": 1462500.0
}
```

### 订单簿快照（JSONL）

```json
{
  "timestamp": 1711353600000,
  "bids": [
    ["2150.50", "50.5"],
    ["2150.49", "30.2"]
  ],
  "asks": [
    ["2150.51", "45.3"],
    ["2150.52", "28.1"]
  ]
}
```

### 统计报告（JSON）

```json
{
  "total_klines": 10080,
  "1min_amplitude": {
    "p10": 0.021,
    "p25": 0.035,
    "p50": 0.058,
    "p75": 0.092,
    "p90": 0.145
  },
  "recommendation": {
    "low_threshold": 0.035,
    "normal_min": 0.045,
    "normal_max": 0.078,
    "high_threshold": 0.092
  }
}
```

## ⚠️ API 限流说明

**币安 Futures API 限制：**
- 权重限制：1200 权重/分钟
- K 线接口：500 根权重 2
- 深度接口：权重 5

**脚本保护措施：**
- 自动计算请求间隔
- K 线获取：每次 500 根，间隔 0.1 秒
- 订单簿：每 5 分钟一次，避免超限

**建议：**
- 不要同时运行多个脚本实例
- 如需获取多个交易对，依次运行
- 监控 API 响应，如有限流提示暂停运行

## 🔄 自动化流程

### py-shortqt 启动时

1. 程序启动
2. 连接 WebSocket
3. **自动补全缺失的历史数据**（过去 14 天）
4. 进入主交易界面

### 定时任务（King 独立运行）

**每小时：**
```bash
python collect_data.py --mode collect --days 1
```
- 检查并补全缺失的 K 线
- 增量获取（已存在的日期跳过）

**每 5 分钟：**
```bash
python collect_data.py --mode record --interval 300
```
- 记录订单簿快照
- 持续运行（后台进程）

**每周日：**
```bash
python collect_data.py --mode analyze --days 7
```
- 分析过去 7 天数据
- 生成阈值建议报告

## 📈 数据分析应用

### 波动率阈值校准

运行统计分析后，参考报告中的分位数调整打分系统：

```python
# 当前阈值（经验值）
'1min_amplitude': {
    'low': 0.03,
    'normal_min': 0.05,
    'normal_max': 0.15,
    'high': 0.3
}

# 建议阈值（基于实盘数据）
'1min_amplitude': {
    'low': p25,      # 25% 分位数
    'normal_min': p40,
    'normal_max': p60,
    'high': p75      # 75% 分位数
}
```

### 流动性阈值校准

分析订单簿数据：
- 计算 Bid1-5 累计深度分布
- 计算 Ask1-5 累计深度分布
- 找出"充足"深度的分位数（如 p75）

## 🔧 故障排除

### 问题：API 返回 429 错误

**原因：** 请求频率过高

**解决：**
1. 暂停脚本运行
2. 等待 5-10 分钟
3. 增加请求间隔（修改 `RATE_LIMIT` 参数）

### 问题：数据文件为空

**原因：** 网络问题或 API 错误

**解决：**
1. 检查网络连接
2. 手动测试 API
3. 删除空文件，重新运行

## 📝 更新日志

- **2026-03-25** - 初始版本
  - K 线增量获取
  - 订单簿快照记录
  - 统计分析功能
  - API 限流保护
  - 集成到 py-shortqt 启动流程
