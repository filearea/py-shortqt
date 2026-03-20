# -*- coding: utf-8 -*-
"""API 连通性测试"""

from src.api.binance_client import BinanceClient
import json

cfg = json.load(open('config/accounts.json', encoding='utf-8'))
client = BinanceClient(cfg['accounts'][0]['api_key'], cfg['accounts'][0]['api_secret'], False)

# 测试设置杠杆
print('设置杠杆...')
result = client.set_leverage('ETHUSDC', 100)
print('杠杆设置结果:', result)

# 测试获取持仓
print('\n获取持仓...')
positions = client.get_position('ETHUSDC')
for p in positions:
    print(f"持仓：{p['positionAmt']} | 强平价：{p['liquidationPrice']}")

# 测试获取挂单
print('\n获取挂单...')
orders = client.get_open_orders('ETHUSDC')
print(f'当前挂单数：{len(orders)}')

# 测试获取余额
print('\n获取余额...')
account = client.get_account()
for asset in account['assets']:
    if asset['asset'] == 'USDC':
        print(f"USDC 可用余额：{asset['availableBalance']}")
        print(f"USDC 总余额：{asset['walletBalance']}")
        break

print('\n✓ API 测试通过')
