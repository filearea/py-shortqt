# py-shortqt v1.2.0 - TUI 设置模块开发文档

> 版本：1.2.0
> 开发日期：2026-03-21
> 状态：待确认（等待杰哥说"可以开工"后开始实现）

---

## 📌 版本目标

### 核心功能

- ✅ TUI 设置面板（按 S 键进入/退出）
- ✅ 止盈双模式（固定点数 / 百分比）
- ✅ 止损双模式（触发价：固定点数/百分比 + 实际止损价：同向价 1/自定义滑点）
- ✅ 保底止损（最大损失比例，基于开仓前保证金）
- ✅ 双杠杆设置（API 杠杆 / 实际杠杆）
- ✅ 配置备份/恢复/重置功能
- ✅ 启动时选择账户（命令行/脚本）
- ✅ 配置验证 + 实时计算预览

### 技术指标

| 指标 | 要求 |
|------|------|
| 精度 | 所有数值保留 2 位小数 |
| 合约 | 固定 ETHUSDC（本版本不切换） |
| 账户 | 启动时选择，TUI 内不切换 |
| 配置持久化 | runtime.json 自动保存 |

---

## 🏗️ 技术架构

### 模块划分

```
py-shortqt/
├── 启动.bat                  # 启动脚本（选择账户）
├── config/
│   ├── accounts.json         # 账户配置（启动时选择）
│   ├── runtime.json          # 运行时配置（可修改）
│   ├── runtime.json.backup   # 配置备份
│   └── settings.py           # 系统配置（只读）
├── src/
│   ├── main_live.py          # 实盘入口
│   ├── config/
│   │   ├── manager.py        # ← 新增：配置管理器
│   │   └── validator.py      # ← 新增：配置验证
│   ├── trading/
│   │   └── live_trader.py    # ← 修改：集成新止盈止损逻辑
│   └── ui/
│       ├── live_ui.py        # 主交易界面
│       └── settings_ui.py    # ← 新增：设置界面
├── docs/
│   └── VERSION_1.2.0.md      # ← 本文档
└── logs/                     # 运行日志
```

---

## 📋 功能设计详情

### 0. 安全检测（进入设置前）

**检测条件：**
- 有挂单（pending_order）
- 有持仓（position）

**触发时机：** 用户按 `S` 键尝试进入设置面板时

**处理逻辑：**
```python
def try_enter_settings(self):
    """尝试进入设置面板"""
    if self.trader.pending_order:
        self.logger.log_action("⚠️ 禁止进入", "请先撤销挂单（按 ←）")
        return False
    
    if self.trader.position:
        self.logger.log_action("⚠️ 禁止进入", "请先平仓（按 →）")
        return False
    
    # 可以进入
    self.show_settings = True
    return True
```

**日志区提示：**
```
[12:20:15.123]  ⚠️ 禁止进入  请先撤销挂单（按 ←）
[12:20:18.456]  ⚠️ 禁止进入  请先平仓（按 →）
```

**设计原因：**
- 防止用户在有挂单/持仓时误修改止盈止损参数
- 避免参数不一致导致的风险

---

### 1. 止盈设置（双模式）

**模式选择：**
- **固定点数：** 开仓价 + 固定点数
- **百分比：** 开仓价 × (1 + 百分比%)

**配置结构：**
```json
{
    "take_profit": {
        "mode": "fixed",
        "points": 1.00,
        "percent": 0.36
    }
}
```

**计算公式：**
```python
def calculate_take_profit(entry_price: Decimal, config: dict) -> Decimal:
    if config['take_profit']['mode'] == 'fixed':
        return entry_price + Decimal(str(config['take_profit']['points']))
    else:
        return entry_price * (Decimal('1') + Decimal(str(config['take_profit']['percent'])) / Decimal('100'))
```

**示例：**
```
开仓价：2150.00
- 固定点数模式 (+1.00 点) → 止盈价：2151.00
- 百分比模式 (+0.36%) → 止盈价：2157.74
```

---

### 2. 止损设置（双模式 + 双模式）

#### 2.1 触发价模式

