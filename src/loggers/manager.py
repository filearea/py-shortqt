# -*- coding: utf-8 -*-
"""
日志管理器 - 统一管理所有日志模块
单例模式，全局唯一入口
"""

import os
import sys
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import logging
from logging.handlers import TimedRotatingFileHandler


class LogManager:
    """日志管理器 - 单例模式"""
    
    _instance: Optional['LogManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, log_dir: Path = None, debug_mode: bool = False):
        # 只初始化一次
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self._initialized = True
        
        # 日志目录
        if log_dir is None:
            project_root = Path(__file__).parent.parent.parent
            log_dir = project_root / "logs"
        
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 调试模式
        self.debug_mode = debug_mode
        
        # 当前日期（用于日志文件命名）
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # 日志级别映射
        self.level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        
        # 初始化各日志模块
        self._init_system_logger()
        self._init_market_logger()
        self._init_trading_logger()
        
        # 写入索引文件
        self._write_index()
        
        print(f"✓ 日志系统初始化完成：{self.log_dir}")
    
    def _init_system_logger(self):
        """初始化系统日志"""
        from .system import SystemLogger
        self.system = SystemLogger(self.log_dir, self.debug_mode)
    
    def _init_market_logger(self):
        """初始化市场日志"""
        from .market import MarketLogger
        self.market = MarketLogger(self.log_dir, self.debug_mode)
    
    def _init_trading_logger(self):
        """初始化交易日志"""
        from .trading import TradingLogger
        self.trading = TradingLogger(self.log_dir, self.debug_mode)
    
    def _write_index(self):
        """写入日志索引文件"""
        index_file = self.log_dir / "index.json"
        
        # 读取现有索引
        index_data = {}
        if index_file.exists():
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
            except:
                pass
        
        # 添加本次运行记录
        run_key = self.run_id
        index_data[run_key] = {
            'start_time': datetime.now().isoformat(),
            'run_id': run_key,
            'debug_mode': self.debug_mode,
            'log_files': {
                'system': f"system_{self.current_date}.log",
                'market': f"market_{self.current_date}.jsonl",
                'trading': f"trading_{self.current_date}.jsonl",
                'signals': f"signals_{self.current_date}.csv"
            }
        }
        
        # 写入索引
        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)
    
    def set_level(self, level: str):
        """动态设置日志级别"""
        if level in self.level_map:
            py_level = self.level_map[level]
            self.system.set_level(py_level)
            print(f"[OK] 日志级别已设置为：{level}")
        else:
            print(f"✗ 无效的日志级别：{level}")
    
    def debug(self, msg: str, module: str = 'system'):
        """DEBUG 日志"""
        if module == 'system':
            self.system.debug(msg)
        elif module == 'market':
            self.market.debug(msg)
        elif module == 'trading':
            self.trading.debug(msg)
    
    def info(self, msg: str, module: str = 'system'):
        """INFO 日志"""
        if module == 'system':
            self.system.info(msg)
        elif module == 'market':
            self.market.info(msg)
        elif module == 'trading':
            self.trading.info(msg)
    
    def warning(self, msg: str, module: str = 'system'):
        """WARNING 日志"""
        if module == 'system':
            self.system.warning(msg)
        elif module == 'market':
            self.market.warning(msg)
        elif module == 'trading':
            self.trading.warning(msg)
    
    def error(self, msg: str, module: str = 'system', exc_info: bool = False):
        """ERROR 日志"""
        if module == 'system':
            self.system.error(msg, exc_info=exc_info)
        elif module == 'market':
            self.market.error(msg)
        elif module == 'trading':
            self.trading.error(msg)
    
    def close(self):
        """关闭所有日志"""
        self.system.close()
        self.market.close()
        self.trading.close()
        
        # 更新索引文件（添加结束时间）
        index_file = self.log_dir / "index.json"
        if index_file.exists():
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    index_data = json.load(f)
                
                if self.run_id in index_data:
                    index_data[self.run_id]['end_time'] = datetime.now().isoformat()
                
                with open(index_file, 'w', encoding='utf-8') as f:
                    json.dump(index_data, f, indent=2, ensure_ascii=False)
            except:
                pass
        
        print(f"✓ 日志已保存至：{self.log_dir}")


# 全局日志管理器实例
_log_manager: Optional[LogManager] = None


def get_logger(log_dir: Path = None, debug_mode: bool = False) -> LogManager:
    """获取全局日志管理器实例"""
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager(log_dir, debug_mode)
    return _log_manager
