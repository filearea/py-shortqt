# -*- coding: utf-8 -*-
"""
实盘交易模块
"""

import asyncio
import socket
import requests
from pathlib import Path


class LiveTrader:
    """实盘交易器"""
    
    def __init__(self):
        self.running = False
        self.account = None
    
    async def run(self):
        """运行实盘交易"""
        print("\n实盘模式")
        print("=" * 70)
        
        # 显示外网 IP
        public_ip = self._get_public_ip()
        if public_ip:
            print(f"\n⚠️  本机外网 IP: {public_ip}")
            print("\n请确保已在币安 API 设置中添加此 IP 到白名单！")
            print()
        
        # 选择账号
        self.account = self._select_account()
        
        if self.account:
            print(f"\n已选择账号：{self.account['name']}")
            print("[提示] 实盘交易功能正在开发中...")
            print()
            input("按回车返回菜单...")
        else:
            print("\n未选择账号，返回菜单")
    
    def _get_public_ip(self):
        """获取外网 IP"""
        try:
            # 方法 1: 通过 API 获取
            response = requests.get("https://api.ipify.org?format=json", timeout=5)
            if response.status_code == 200:
                return response.json().get('ip')
        except Exception:
            pass
        
        try:
            # 方法 2: 备用 API
            response = requests.get("https://ip.42.pl/raw", timeout=5)
            if response.status_code == 200:
                return response.text.strip()
        except Exception:
            pass
        
        return None
    
    def _select_account(self):
        """选择交易账号"""
        config_file = Path(__file__).parent.parent.parent / "config" / "accounts.json"
        
        if not config_file.exists():
            print("\n未找到账号配置文件，请先创建 config/accounts.json")
            print("配置文件格式示例：")
            print('''
{
    "accounts": [
        {
            "name": "主账号",
            "api_key": "your_api_key",
            "api_secret": "your_api_secret"
        },
        {
            "name": "备用账号",
            "api_key": "your_api_key_2",
            "api_secret": "your_api_secret_2"
        }
    ]
}
            ''')
            return None
        
        # TODO: 读取并选择账号
        print("\n账号选择功能正在开发中...")
        return None