**模式选择：**
- **固定点数：** 开仓价 ± 固定点数
- **百分比：** 开仓价 × (1 ± 百分比%)

#### 2.2 实际止损价模式

**模式选择：**
- **同向价 1（原有功能）：** Algo API 传 `priceMatch='QUEUE'`，不传 `price`
- **自定义滑点（新增）：** Algo API 传 `price`，不传 `priceMatch`

**配置结构：**
```json
{
    "stop_loss": {
        "trigger_mode": "fixed",
        "trigger_points": 3.00,
        "trigger_percent": 0.50,
        "limit_mode": "queue",
        "limit_offset": 10.50
    }
}
```

**计算公式：**
```python
def calculate_stop_loss(entry_price: Decimal, side: str, config: dict) -> tuple[Decimal, str, str]:
    """
    返回：(触发价，Algo API 参数)
    """
    # 1. 计算触发价
    if config['stop_loss']['trigger_mode'] == 'fixed':
        points = Decimal(str(config['stop_loss']['trigger_points']))
        if side == 'LONG':
            trigger_price = entry_price - points
        else:
            trigger_price = entry_price + points
    else:
        percent = Decimal(str(config['stop_loss']['trigger_percent'])) / Decimal('100')
        if side == 'LONG':
            trigger_price = entry_price * (Decimal('1') - percent)
        else:
            trigger_price = entry_price * (Decimal('1') + percent)
    
    # 2. 构建 Algo API 参数
    algo_params = {
        'triggerPrice': str(trigger_price),
        'quantity': str(size),
        'workingType': 'CONTRACT_PRICE',
        'positionSide': side,
        'timeInForce': 'GTC'
    }
    
    if config['stop_loss']['limit_mode'] == "queue":
        # 同向价 1 模式（原有功能，别改崩）
        algo_params['priceMatch'] = 'QUEUE'
        # 不传 price
    else:
        # 自定义滑点模式（新增）
        # 多单止损：卖出平仓，挂高价（触发价 + 滑点）
        # 空单止损：买入平仓，挂低价（触发价 - 滑点）
        offset = Decimal(str(config['stop_loss']['limit_offset']))
        if side == 'LONG':
            limit_price = trigger_price + offset  # 多单：触发价 + 滑点
        else:
            limit_price = trigger_price - offset  # 空单：触发价 - 滑点
        algo_params['price'] = str(limit_price)
        # 不传 priceMatch
    
    return trigger_price, algo_params
```

**示例：**
```
开仓价：2150.00（多单）

触发价计算：
- 固定点数模式 (-3.00 点) → 触发价：2147.00
- 百分比模式 (-0.50%) → 触发价：2139.25

实际止损价计算（基于触发价 2147.00）：
- 同向价 1 模式 → Algo: priceMatch='QUEUE' → 触发后挂 2146.00
- 自定义滑点模式 (滑点 10.50 点) → Algo: price='2157.50' → 触发后挂 2157.50
  （多单止损：触发价 2147 + 滑点 10.50 = 2157.50）
```

---

### 3. 保底止损（最大损失比例）

**核心逻辑：**
```
最大损失（USDT）= 开仓前保证金 × 最大损失比例
手续费 = 名义仓位价值 × 0.05%
实际可承受价格损失 = 最大损失 - 手续费
损失价差 = 实际可承受损失 / 数量
止损价 = 开仓价 ± 损失价差
最终止损价 = max/min(计算值，强平价±1)
```

**配置结构：**
```json
{
    "stop_market": {
        "max_loss_percent": 30.00
    }
}
```

**计算公式：**
```python
def calculate_stop_market(
    entry_price: Decimal,
    side: str,
    size: Decimal,
    balance_before: Decimal,
    max_loss_percent: Decimal,
    liquidation_price: Decimal
) -> Decimal:
    # 1. 名义仓位价值
    notional = entry_price * size
    
    # 2. 手续费（Taker 0.05%）
    fee = notional * Decimal('0.0005')
    
    # 3. 最大损失（USDT）
    max_loss_usd = balance_before * (max_loss_percent / Decimal('100'))
    
    # 4. 实际可承受价格损失
    price_loss_usd = max_loss_usd - fee
    
    # 5. 损失价差
    price_diff = price_loss_usd / size
    
    # 6. 止损价
    if side == 'LONG':
        stop_price = entry_price - price_diff
        # 和强平价 +1 比较，取更高的（更安全）
        liquidation_stop = liquidation_price + Decimal('1')
        stop_price = max(stop_price, liquidation_stop)
    else:
        stop_price = entry_price + price_diff
        # 和强平价 -1 比较，取更低的（更安全）
        liquidation_stop = liquidation_price - Decimal('1')
        stop_price = min(stop_price, liquidation_stop)
    
    return stop_price
```

