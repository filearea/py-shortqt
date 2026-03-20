# -*- coding: utf-8 -*-
"""
系统日志配置 - 记录程序运行日志（报错、调试信息等）
日志位置：项目根目录/logs/system.log
"""

import logging
from pathlib import Path
from datetime import datetime


def setup_system_logger(log_dir: Path = None):
    """设置系统日志"""
    if log_dir is None:
        project_root = Path(__file__).parent.parent
        log_dir = project_root / "logs"
    
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 系统日志文件
    log_file = log_dir / "system.log"
    
    # 创建 logger
    logger = logging.getLogger("system")
    logger.setLevel(logging.DEBUG)
    
    # 清除已有 handler
    logger.handlers = []
    
    # 文件 handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
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
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


# 全局系统日志实例
system_logger = None


def get_system_logger():
    """获取系统日志实例"""
    global system_logger
    if system_logger is None:
        system_logger = setup_system_logger()
    return system_logger
