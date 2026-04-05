# -*- coding: utf-8 -*-
"""
py-shortqt 版本号
"""

from pathlib import Path

def get_version() -> str:
    """获取版本号（兼容 UTF-8/UTF-16 编码）"""
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        try:
            return version_file.read_text(encoding='utf-8').strip()
        except UnicodeDecodeError:
            # 如果是 UTF-16 BOM，尝试用 utf-16 读取
            return version_file.read_text(encoding='utf-16').strip()
    return "unknown"

__version__ = get_version()