**示例：**
```
开仓价：2150.00（多单）
持仓：0.407 ETH
开仓前保证金：35.00 U
最大损失比例：30%
强平价：2060.00

计算：
1. 名义仓位价值 = 2150 × 0.407 = 875.05 U
2. 手续费 = 875.05 × 0.0005 = 0.44 U
3. 最大损失 = 35 × 30% = 10.50 U
4. 实际价格损失 = 10.50 - 0.44 = 10.06 U
5. 损失价差 = 10.06 / 0.407 = 24.72 U
6. 计算止损价 = 2150 - 24.72 = 2125.28
7. 强平价 +1 = 2060 + 1 = 2061
8. 最终止损价 = max(2125.28, 2061) = 2125.28 ✓
```

---

### 4. 杠杆设置（双杠杆）

**需求：**
- **API 杠杆：** 币安 API 设置的杠杆，影响最大可开名义价值
- **实际杠杆：** 实际仓位计算用的杠杆，控制风险

**配置结构：**
```json
{
    "leverage": {
        "api": 100,
        "actual": 25
    }
}
```

**使用方式：**
```python
# 1. 初始化时设置 API 杠杆
api.set_leverage(symbol='ETHUSDC', leverage=100)

# 2. 计算仓位时用实际杠杆
margin = 35.00  # 保证金
actual_leverage = 25
notional = margin * actual_leverage  # 875.00 U
size = notional / entry_price  # 0.407 ETH
```

**示例：**
```
保证金：35.00 U
API 杠杆：100 → 最大可开名义价值：3500.00 U
实际杠杆：25 → 本单名义价值：875.00 U
本单数量：0.407 ETH @ 2150.00
```

---

### 5. 配置备份/恢复/重置

#### 5.1 备份配置

**触发方式：**
- 手动备份：设置面板按 `B` 键
- 自动备份：每次保存配置前自动备份到 `runtime.json.auto`

**实现：**
```python
class ConfigManager:
    def backup_config(self, backup_name: str = None) -> str:
        """备份当前配置"""
        if backup_name is None:
            backup_name = f"runtime.json.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        backup_path = Path(f"config/{backup_name}")
        shutil.copy("config/runtime.json", backup_path)
        
        return backup_path
```

#### 5.2 恢复配置

**触发方式：**
- 设置面板按 `R` 键，选择备份文件恢复

**实现：**
```python
class ConfigManager:
    def restore_config(self, backup_name: str) -> bool:
        """从备份恢复配置"""
        backup_path = Path(f"config/{backup_name}")
        if not backup_path.exists():
            return False
        
        shutil.copy(backup_path, "config/runtime.json")
        self.reload()
        return True
    
    def list_backups(self) -> list[str]:
        """列出所有备份文件"""
        backups = []
        for f in Path("config").glob("runtime.json.*"):
            if f.name not in ['runtime.json.auto']:
                backups.append(f.name)
        return sorted(backups, reverse=True)
```

#### 5.3 重置为默认值

**触发方式：**
- 设置面板按 `D` 键（需要二次确认）

**默认配置：**
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
    "leverage": {
        "api": 100,
        "actual": 25
    },
    "order_timeout_seconds": 2.00
}
```

**实现：**
```python
class ConfigManager:
    DEFAULT_CONFIG = {
        "take_profit": {"mode": "fixed", "points": 1.00, "percent": 0.36},
        "stop_loss": {"trigger_mode": "fixed", "trigger_points": 3.00, "trigger_percent": 0.50, "limit_mode": "queue", "limit_offset": 10.50},
        "stop_market": {"max_loss_percent": 30.00},
        "leverage": {"api": 100, "actual": 25},
        "order_timeout_seconds": 2.00
    }
    
    def reset_to_defaults(self):
        """重置为默认配置"""
        with open("config/runtime.json", "w") as f:
            json.dump(self.DEFAULT_CONFIG, f, indent=4)
        self.reload()
