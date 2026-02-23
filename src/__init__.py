"""
立直麻将读牌应用

一个基于天凤凤凰桌牌谱数据的立直麻将读牌辅助工具。
"""

__version__ = "0.1.0"
__author__ = "Your Name"

from . import data_downloader
from . import mjlog_parser
from . import database
try:
    from . import gui_app
except ImportError:
    gui_app = None  # PyQt5 未安装时跳过

__all__ = [
    "data_downloader",
    "mjlog_parser",
    "database",
    "gui_app",
]
