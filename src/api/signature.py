# -*- coding: utf-8 -*-
"""
币安 API 签名模块
HMAC SHA256 签名算法
"""

import hmac
import hashlib
import time
from urllib.parse import urlencode

# 本地时间与币安服务器时间的偏移量（毫秒），正数表示本地慢于服务器
_time_offset = 0


def sync_time(server_time_ms: int) -> int:
    """根据服务器时间计算时间偏移量，后续 get_timestamp() 会自动修正"""
    global _time_offset
    local_now = int(time.time() * 1000)
    _time_offset = server_time_ms - local_now
    return _time_offset


def generate_signature(secret: str, params: dict) -> str:
    """
    生成 HMAC SHA256 签名
    
    Args:
        secret: API Secret
        params: 请求参数字典
    
    Returns:
        签名字符串
    """
    # 添加时间戳
    params['timestamp'] = int(time.time() * 1000)
    
    # 参数按字母顺序排序并编码
    query_string = urlencode(sorted(params.items()))
    
    # 生成签名
    signature = hmac.new(
        secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return signature


def get_timestamp() -> int:
    """获取当前时间戳（毫秒），自动加上服务器时间偏移量"""
    return int(time.time() * 1000) + _time_offset


def build_signed_params(params: dict, secret: str, recv_window: int = 5000) -> dict:
    """
    构建带签名的请求参数
    
    Args:
        params: 原始参数
        secret: API Secret
        recv_window: 接收窗口（毫秒），默认 5000
    
    Returns:
        带签名的完整参数字典
    """
    params = params.copy()
    params['timestamp'] = get_timestamp()
    params['recvWindow'] = recv_window
    
    # 币安要求：参数按字母顺序排序，用 & 连接（不使用 urlencode 编码）
    query_string = '&'.join(f'{k}={v}' for k, v in sorted(params.items()))
    params['signature'] = hmac.new(
        secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return params