```

---

### 6. 启动时选择账户

**启动脚本（启动.bat）：**
```batch
@echo off
chcp 65001 >nul
echo.
echo ╔════════════════════════════════════════╗
echo ║     py-shortqt v1.2.0 启动器          ║
echo ╚════════════════════════════════════════╝
echo.
echo 选择账户:
echo.

:: 读取 accounts.json 显示账户列表
python -c "import json; accounts=json.load(open('config/accounts.json'))['accounts']; [print(f'{i+1}. {a[\"name\"]} ({\"测试网\" if a[\"testnet\"] else \"实盘\"})') for i,a in enumerate(accounts)]"

echo.
set /p choice="请输入选项 (1-N): "

:: 根据选择启动
python -c "import json; accounts=json.load(open('config/accounts.json'))['accounts']; print(accounts[%choice%-1]['name'])" > temp_account.txt
set /p account=<temp_account.txt
del temp_account.txt

echo.
echo 启动账户：%account%
echo.
python src/main_live.py --account "%account%"
pause
```

**命令行参数：**
```python
# src/main_live.py
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--account', type=str, default=None, help='账户名称')
args = parser.parse_args()

# 加载账户配置
accounts = load_accounts()
if args.account:
    account = find_account(accounts, args.account)
else:
    account = accounts[0]  # 默认第一个

# 初始化 API
api = BinanceClient(account['api_key'], account['api_secret'], account['testnet'])
```

---

## 📊 TUI 界面设计

### 主交易界面（新增 S 键提示）

**无挂单/无持仓状态（可以进入设置）：**
```
┌──────────────────────────────────────────────────┐
│ ETHUSDC  |  价格：2148.17 ↑  |  就绪             │
├──────────────────────────────────────────────────┤
│                                                  │
│  订单簿                    账户                  │
│  2149.50  0.125           可用：35.000 U        │
│  2149.00  0.250           占用：0.000 U         │
│  2148.50  0.375                                  │
│  2148.17  (最新价)         无持仓                │
│  2148.00  0.500                                  │
│  2147.50  0.625                                  │
│  2147.00  0.750                                  │
│                                                  │
├──────────────────────────────────────────────────┤
│ ↑做多 ↓做空 ←撤单 →平仓 S 设置 Q 退出            │
└──────────────────────────────────────────────────┘
```

**有挂单状态（禁止进入设置）：**
```
┌──────────────────────────────────────────────────┐
│ ETHUSDC  |  价格：2148.17 ↑  |  开仓挂单中       │
├──────────────────────────────────────────────────┤
│                                                  │
│  订单簿                    账户                  │
│  2149.50  0.125           可用：34.500 U        │
│  2149.00  0.250           占用：0.500 U         │
│  2148.50  0.375                                  │
│  2148.17  (最新价)         开仓挂单：做多        │
│  2148.00  0.500           价格：2148.00         │
│  2147.50  0.625           数量：0.407 ETH       │
│  2147.00  0.750                                  │
│                                                  │
├──────────────────────────────────────────────────┤
│ ↑做多 ↓做空 ←撤单 →平仓 S 设置 Q 退出            │
└──────────────────────────────────────────────────┘

按 S 键后日志区提示：
[12:20:15.123]  ⚠️ 禁止进入  请先撤销挂单（按 ←）
```

**有持仓状态（禁止进入设置）：**
```
┌──────────────────────────────────────────────────┐
│ ETHUSDC  |  价格：2148.17 ↑  |  持仓中 (→平仓)   │
├──────────────────────────────────────────────────┤
│                                                  │
│  订单簿                    账户                  │
│  2149.50  0.125           可用：15.234 U        │
│  2149.00  0.250           占用：19.766 U        │
│  2148.50  0.375                                  │
│  2148.17  (最新价)         持仓：做多            │
│  2148.00  0.500           开仓价：2147.00       │
│  2147.50  0.625           数量：0.500 ETH       │
│  2147.00  0.750           止盈：2148.00         │
│                            止损：2144.00         │
│                                                  │
├──────────────────────────────────────────────────┤
│ ↑做多 ↓做空 ←撤单 →平仓 S 设置 Q 退出            │
└──────────────────────────────────────────────────┘

