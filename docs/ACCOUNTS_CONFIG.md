# 账号配置说明

## 📁 配置文件位置

```
config/accounts.json
```

## 🔐 安全保护

- ✅ `accounts.json` 已加入 `.gitignore`，**不会被提交到 Git**
- ✅ 范例文件 `accounts.json.example` 可安全提交
- ⚠️ **切勿**将真实 API Key 提交到版本控制

## 📝 配置格式

```json
{
    "accounts": [
        {
            "name": "主账号",
            "api_key": "YOUR_BINANCE_API_KEY_HERE",
            "api_secret": "YOUR_BINANCE_API_SECRET_HERE",
            "testnet": true,
            "note": "测试网络，不产生真实交易"
        },
        {
            "name": "实盘账号",
            "api_key": "YOUR_REAL_API_KEY_HERE",
            "api_secret": "YOUR_REAL_API_SECRET_HERE",
            "testnet": false,
            "note": "真实交易账户，请谨慎使用"
        }
    ],
    "settings": {
        "default_account": "主账号",
        "risk_warning": "⚠️ 实盘交易有风险，请谨慎操作",
        "ip_whitelist_reminder": "请确保已在币安 API 设置中添加本机外网 IP 到白名单"
    }
}
```

## 🔑 配置字段说明

### 账号字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 账号名称（用于显示） |
| `api_key` | string | 币安 API Key |
| `api_secret` | string | 币安 API Secret |
| `testnet` | boolean | 是否使用测试网络（true=测试，false=实盘） |
| `note` | string | 备注说明 |

### 设置字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `default_account` | string | 默认账号名称 |
| `risk_warning` | string | 风险提示 |
| `ip_whitelist_reminder` | string | IP 白名单提醒 |

## 🛠️ 创建步骤

1. **复制范例文件**
   ```powershell
   cd D:\Project\py-shortqt
   Copy-Item config/accounts.json.example config/accounts.json
   ```

2. **编辑配置文件**
   ```powershell
   notepad config/accounts.json
   ```

3. **填入真实 API Key**
   - 替换 `YOUR_BINANCE_API_KEY_HERE` 为你的 API Key
   - 替换 `YOUR_BINANCE_API_SECRET_HERE` 为你的 API Secret

4. **保存文件**
   - 保存后关闭编辑器

## ⚠️ 安全注意事项

### 1. API Key 权限设置

在币安创建 API Key 时，建议权限：
- ✅ 启用现货交易（Enable Spot & Margin Trading）
- ❌ 禁用提现（Disable Withdrawals）
- ❌ 禁用转账（Disable Internal Transfer）

### 2. IP 白名单

**重要：** 实盘模式会显示本机外网 IP，请在币安 API 设置中添加此 IP 到白名单！

```
币安官网 → 个人中心 → API 管理 → 编辑 API → 添加 IP 地址
```

### 3. 文件安全

- ✅ `accounts.json` 已加入 `.gitignore`，不会被 Git 跟踪
- ✅ 可以安全地推送代码到 GitHub
- ⚠️ 不要手动将 `accounts.json` 添加到 Git
- ⚠️ 不要将 API Key 截图或复制到公开场合

### 4. 测试网络

建议先使用币安测试网络（testnet）：
- 测试网络 API：https://testnet.binance.vision
- 测试网络不产生真实交易
- 测试网络 Key 与正式网络 Key 不通用

## 🔍 验证配置

运行实盘模式会自动读取配置：

```powershell
cd D:\Project\py-shortqt
$env:PYTHONUTF8=1
python src/main_v1.1.py
# 选择 2. 实盘模式
```

系统会：
1. 显示本机外网 IP
2. 提醒添加 IP 白名单
3. 列出可用账号供选择

## 📄 相关文件

| 文件 | 说明 | 是否提交 |
|------|------|----------|
| `config/accounts.json` | 真实配置 | ❌ 否（已保护） |
| `config/accounts.json.example` | 范例配置 | ✅ 是 |
| `.gitignore` | Git 忽略规则 | ✅ 是 |

---

**最后提醒：实盘交易有风险，请谨慎操作！** ⚠️
