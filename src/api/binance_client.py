# -*- coding: utf-8 -*-
"""
币安 Futures API 客户端
封装 REST API 调用
"""

import requests
from decimal import Decimal
from pathlib import Path
import json
import asyncio

from .signature import build_signed_params, get_timestamp, sync_time
from .rate_limiter import RateLimiter, BINANCE_WEIGHTS


class BinanceClient:
    """币安 Futures API 客户端"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        
        # API 端点
        if testnet:
            self.base_url = "https://demo-fapi.binance.com"  # 新测试网
        else:
            self.base_url = "https://fapi.binance.com"
        
        self.session = requests.Session()
        self.session.headers.update({
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        })
        
        # v1.4.0 新增：API 请求速率限制器
        self.rate_limiter = RateLimiter(weight_limit=1200, window_seconds=60)
    
    def _get(self, path: str, params: dict = None, signed: bool = False, weight: int = 1) -> dict:
        """GET 请求（带速率限制）"""
        # 同步等待获取请求权限
        import time
        while True:
            if self.rate_limiter.get_available_weight() >= weight:
                self.rate_limiter.requests.append((time.time(), weight))
                self.rate_limiter.current_weight += weight
                break
            else:
                wait_time = self.rate_limiter.get_wait_time(weight)
                if wait_time > 0:
                    time.sleep(wait_time)
                else:
                    time.sleep(0.1)
        
        url = f"{self.base_url}{path}"
        
        if signed:
            params = build_signed_params(params or {}, self.api_secret)
            # 签名后，把参数字符串直接拼接到 URL（避免 requests 再次编码）
            query_string = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
            url = f"{url}?{query_string}"
            response = self.session.get(url, timeout=10)
        else:
            response = self.session.get(url, params=params, timeout=10)
        
        return self._handle_response(response)
    
    def get_klines(self, symbol: str, interval: str, limit: int = 100, startTime: int = None, endTime: int = None) -> list:
        """
        获取 K 线数据（公开接口，无需签名）

        Args:
            symbol: 交易对（如 ETHUSDC）
            interval: 时间间隔（1m, 5m, 1h 等）
            limit: 返回数量（最多 1500）
            startTime: 起始时间戳（毫秒）
            endTime: 结束时间戳（毫秒）

        Returns:
            K 线数据列表 [[timestamp, open, high, low, close, volume, ...], ...]
        """
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        if startTime is not None:
            params['startTime'] = startTime
        if endTime is not None:
            params['endTime'] = endTime
        weight = BINANCE_WEIGHTS.get('GET /fapi/v1/klines', 1)
        return self._get('/fapi/v1/klines', params, signed=False, weight=weight)
    
    def _post(self, path: str, params: dict = None, signed: bool = False) -> dict:
        """POST 请求"""
        url = f"{self.base_url}{path}"
        
        if signed:
            params = build_signed_params(params or {}, self.api_secret)
            # 签名后，把参数字符串直接作为 data 发送（避免 requests 再次编码）
            data = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
            response = self.session.post(url, data=data, timeout=10)
        else:
            response = self.session.post(url, data=params, timeout=10)
        
        return self._handle_response(response)
    
    def _delete(self, path: str, params: dict = None, signed: bool = False) -> dict:
        """DELETE 请求"""
        url = f"{self.base_url}{path}"
        
        if signed:
            params = build_signed_params(params or {}, self.api_secret)
            # 签名后，把参数字符串直接拼接到 URL
            query_string = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
            url = f"{url}?{query_string}"
            response = self.session.delete(url, timeout=10)
        else:
            response = self.session.delete(url, params=params, timeout=10)
        
        return self._handle_response(response)
    
    def _handle_response(self, response: requests.Response) -> dict:
        """处理响应"""
        if response.status_code == 200:
            return response.json()
        else:
            error = response.json() if response.text else {'code': response.status_code}
            raise BinanceAPIError(error.get('code', -1), error.get('msg', 'Unknown error'))
    
    # ==================== 公共接口（无需签名）====================
    
    def get_server_time(self) -> int:
        """获取服务器时间"""
        data = self._get('/fapi/v1/time')
        return data['serverTime']

    def sync_time(self) -> int:
        """同步本地时间与服务器时间，返回偏移量（毫秒）"""
        server_time = self.get_server_time()
        return sync_time(server_time)
    
    def get_exchange_info(self, symbol: str = None) -> dict:
        """获取交易规则"""
        params = {'symbol': symbol} if symbol else {}
        return self._get('/fapi/v1/exchangeInfo', params)

    def get_ticker_price(self, symbol: str) -> float:
        """获取最新成交价"""
        data = self._get('/fapi/v1/ticker/price', {'symbol': symbol})
        return float(data['price'])
    
    # ==================== 账户接口（需要签名）====================
    
    def get_balance(self) -> list:
        """获取账户余额"""
        return self._get('/fapi/v2/balance', signed=True)
    
    def get_position(self, symbol: str = None) -> list:
        """获取持仓信息"""
        params = {'symbol': symbol} if symbol else {}
        return self._get('/fapi/v2/positionRisk', params, signed=True)
    
    def get_open_orders(self, symbol: str = None) -> list:
        """获取当前挂单"""
        params = {'symbol': symbol} if symbol else {}
        return self._get('/fapi/v1/openOrders', params, signed=True)
    
    def get_account(self) -> dict:
        """获取账户信息"""
        return self._get('/fapi/v2/account', signed=True)
    
    # ==================== 交易接口（需要签名）====================
    
    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """设置杠杆倍数"""
        return self._post('/fapi/v1/leverage', {
            'symbol': symbol,
            'leverage': leverage
        }, signed=True)
    
    def place_algo_order(self, symbol: str, side: str, type: str, 
                    price: str = None, quantity: str = None,
                    timeInForce: str = 'GTC', 
                    triggerPrice: str = None,
                    workingType: str = None,
                    priceMatch: str = None,
                    positionSide: str = None,
                    newOrderRespType: str = 'RESULT') -> dict:
        """
        Algo Order API 下单（用于条件单：STOP, STOP_MARKET, TAKE_PROFIT 等）
        接口：/fapi/v1/algoOrder
        
        Args:
            price: 限价（与 priceMatch 互斥）
            priceMatch: 'QUEUE'/'QUEUE_5'/'OPPONENT'/'OPPONENT_5'（与 price 互斥）
        """
        params = {
            'symbol': symbol,
            'side': side,
            'type': type,
            'quantity': quantity,
            'newOrderRespType': newOrderRespType,
            'algoType': 'CONDITIONAL'  # Algo Order API 必需参数，固定为 CONDITIONAL
        }
        
        if price:
            params['price'] = price
        
        if timeInForce:
            params['timeInForce'] = timeInForce
        
        if triggerPrice:
            params['triggerPrice'] = triggerPrice
        
        if workingType:
            params['workingType'] = workingType
        
        if priceMatch:
            params['priceMatch'] = priceMatch
        
        if positionSide:
            params['positionSide'] = positionSide

        return self._post('/fapi/v1/algoOrder', params, signed=True)
    
    def cancel_all_open_orders(self, symbol: str) -> dict:
        """
        撤销全部订单（普通订单 + 条件单）
        接口：/fapi/v1/algoOpenOrders
        """
        params = {
            'symbol': symbol
        }
        return self._delete('/fapi/v1/algoOpenOrders', params, signed=True)
    
    def cancel_algo_order(self, symbol: str, algo_id: int) -> dict:
        """
        撤销条件单（Algo Order API）
        接口：/fapi/v1/algoOrder
        """
        params = {
            'symbol': symbol,
            'algoId': algo_id
        }
        return self._delete('/fapi/v1/algoOrder', params, signed=True)
    
    def cancel_all_orders(self, symbol: str) -> dict:
        """撤销所有挂单"""
        return self._delete('/fapi/v1/allOpenOrders', {
            'symbol': symbol
        }, signed=True)
    
    def place_order(self, symbol: str, side: str, type: str, 
                    price: str = None, quantity: str = None,
                    timeInForce: str = 'GTC', 
                    stopPrice: str = None,
                    workingType: str = None,
                    priceMatch: str = None,
                    positionSide: str = None,
                    newOrderRespType: str = 'RESULT') -> dict:
        """
        下单（双向持仓模式）
        
        Args:
            symbol: 交易对，如 'ETHUSDC'
            side: 'BUY' 或 'SELL'
            type: 订单类型
            price: 限价单价格（与 priceMatch 互斥）
            quantity: 下单数量
            timeInForce: 'GTC'
            stopPrice: 触发价（条件单需要）
            priceMatch: 'QUEUE'/'QUEUE_5'/'OPPONENT'/'OPPONENT_5'（与 price 互斥）
            positionSide: 'LONG' 或 'SHORT'
        
        Returns:
            订单信息
        
        注意：双向持仓模式不接受 reduceOnly 参数
        """
        params = {
            'symbol': symbol,
            'side': side,
            'type': type,
            'quantity': quantity,
            'newOrderRespType': newOrderRespType
        }
        
        if price:
            params['price'] = price
        
        # 市价单不需要 timeInForce
        if timeInForce and type != 'MARKET':
            params['timeInForce'] = timeInForce
        
        if stopPrice:
            params['stopPrice'] = stopPrice
        
        if workingType:
            params['workingType'] = workingType
        
        if priceMatch:
            params['priceMatch'] = priceMatch
        
        if positionSide:
            params['positionSide'] = positionSide

        return self._post('/fapi/v1/order', params, signed=True)
    
    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """撤单"""
        return self._delete('/fapi/v1/order', {
            'symbol': symbol,
            'orderId': order_id
        }, signed=True)
    
    def cancel_all_orders(self, symbol: str) -> dict:
        """撤销所有挂单"""
        return self._delete('/fapi/v1/allOpenOrders', {
            'symbol': symbol
        }, signed=True)

    def get_order(self, symbol: str, order_id: int) -> dict:
        """查询订单状态"""
        return self._get('/fapi/v1/order', {
            'symbol': symbol,
            'orderId': order_id
        }, signed=True)

    def get_fills(self, symbol: str, limit: int = 10, startTime: int = None, endTime: int = None, fromId: int = None) -> list:
        """查询成交记录（支持分页）"""
        params = {
            'symbol': symbol,
            'limit': min(limit, 1000)
        }
        if startTime is not None:
            params['startTime'] = startTime
        if endTime is not None:
            params['endTime'] = endTime
        if fromId is not None:
            params['fromId'] = fromId
        return self._get('/fapi/v1/userTrades', params, signed=True, weight=5)

    def get_income_history(self, symbol: str, incomeType: str = None, startTime: int = None, endTime: int = None, limit: int = 1000) -> list:
        """查询收入历史（资金费率、手续费、已实现盈亏等）"""
        params = {
            'symbol': symbol,
            'limit': min(limit, 1000)
        }
        if incomeType is not None:
            params['incomeType'] = incomeType
        if startTime is not None:
            params['startTime'] = startTime
        if endTime is not None:
            params['endTime'] = endTime
        return self._get('/fapi/v1/income', params, signed=True, weight=30)

    # ==================== 用户数据流====================
    
    def get_listen_key(self) -> str:
        """获取用户数据流 listenKey"""
        data = self._post('/fapi/v1/listenKey')
        return data['listenKey']
    
    def keep_alive_listen_key(self, listen_key: str) -> dict:
        """保活 listenKey"""
        return self._put('/fapi/v1/listenKey', {'listenKey': listen_key})
    
    def _put(self, path: str, params: dict = None) -> dict:
        """PUT 请求（用于 listenKey 保活）"""
        url = f"{self.base_url}{path}"
        response = self.session.put(url, params=params, timeout=10)
        return self._handle_response(response)


class BinanceAPIError(Exception):
    """币安 API 错误"""
    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(f"[{code}] {msg}")
