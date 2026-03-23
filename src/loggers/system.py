# -*- coding: utf-8 -*-
"""
系统日志 - 记录程序运行状态、错误、调试信息
"""

import logging
import sys
import traceback
from pathlib import Path
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler


class SystemLogger:
    """系统日志记录器"""
    
    def __init__(self, log_dir: Path, debug_mode: bool = False):
        self.log_dir = log_dir
        self.debug_mode = debug_mode
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        
        # 创建 logger
        self.logger = logging.getLogger("py-shortqt.system")
        self.logger.setLevel(logging.DEBUG)
        
        # 清除已有 handler
        self.logger.handlers = []
        
        # 文件 handler（按日期轮转）
        log_file = self.log_dir / f"system_{self.current_date}.log"
        file_handler = TimedRotatingFileHandler(
            log_file,
            when='D',
            interval=1,
            backupCount=7,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        
        # 控制台 handler（只显示 WARNING 及以上）
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.WARNING)
        
        # 格式
        file_format = logging.Formatter(
            '%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(filename)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_format = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        file_handler.setFormatter(file_format)
        console_handler.setFormatter(console_format)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    def set_level(self, level: int):
        """设置日志级别"""
        self.logger.setLevel(level)
    
    def debug(self, msg: str):
        """DEBUG 级别日志"""
        if self.debug_mode:
            self.logger.debug(msg)
    
    def info(self, msg: str):
        """INFO 级别日志"""
        self.logger.info(msg)
    
    def warning(self, msg: str):
        """WARNING 级别日志"""
        self.logger.warning(msg)
    
    def error(self, msg: str, exc_info: bool = False):
        """ERROR 级别日志"""
        self.logger.error(msg, exc_info=exc_info)
    
    def critical(self, msg: str, exc_info: bool = True):
        """CRITICAL 级别日志"""
        self.logger.critical(msg, exc_info=exc_info)
    
    def exception(self, msg: str):
        """记录异常堆栈"""
        self.logger.exception(msg)
    
    def close(self):
        """关闭 handler"""
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