按 S 键后日志区提示：
[12:20:18.456]  ⚠️ 禁止进入  请先平仓（按 →）
```

---

### 设置界面 - 交易参数标签

```
┌──────────────────────────────────────────────────┐
│ ⚙️ 设置面板  |  ←→调整  ↑↓切换项  Tab 切换标签  S 返回  │
├──────────────────────────────────────────────────┤
│                                                  │
│  ┌─ 止盈设置 ──────────────────────────────┐   │
│  │  模式：[● 固定点数]  [ ] 百分比          │   │
│  │  → 固定点数：[+1.00] 点                  │   │
│  │     百分比：  [0.36]%                    │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
│  ┌─ 止损设置 ──────────────────────────────┐   │
│  │  触发价：[● 固定点数]  [ ] 百分比        │   │
│  │  → 固定点数：[-3.00] 点                  │   │
│  │     百分比：  [-0.50]%                   │   │
│  ├─────────────────────────────────────────┤   │
│  │  实际止损价：[● 同向价 1]  [ ] 自定义   │   │
│  │  → 自定义滑点：[10.50] 点                │   │
│  │     (多单：触发价 + 滑点，空单：触发价 - 滑点)  │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
│  ┌─ 保底止损 ──────────────────────────────┐   │
│  │  最大损失比例：[30.00]%                  │   │
│  │  示例：100U 本金→最大亏损 30U             │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
│  ┌─ 杠杆设置 ──────────────────────────────┐   │
│  │  API 杠杆：[100] (最大可开 3500U)         │   │
│  │  实际杠杆：[25 ] (本单用 875U)            │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
│  ┌─ 其他参数 ──────────────────────────────┐   │
│  │  订单超时：[2.00s]                       │   │
│  └─────────────────────────────────────────┘   │
│                                                  │
├──────────────────────────────────────────────────┤
│ [保存]  [取消]  [B 备份]  [R 恢复]  [D 重置默认] │
└──────────────────────────────────────────────────┘
```

---

### 设置界面 - 实时计算预览

```
┌─ 实时计算预览（基于当前配置）─────────────────┐
│  假设开仓价：2150.00（多单）                  │
│  持仓：0.407 ETH (名义价值：875.00 U)         │
│  开仓前保证金：35.00 U                        │
├───────────────────────────────────────────────┤
│  止盈价：2157.74 (+0.36%)  ✓                 │
│  止损触发：2139.25 (-0.50%)                  │
│  止损限价：2157.50 (滑点 +10.50，多单)       │
│  保底止损：2125.28 (最大损失 10.50U)         │
│  强平价：2060.00                              │
├───────────────────────────────────────────────┤
│  盈亏比：2.29:1 ✓  (止盈 7.74 / 止损 3.37)   │
│  建议：盈亏比至少 1.5:1                       │
└───────────────────────────────────────────────┘
```

---

### 备份管理对话框

```
┌─────────────────────────────────────────┐
│           📦 配置备份管理               │
├─────────────────────────────────────────┤
│  现有备份：                             │
│                                         │
│  → runtime.json.20260321_120000         │
│    runtime.json.20260320_180000         │
│    runtime.json.20260319_090000         │
│                                         │
│  [B 新建备份]  [R 恢复选中]  [X 删除]   │
│                                         │
│         [Esc 返回]                      │
└─────────────────────────────────────────┘
```

---

### 确认对话框

```
┌─────────────────────────────────────────┐
│              ⚠️ 确认操作                │
├─────────────────────────────────────────┤
│                                         │
│  确定要重置所有配置为默认值吗？         │
│                                         │
│  此操作不可恢复，建议先备份当前配置。   │
│                                         │
│           [Y 确认]  [N 取消]            │
│                                         │
└─────────────────────────────────────────┘
```

---

## ⚠️ 安全设计

### 1. 配置验证

```python
class ConfigValidator:
    """配置验证器"""
    
    @staticmethod
    def validate(config: dict) -> tuple[bool, list[str]]:
        """验证配置合理性"""
        errors = []
        
        # 1. 止盈验证
        tp = config['take_profit']
        if tp['mode'] == 'fixed':
            if tp['points'] < 0.01:
                errors.append("止盈点数不能小于 0.01")
            if tp['points'] > 100:
                errors.append("止盈点数不能大于 100")
        else:
            if tp['percent'] < 0.01:
                errors.append("止盈百分比不能小于 0.01%")
            if tp['percent'] > 10:
                errors.append("止盈百分比不能大于 10%")
        
        # 2. 止损验证
        sl = config['stop_loss']
        if sl['trigger_mode'] == 'fixed':
            if abs(sl['trigger_points']) < 0.01:
                errors.append("止损触发点数不能小于 0.01")
        else:
            if sl['trigger_percent'] < 0.01:
                errors.append("止损触发百分比不能小于 0.01%")
        
        # 3. 盈亏比验证（需要开仓价）
        # 在开仓时动态验证
        
        # 4. 最大损失验证
        max_loss = config['stop_market']['max_loss_percent']
        if max_loss < 10:
            errors.append("最大损失比例不能小于 10%")
        if max_loss > 80:
            errors.append("最大损失比例不能大于 80%（建议 20-40%）")
        
        # 5. 杠杆验证
        leverage = config['leverage']
        if leverage['api'] < 1 or leverage['api'] > 125:
            errors.append("API 杠杆必须在 1-125 之间")
        if leverage['actual'] < 1 or leverage['actual'] > 125:
            errors.append("实际杠杆必须在 1-125 之间")
        if leverage['actual'] > leverage['api']:
            errors.append("实际杠杆不能大于 API 杠杆")
        
        return len(errors) == 0, errors
