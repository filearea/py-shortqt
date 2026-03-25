import requests

limits = [5, 10, 20, 50, 100, 500, 1000, 5000]

print("Testing Binance orderbook depth limits...")
print("=" * 50)

for limit in limits:
    url = "https://fapi.binance.com/fapi/v1/depth"
    params = {'symbol': 'ETHUSDC', 'limit': limit}
    
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        data = response.json()
        bids = len(data.get('bids', []))
        asks = len(data.get('asks', []))
        print(f"limit={limit:5d} -> OK: bids={bids}, asks={asks}")
    else:
        print(f"limit={limit:5d} -> Error: {response.status_code}")

print("=" * 50)
print("\nBinance Futures API orderbook depth limits:")
print("Valid values: 5, 10, 20, 50, 100, 500, 1000, 5000")
print("Default: 100")
