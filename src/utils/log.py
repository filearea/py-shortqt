# -*- coding: utf-8 -*-
"""
日志工具 - 统一日志接口
"""

from src.loggers import LogManager

_log_manager = None

def get_log_manager():
    """获取日志管理器单例"""
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager()
    return _log_manager

def log(msg: str, level: str = 'info'):
    """写入日志（不再 print，避免 TUI 抖动）"""
    lm = get_log_manager()
    if level == 'info':
        lm.system.info(msg)
    elif level == 'debug':
        lm.system.debug(msg)
    elif level == 'warning':
        lm.system.warning(msg)
    elif level == 'error':
        lm.system.error(msg)

def info(msg: str):
    """INFO 级别日志"""
    log(msg, 'info')

def debug(msg: str):
    """DEBUG 级别日志"""
    log(msg, 'debug')

def warning(msg: str):
    """WARNING 级别日志"""
    log(msg, 'warning')

def error(msg: str):
    """ERROR 级别日志"""
    log(msg, 'error')