```

### 2. 危险操作二次确认

```python
DANGEROUS_ACTIONS = {
    'reset_defaults': "确定要重置所有配置为默认值吗？此操作不可恢复。",
    'delete_backup': "确定要删除备份 '{name}' 吗？",
    'restore_backup': "确定要从备份 '{name}' 恢复吗？当前配置将被覆盖。",
}
```

### 3. 自动备份

```python
class ConfigManager:
    def save_config(self, config: dict):
        """保存配置前自动备份"""
        # 自动备份到 runtime.json.auto
        auto_backup = Path("config/runtime.json.auto")
        if Path("config/runtime.json").exists():
            shutil.copy("config/runtime.json", auto_backup)
        
        # 保存新配置
        with open("config/runtime.json", "w") as f:
            json.dump(config, f, indent=4)
```

---

## 🔄 工作流程

### 开仓时计算止盈/止损/保底

```python
class LiveTrader:
    def open_position(self, side: str):
        """开仓并设置止盈止损"""
        # 1. 获取配置
        config = self.config_manager.get_config()
        
        # 2. 计算仓位
        api_leverage = config['leverage']['api']
        actual_leverage = config['leverage']['actual']
        balance = self.get_balance()
        
        # 设置 API 杠杆
        self.api.set_leverage(self.symbol, api_leverage)
        
        # 计算名义价值和数量
        notional = balance * actual_leverage
        size = notional / self.entry_price
        
        # 3. 计算止盈价
        tp_price = calculate_take_profit(self.entry_price, config)
        
        # 4. 计算止损触发价和 Algo 参数
        sl_trigger, sl_algo_params = calculate_stop_loss(
            self.entry_price, side, size, config
        )
        
        # 5. 计算保底止损价
        liquidation = self.get_liquidation_price(side)
        sm_price = calculate_stop_market(
            self.entry_price, side, size, balance,
            Decimal(str(config['stop_market']['max_loss_percent'])),
            liquidation
        )
        
        # 6. 下单
        self._place_tp_order(tp_price, size)
        self._place_sl_order(sl_algo_params)
        self._place_sm_order(sm_price, size)
        
        # 7. 记录日志
        self.logger.log_open(
            side, self.entry_price, size,
            tp_price, sl_trigger, sm_price
        )
