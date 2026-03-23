# -*- coding: utf-8 -*-
"""
py-shortqt 版本号
"""

from pathlib import Path

def get_version() -> str:
    """获取版本号"""
    version_file = Path(__file__).parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "unknown"

__version__ = get_version()