```

---

## 📝 实现计划

### Phase 1: 配置管理器（1 天）

- [ ] 创建 `src/config/manager.py`
- [ ] 创建 `src/config/validator.py`
- [ ] 实现配置加载/保存
- [ ] 实现配置验证
- [ ] 实现备份/恢复/重置功能
- [ ] 创建 `config/runtime.json` 模板

### Phase 2: 止盈止损计算（1 天）

- [ ] 实现止盈双模式计算
- [ ] 实现止损双模式计算（同向价 1/自定义滑点）
- [ ] 实现保底止损计算（最大损失比例）
- [ ] 修改 `src/trading/live_trader.py`
- [ ] 单元测试计算逻辑

### Phase 3: TUI 设置界面（2 天）

- [ ] 创建 `src/ui/settings_ui.py`
- [ ] 实现设置面板渲染
- [ ] 实现按键交互（←→调整，↑↓切换）
- [ ] 实现实时计算预览
- [ ] 实现备份管理对话框
- [ ] 集成到主界面（S 键入口）

### Phase 4: 启动脚本（0.5 天）

- [ ] 修改 `启动.bat` 支持账户选择
- [ ] 修改 `src/main_live.py` 支持 `--account` 参数
- [ ] 测试启动流程

### Phase 5: 测试与文档（0.5 天）

- [ ] 集成测试
- [ ] 边界条件测试
- [ ] 更新 README.md
- [ ] 更新 VERSION_1.2.0.md（本文档）

**总工期：** 约 5 天

---

## 🔧 配置文件模板

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
    "leverage": {
        "api": 100,
        "actual": 25
    },
    "order_timeout_seconds": 2.00,
    "last_modified": "2026-03-21T12:00:00+08:00"
}
```

### config/accounts.json

```json
{
    "accounts": [
        {
            "name": "主账号",
            "api_key": "YOUR_API_KEY",
            "api_secret": "YOUR_API_SECRET",
            "testnet": true,
            "note": "测试网络"
        },
        {
            "name": "实盘账号",
            "api_key": "YOUR_REAL_API_KEY",
            "api_secret": "YOUR_REAL_API_SECRET",
            "testnet": false,
            "note": "实盘交易"
        }
    ],
    "default_account": "主账号"
}
```

---

## 📋 验收标准

### 功能验收

- [ ] 有挂单时按 S 键，禁止进入设置，日志提示"请先撤销挂单"
- [ ] 有持仓时按 S 键，禁止进入设置，日志提示"请先平仓"
- [ ] 无挂单/无持仓时按 S 键，正常进入设置面板
- [ ] 按 S 键能进入/退出设置面板
- [ ] 止盈模式切换后，计算正确
- [ ] 止损触发价两种模式计算正确
- [ ] 止损实际止损价两种模式计算正确
- [ ] 保底止损计算正确（和手动计算一致）
- [ ] 双杠杆设置生效（API 杠杆影响最大仓位，实际杠杆影响本单仓位）
- [ ] 配置保存后重启不丢失
- [ ] 备份功能正常
- [ ] 恢复功能正常
- [ ] 重置默认功能正常
- [ ] 启动时能选择账户

### 精度验收

- [ ] 所有百分比显示 2 位小数（如 0.36%）
- [ ] 所有点数显示 2 位小数（如 1.00 点）
- [ ] 所有价格显示 2 位小数（如 2150.00）

### 安全验收

- [ ] 配置验证生效（非法值无法保存）
- [ ] 危险操作有二次确认
- [ ] 保存前自动备份

---

## 🔗 参考链接

- **币安 Futures API：** https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures
- **Algo Order API：** `/fapi/v1/algoOrder`
- **订单类型说明：** https://developers.binance.com/docs/zh-CN/derivatives/usds-margined-futures/trade/rest-api

---

_文档版本：1.0_
_设计日期：2026-03-21_
_作者：老王_
_状态：待确认（等杰哥说"可以开工"）_
