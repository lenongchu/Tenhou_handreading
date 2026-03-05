"""
GUI主程序

使用 PyQt5 开发桌面界面
"""

import sys
import re
import html
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QTextBrowser, QGroupBox,
    QSpinBox, QRadioButton, QButtonGroup, QProgressBar,
    QMessageBox, QFormLayout, QListWidget, QListWidgetItem,
    QDialog, QComboBox, QDialogButtonBox, QSizePolicy,
    QScrollArea, QFrame, QGridLayout, QCheckBox, QSplitter,
    QTabWidget, QTabBar, QInputDialog,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QRect, QRectF, QSettings, QTimer
from PyQt5.QtGui import QPainter, QColor, QBrush, QPen, QFont, QIntValidator

from .data_downloader import DataDownloader
from .live_analyzer import LiveAnalyzer, get_database_stats, format_samples_for_display
from .equivalent_variants import split_discard_pattern, parse_multi_targets
from .tile_illustration import render_illustration_to_qimage
logger = logging.getLogger(__name__)


def _fmt_int(n) -> str:
    """整数格式化，不用千分位符避免中文环境 Qt 渲染乱码（如 8 显示成 日）"""
    return str(int(n))


def _get_app_stylesheet() -> str:
    """科技感深色主题样式表"""
    return """
    /* === 全局 === */
    QWidget { background-color: #0d1117; color: #c9d1d9; }
    QMainWindow { background-color: #0d1117; }
    
    /* === GroupBox 卡片区 === */
    QGroupBox {
        font-weight: bold;
        font-size: 11pt;
        color: #58a6ff;
        border: 1px solid #30363d;
        border-radius: 8px;
        margin-top: 12px;
        padding: 12px 12px 8px 12px;
        background-color: #161b22;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        top: 2px;
        padding: 0 6px;
        color: #00d4ff;
        background-color: #161b22;
    }
    
    /* === 按钮 === */
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #21262d, stop:1 #161b22);
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 14px;
        font-weight: 500;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #30363d, stop:1 #21262d);
        border-color: #58a6ff;
        color: #fff;
    }
    QPushButton:pressed {
        background-color: #0d1117;
    }
    QPushButton:disabled {
        background-color: #21262d;
        color: #484f58;
        border-color: #21262d;
    }
    
    /* 主操作按钮 === */
    QPushButton#query_btn {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #238636, stop:1 #2ea043);
        color: #fff;
        border: 1px solid #2ea043;
        font-size: 13pt;
        padding: 10px 20px;
    }
    QPushButton#query_btn:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #2ea043, stop:1 #3fb950);
    }
    QPushButton#query_btn:disabled {
        background: #21262d;
        color: #484f58;
        border-color: #21262d;
    }
    
    /* 小按钮 ? === */
    QPushButton[text="?"] {
        padding: 2px 8px;
        font-size: 10pt;
    }
    
    /* === 输入框 === */
    QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 10px;
        selection-background-color: #388bfd;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
        border-color: #58a6ff;
    }
    
    /* === 下拉框 === */
    QComboBox {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 12px;
        min-height: 20px;
    }
    QComboBox:hover { border-color: #58a6ff; }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView {
        background-color: #161b22;
        color: #c9d1d9;
        selection-background-color: #388bfd;
    }
    
    /* === 数字框 === */
    QSpinBox {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 4px 8px;
    }
    QSpinBox:focus { border-color: #58a6ff; }
    
    /* === 复选框 / 单选框 === */
    QCheckBox, QRadioButton {
        color: #c9d1d9;
        spacing: 8px;
    }
    QCheckBox::indicator, QRadioButton::indicator {
        width: 16px;
        height: 16px;
        border: 2px solid #30363d;
        border-radius: 3px;
        background-color: #0d1117;
    }
    QRadioButton::indicator { border-radius: 8px; }
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {
        background-color: #58a6ff;
        border-color: #58a6ff;
    }
    QCheckBox:hover::indicator, QRadioButton:hover::indicator {
        border-color: #58a6ff;
    }
    
    /* === 进度条 === */
    QProgressBar {
        border: 1px solid #30363d;
        border-radius: 4px;
        text-align: center;
        background-color: #0d1117;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 #00d4ff, stop:1 #58a6ff);
        border-radius: 3px;
    }
    
    /* === 标签 === */
    QLabel { color: #c9d1d9; }
    QLabel[colorHint="muted"] { color: #8b949e; }
    
    /* === 列表 === */
    QListWidget {
        background-color: #0d1117;
        color: #c9d1d9;
        border: 1px solid #30363d;
        border-radius: 6px;
    }
    QListWidget::item:selected { background-color: #388bfd; }
    
    /* === 滚动区域 === */
    QScrollArea { border: none; background: transparent; }
    QScrollBar:vertical {
        background: #0d1117;
        width: 10px;
        border-radius: 5px;
        margin: 0;
    }
    QScrollBar::handle:vertical {
        background: #30363d;
        border-radius: 5px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover { background: #484f58; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    
    /* === 分割器 === */
    QSplitter::handle { background-color: #30363d; width: 2px; }
    
    /* === Tab / 对话框 === */
    QDialog { background-color: #0d1117; }
    QTabWidget::pane {
        border: 1px solid #30363d;
        border-radius: 6px;
        background-color: #161b22;
        margin-top: 0;
        padding: 12px;
        top: 2px;
    }
    QTabBar {
        background: transparent;
        border: none;
    }
    QTabBar::tab {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #21262d, stop:1 #161b22);
        color: #8b949e;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 6px 16px;
        margin-right: 6px;
        font-weight: 500;
        min-width: 64px;
    }
    QTabBar::tab:selected {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #1a5fb4, stop:1 #388bfd);
        color: #fff;
        border-color: #58a6ff;
    }
    QTabBar::tab:hover:!selected {
        color: #c9d1d9;
        border-color: #58a6ff;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #30363d, stop:1 #21262d);
    }
    QTabBar::tab:first {
        margin-left: 0;
    }
    """


class RangeSlider(QWidget):
    """双柄范围滑块，用于选择巡目范围 1-18"""
    valueChanged = pyqtSignal(int, int)  # (min, max)

    def __init__(self, min_val=1, max_val=18, default_min=1, default_max=6, parent=None):
        super().__init__(parent)
        self._min_val = min_val
        self._max_val = max_val
        self._value_min = default_min
        self._value_max = default_max
        self._dragging = None  # 'min' | 'max' | None
        self._handle_radius = 6
        self._track_height = 4
        self.setMinimumHeight(28)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def getRange(self):
        return (self._value_min, self._value_max)

    def setRange(self, min_val: int, max_val: int):
        self._value_min = max(self._min_val, min(self._max_val, min_val))
        self._value_max = max(self._min_val, min(self._max_val, max_val))
        if self._value_min > self._value_max:
            self._value_min, self._value_max = self._value_max, self._value_min
        self.update()
        self.valueChanged.emit(self._value_min, self._value_max)

    def _valueToPos(self, value: int) -> float:
        """将数值转换为轨道上的 x 坐标"""
        r = self._max_val - self._min_val
        if r <= 0:
            return self._handle_radius
        track_left = self._handle_radius
        track_width = self.width() - 2 * self._handle_radius
        if track_width <= 0:
            return track_left
        frac = (value - self._min_val) / r
        return track_left + frac * track_width

    def _posToValue(self, x: float) -> int:
        """将轨道上的 x 坐标转换为数值"""
        track_left = self._handle_radius
        track_width = self.width() - 2 * self._handle_radius
        if track_width <= 0:
            return self._min_val
        frac = max(0, min(1, (x - track_left) / track_width))
        return round(self._min_val + frac * (self._max_val - self._min_val))

    def _hitHandle(self, x: float, y: float) -> Optional[str]:
        """检测点击的是哪个手柄，返回 'min' / 'max' / None"""
        cy = self.height() / 2
        pos_min = self._valueToPos(self._value_min)
        pos_max = self._valueToPos(self._value_max)
        r = self._handle_radius + 6
        # 重叠时优先选中右侧手柄，便于向右拖动扩展
        if (x - pos_max) ** 2 + (y - cy) ** 2 <= r ** 2:
            return 'max'
        if (x - pos_min) ** 2 + (y - cy) ** 2 <= r ** 2:
            return 'min'
        return None

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        h = self.height()
        cy = h / 2
        track_y = cy - self._track_height / 2

        # 轨道背景（深色主题）
        track_rect = QRectF(self._handle_radius, track_y,
                            self.width() - 2 * self._handle_radius, self._track_height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(48, 54, 61))  # #30363d
        painter.drawRoundedRect(track_rect, 2, 2)

        # 选中范围高亮（青色科技感）
        pos_min = self._valueToPos(self._value_min)
        pos_max = self._valueToPos(self._value_max)
        hl_rect = QRectF(pos_min - self._handle_radius, track_y,
                         pos_max - pos_min, self._track_height)
        painter.setBrush(QColor(0, 212, 255))  # #00d4ff
        painter.drawRoundedRect(hl_rect, 2, 2)

        # 两个圆形手柄
        painter.setBrush(QColor(88, 166, 255))  # #58a6ff
        painter.setPen(QPen(QColor(0, 212, 255), 1))
        painter.drawEllipse(QRectF(pos_min - self._handle_radius, cy - self._handle_radius,
                                  2 * self._handle_radius, 2 * self._handle_radius))
        painter.drawEllipse(QRectF(pos_max - self._handle_radius, cy - self._handle_radius,
                                  2 * self._handle_radius, 2 * self._handle_radius))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = self._hitHandle(event.x(), event.y())
            if self._dragging:
                self.grabMouse()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            v = self._posToValue(event.x())
            if self._dragging == 'min':
                self._value_min = max(self._min_val, min(v, self._value_max))
                if self._value_min > self._value_max:
                    self._value_max = self._value_min
            else:
                self._value_max = max(self._value_min, min(self._max_val, v))
                if self._value_max < self._value_min:
                    self._value_min = self._value_max
            self.update()
            self.valueChanged.emit(self._value_min, self._value_max)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self.releaseMouse()
            self._dragging = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DiscreteRangeSlider(QWidget):
    """五档离散范围滑块，用于可见枚数 0-4，带锚点 0-1-2-3-4，默认 0-0"""
    valueChanged = pyqtSignal(int, int)  # (min, max)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._min_val = 0
        self._max_val = 4
        self._value_min = 0
        self._value_max = 0
        self._dragging = None
        self._handle_radius = 5
        self._track_height = 3
        self.setMinimumHeight(36)
        self.setMinimumWidth(85)
        self.setMaximumWidth(118)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def getRange(self):
        return (self._value_min, self._value_max)

    def setRange(self, min_val: int, max_val: int):
        self._value_min = max(0, min(4, min_val))
        self._value_max = max(0, min(4, max_val))
        if self._value_min > self._value_max:
            self._value_min, self._value_max = self._value_max, self._value_min
        self.update()
        self.valueChanged.emit(self._value_min, self._value_max)

    def _valueToPos(self, value: int) -> float:
        r = self._max_val - self._min_val
        if r <= 0:
            return self._handle_radius
        track_left = self._handle_radius
        track_width = self.width() - 2 * self._handle_radius
        if track_width <= 0:
            return track_left
        frac = (value - self._min_val) / r
        return track_left + frac * track_width

    def _posToValue(self, x: float) -> int:
        track_left = self._handle_radius
        track_width = self.width() - 2 * self._handle_radius
        if track_width <= 0:
            return 0
        frac = max(0, min(1, (x - track_left) / track_width))
        return round(self._min_val + frac * (self._max_val - self._min_val))

    def _hitHandle(self, x: float, y: float) -> Optional[str]:
        cy = 14
        pos_min = self._valueToPos(self._value_min)
        pos_max = self._valueToPos(self._value_max)
        r = self._handle_radius + 8
        # 重叠时优先右侧手柄，便于向右拖动
        if (x - pos_max) ** 2 + (y - cy) ** 2 <= r ** 2:
            return 'max'
        if (x - pos_min) ** 2 + (y - cy) ** 2 <= r ** 2:
            return 'min'
        return None

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        h = self.height()
        cy = 14
        track_y = cy - self._track_height / 2

        # 轨道背景
        track_rect = QRectF(self._handle_radius, track_y,
                            self.width() - 2 * self._handle_radius, self._track_height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(200, 200, 210))
        painter.drawRoundedRect(track_rect, 1.5, 1.5)

        # 五个锚点刻度
        painter.setPen(QPen(QColor(120, 120, 130), 1))
        painter.setBrush(QColor(150, 150, 160))
        for v in range(5):
            px = self._valueToPos(v)
            painter.drawRect(int(px - 1), int(track_y - 1), 2, int(self._track_height + 2))

        # 选中范围高亮
        pos_min = self._valueToPos(self._value_min)
        pos_max = self._valueToPos(self._value_max)
        hl_rect = QRectF(pos_min - self._handle_radius, track_y,
                         pos_max - pos_min, self._track_height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(70, 130, 180))
        painter.drawRoundedRect(hl_rect, 1.5, 1.5)

        # 两个圆形手柄
        painter.setBrush(QColor(70, 130, 180))
        painter.setPen(QPen(QColor(50, 100, 150), 1))
        painter.drawEllipse(QRectF(pos_min - self._handle_radius, cy - self._handle_radius,
                                  2 * self._handle_radius, 2 * self._handle_radius))
        painter.drawEllipse(QRectF(pos_max - self._handle_radius, cy - self._handle_radius,
                                  2 * self._handle_radius, 2 * self._handle_radius))

        # 锚点标签 0-1-2-3-4
        painter.setPen(QColor(80, 80, 90))
        painter.setFont(QFont("", 9))
        for v in range(5):
            px = self._valueToPos(v)
            painter.drawText(int(px - 5), h - 2, str(v))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = self._hitHandle(event.x(), event.y())
            if self._dragging:
                self.grabMouse()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            v = self._posToValue(event.x())
            v = max(0, min(4, v))
            if self._dragging == 'min':
                self._value_min = max(0, min(v, self._value_max))
                if self._value_min > self._value_max:
                    self._value_max = self._value_min
            else:
                self._value_max = max(self._value_min, min(4, v))
                if self._value_max < self._value_min:
                    self._value_min = self._value_max
            self.update()
            self.valueChanged.emit(self._value_min, self._value_max)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            self.releaseMouse()
            self._dragging = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DownloadThread(QThread):
    """数据下载线程"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)
    
    def __init__(self, downloader: DataDownloader, start_year: int, end_year: int):
        super().__init__()
        self.downloader = downloader
        self.start_year = start_year
        self.end_year = end_year
    
    def run(self):
        try:
            if self.start_year == self.end_year:
                self.progress.emit(f"开始下载 {self.start_year} 年数据...")
            else:
                self.progress.emit(f"开始下载 {self.start_year}-{self.end_year} 年数据...")
            
            self.downloader.download_archive_logs(self.start_year, self.end_year)
            
            if self.start_year == self.end_year:
                self.progress.emit(f"✓ {self.start_year} 年数据下载完成")
            else:
                self.progress.emit(f"✓ {self.start_year}-{self.end_year} 年数据全部下载完成")
                
            self.finished.emit(True, "下载完成")
        except Exception as e:
            self.progress.emit(f"下载失败: {str(e)}")
            self.finished.emit(False, str(e))


class QueryThread(QThread):
    """实时分析查询线程"""
    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)  # (current, total)
    finished = pyqtSignal(bool, object)
    
    def __init__(self, analyzer: LiveAnalyzer, params: dict):
        super().__init__()
        self.analyzer = analyzer
        self.params = params
        self._should_cancel = False
    
    def cancel(self):
        """取消分析"""
        self._should_cancel = True
    
    def run(self):
        try:
            self.progress.emit("开始实时分析对局数据...")
            
            def progress_callback(current, total):
                self.progress_num.emit(current, total)
                if total > 0:
                    percent = current / total * 100
                    self.progress.emit(f"正在分析: {_fmt_int(current)}/{_fmt_int(total)} ({percent:.1f}%)")
                else:
                    self.progress.emit(f"正在分析: 已扫描 {_fmt_int(current)} 场")
            
            def should_cancel():
                return self._should_cancel
            
            result = self.analyzer.analyze_discard_pattern(
                **self.params,
                progress_callback=progress_callback,
                should_cancel=should_cancel
            )
            
            if self._should_cancel:
                self.progress.emit("分析已取消")
                self.finished.emit(False, "用户取消")
            else:
                self.progress.emit("分析完成")
                self.finished.emit(True, result)
        except Exception as e:
            logger.exception("查询失败")
            self.progress.emit(f"分析失败: {str(e)}")
            self.finished.emit(False, str(e))


class OutcomeQueryThread(QThread):
    """结局统计线程：仅统计和了率、放铳率"""
    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)
    finished = pyqtSignal(bool, object)

    def __init__(self, analyzer: LiveAnalyzer, params: dict):
        super().__init__()
        self.analyzer = analyzer
        self.params = params
        self._should_cancel = False

    def cancel(self):
        self._should_cancel = True

    def run(self):
        try:
            self.progress.emit("正在统计和铳率...")

            def progress_callback(current, total):
                self.progress_num.emit(current, total)
                if total > 0:
                    percent = current / total * 100
                    self.progress.emit(f"正在分析: {_fmt_int(current)}/{_fmt_int(total)} ({percent:.1f}%)")
                else:
                    self.progress.emit(f"正在分析: 已扫描 {_fmt_int(current)} 场")

            def should_cancel():
                return self._should_cancel

            outcome_params = {
                k: v for k, v in self.params.items()
                if k in ("query_pattern", "target_tile", "query_items", "dora_constraint", "dora_position_spec",
                         "visible_constraints", "riichi_constraint", "call_constraint",
                         "call_area_constraints", "turn_range", "sample_limit",
                         "exclude_south4", "exclude_south3", "prior_discard_exclusion",
                         "max_workers")
            }
            result = self.analyzer.compute_pattern_outcome_rates(
                **outcome_params,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )

            if self._should_cancel:
                self.progress.emit("已取消")
                self.finished.emit(False, "用户取消")
            else:
                self.progress.emit("统计完成")
                self.finished.emit(True, result)
        except Exception as e:
            logger.exception("结局统计失败")
            self.progress.emit(f"统计失败: {str(e)}")
            self.finished.emit(False, str(e))


class SampleThread(QThread):
    """样本收集线程"""
    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)
    finished = pyqtSignal(bool, object)  # success, samples list

    def __init__(self, analyzer: LiveAnalyzer, params: dict, sample_count: int, target_count_filter,
                 sample_pool=None, target_tile_filter=None, outcome_filter=None, deal_in_filter=None):
        super().__init__()
        self.analyzer = analyzer
        self.params = params
        self.sample_count = sample_count
        self.target_count_filter = target_count_filter
        self.sample_pool = sample_pool  # 主统计时预收集的样本池，有则无需二次遍历
        self.target_tile_filter = target_tile_filter  # 多目标时指定按哪个目标筛选
        self.outcome_filter = outcome_filter  # "win"|"deal_in"|"neither" 和铳率模式下的结局筛选
        self.deal_in_filter = deal_in_filter  # "hit"|"miss"|"furiten" 即时铳率模式筛选
        self._should_cancel = False

    def cancel(self):
        self._should_cancel = True

    def run(self):
        try:
            if self.sample_pool:
                self.progress.emit("从已分析结果中提取样本...")
            else:
                self.progress.emit("正在收集验证样本...")
            def progress_cb(cur, total):
                self.progress_num.emit(cur, total)
                self.progress.emit(f"已扫描: {_fmt_int(cur)} 场" + (f"/{_fmt_int(total)}" if total > 0 else ""))
            params = {k: v for k, v in self.params.items()
                      if k not in ("query_pattern_str", "is_combo", "query_items", "matched_states_cap", "call_area_constraints", "analysis_target")}
            samples = self.analyzer.collect_verification_samples(
                **params,
                sample_count=self.sample_count,
                target_count_filter=self.target_count_filter,
                target_tile_filter=self.target_tile_filter,
                progress_callback=progress_cb,
                should_cancel=lambda: self._should_cancel,
                sample_pool=self.sample_pool,
                outcome_filter=self.outcome_filter,
                deal_in_filter=self.deal_in_filter,
            )
            self.progress.emit(f"收集完成，共 {len(samples)} 条")
            self.finished.emit(True, samples)
        except Exception as e:
            logger.exception("样本收集失败")
            self.finished.emit(False, str(e))


class SampleDialog(QDialog):
    """样本展示对话框"""
    def __init__(self, parent, text: str, query_str: str):
        super().__init__(parent)
        self.setWindowTitle("验证样本")
        self.setMinimumSize(650, 500)
        layout = QVBoxLayout()
        self.text_edit = QTextBrowser()  # 使用 QTextBrowser 以支持 setOpenExternalLinks
        self.text_edit.setReadOnly(True)
        self.text_edit.setOpenExternalLinks(True)  # 点击链接在浏览器打开
        # 将 tenhou 牌谱 URL 转为可点击超链接后以 HTML 显示
        html_content = html.escape(text).replace("\n", "<br>")
        html_content = re.sub(
            r'(https://tenhou\.net/4/[^\s<]+)',
            r'<a href="\1">\1</a>',
            html_content
        )
        self.text_edit.setHtml(html_content)
        layout.addWidget(self.text_edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Close
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)
        self.setLayout(layout)
        self._text = text
        self._query_str = query_str

    def _save(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "保存样本", f"verify_samples_{self._query_str.replace('-', '_')}.txt",
            "文本文件 (*.txt)"
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._text)
                QMessageBox.information(self, "保存成功", f"已保存至 {path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))


# ---- 存档管理 ----
def _archive_file_path(db_path: str) -> Path:
    """存档 JSON 文件路径"""
    p = Path(db_path)
    return p.parent / "query_archive.json"


def _new_folder_id() -> str:
    """生成新文件夹 ID"""
    import uuid
    return "f_" + uuid.uuid4().hex[:12]


def _load_archive(db_path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """加载存档列表与文件夹。返回 (items, folders)。兼容旧格式。"""
    path = _archive_file_path(db_path)
    if not path.exists():
        return [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], []
    items = data.get("items", [])
    folders = data.get("folders", [])
    if not isinstance(folders, list):
        folders = []
    # 旧格式无 folder_id，补全
    for e in items:
        if "folder_id" not in e:
            e["folder_id"] = None
    return items, folders


def _save_archive(db_path: str, items: List[Dict[str, Any]], folders: List[Dict[str, Any]]) -> None:
    """保存存档列表与文件夹"""
    path = _archive_file_path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": items, "folders": folders}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise RuntimeError(f"保存存档失败: {e}")


def _build_pattern_summary(result: dict) -> str:
    """从查询结果提取舍牌模式摘要，用于列表展示与搜索"""
    if result.get("table_dist") is not None:
        hc = result.get("header_col", [])
        hr = result.get("header_row", [])
        return f"矩阵 {len(hc)}×{len(hr)}: " + " | ".join(hc[:3]) + ("..." if len(hc) > 3 else "")
    multi = result.get("multi_pattern", False)
    pr_list = result.get("pattern_results", []) if multi else []
    use_tenpai = result.get("analysis_target") == "tenpai"
    if multi and pr_list:
        parts = [
            f"{pr['pattern_str']} → {'听牌' if use_tenpai else pr['target']}"
            for pr in pr_list
        ]
        return "  |  ".join(parts)
    qp = result.get("query_pattern_str", "") or "-".join(result.get("query_pattern", []))
    target = "听牌" if use_tenpai else result.get("target_tile", "")
    return f"{qp} → {target}"


class ArchiveViewDialog(QDialog):
    """存档结果查看对话框，点击展开显示具体结果"""
    def __init__(self, parent, entry: Dict[str, Any]):
        super().__init__(parent)
        self.setWindowTitle("存档结果")
        self.setMinimumSize(700, 500)
        layout = QVBoxLayout()
        # 摘要标题
        summary = entry.get("pattern_summary", "")
        time_str = entry.get("created_at", "")[:19].replace("T", " ")
        title = QLabel(f"舍牌模式: {summary}\n存档时间: {time_str}")
        title.setStyleSheet("font-weight: bold; font-size: 12pt; color: #00d4ff;")
        title.setWordWrap(True)
        layout.addWidget(title)
        # 结果文本
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(entry.get("result_display_text", ""))
        layout.addWidget(self.text_edit)
        # 按钮
        btns = QDialogButtonBox()
        copy_btn = QPushButton("复制到 Excel")
        copy_btn.clicked.connect(self._copy_excel)
        save_btn = QPushButton("保存为文本")
        save_btn.clicked.connect(self._save_txt)
        btns.addButton(copy_btn, QDialogButtonBox.ActionRole)
        btns.addButton(save_btn, QDialogButtonBox.ActionRole)
        btns.addButton(QDialogButtonBox.Close)
        btns.rejected.connect(self.accept)
        layout.addWidget(btns)
        self.setLayout(layout)
        self._entry = entry

    def _copy_excel(self):
        excel = self._entry.get("excel_text", "")
        if excel:
            QApplication.clipboard().setText(excel)
            QMessageBox.information(self, "已复制", "分析结果已复制到剪贴板，可直接粘贴到 Excel 中。")
        else:
            QMessageBox.warning(self, "提示", "该存档无 Excel 格式数据")

    def _save_txt(self):
        from PyQt5.QtWidgets import QFileDialog
        text = self._entry.get("result_display_text", "")
        pattern_safe = re.sub(r'[\\/:*?"<>|]', "_", self._entry.get("pattern_summary", "result")[:40])
        path, _ = QFileDialog.getSaveFileName(
            self, "保存结果", f"archive_{pattern_safe}.txt", "文本文件 (*.txt)"
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                QMessageBox.information(self, "保存成功", f"已保存至 {path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))


PATTERN_HELP_HTML = """
<h2>舍牌模式输入说明</h2>
<p>用 <b>-</b> 或 <b>AND</b> 分隔各张牌，按出牌顺序从左到右书写。</p>

<h3>一、数牌</h3>
<ul>
<li><b>1m～9m</b> 萬子、<b>1p～9p</b> 筒子、<b>1s～9s</b> 索子</li>
<li><b>赤五</b>：<code>0m</code> 赤5万、<code>0p</code> 赤5筒、<code>0s</code> 赤5索；参与花色变换，0m/0p/0s 三者与数牌一起等价（如 3m-2m-0m ≡ 7s-8s-0s）；含赤五时等价变体为 9+9 个</li>
<li><b>花色通配符</b>：<code>m</code> 任意万字、<code>p</code> 任意饼子、<code>s</code> 任意索子（<u>单字符</u>，与 <code>1m</code>、<code>2p</code>、<code>3s</code> 等两字符具体牌区分）</li>
<li>等价：1s-3s 与 1m-3m、1p-3p 等自动等价（花色对称，1 种花色 3 变体，2–3 种花色 6 变体）</li>
</ul>

<h3>二、摸切标记 t 与 f</h3>
<ul>
<li><b>t</b>：必须摸切；<b>f</b>：手切或摸切皆可；无后缀为必须手切</li>
<li>例：<code>3mt-1m</code> = 3m 摸切、1m 手切；<code>3mf-1m</code> = 3m 手摸切皆可、1m 手切</li>
</ul>

<h3>三、立直宣言牌 r</h3>
<ul>
<li>牌后加 <b>r</b> 表示该牌为立直宣言牌（打出此牌宣告立直）</li>
<li>例：<code>3m-5mr</code> = 玩家先打 3m，再打 5m 时宣告立直；统计立直瞬间手牌中目标牌的数量</li>
<li>立直宣言牌必为摸切；立直后该玩家舍牌均为摸切</li>
<li><b>互斥</b>：r 与吃(c)、碰(p) 不可同现，立直玩家不可副露</li>
<li>含 r 时优先检索牌谱是否有玩家立直，无则快速跳过</li>
</ul>

<h3>四、通配符 * 与 $</h3>
<ul>
<li><b>*</b> 表示“任意摸切”，可匹配中间任意张摸切牌</li>
<li>例：<code>3m-*-1m</code> = 3m 与 1m 之间允许若干摸切，不允许手切</li>
<li><b>$</b> 表示“任意一张手切”（吃/碰之后的牌必为手切）</li>
<li>例：<code>c0p6p-$</code> = 用 0p6p 吃后打出任意一张手切牌</li>
</ul>

<h3>五、拆搭 cd1 / cd2 / cdm / cdp / cds</h3>
<p>搭子 = 两张同花色、数值差 1 或 2 的数牌（如 1m3m、2m3m、4s5s）。<b>1s9s 不是搭子</b>。拆搭须为手切，且另一张也须为手切。</p>
<ul>
<li><b>cd1</b>：任意拆搭（万/筒/索均可）</li>
<li><b>cd2</b>：拆搭，且拆搭花色 ≠ 下一张舍牌花色。例：<code>cd2-1m</code> 拆的不能是万</li>
<li><b>cdm</b> / <b>cdp</b> / <b>cds</b>：拆万字搭 / 饼搭 / 索搭</li>
<li>例：<code>cd1-3m</code> 先拆某搭子，再打 3m；<code>cd2-5s</code> 拆非索搭后打 5s</li>
</ul>

<h3>六、吃 / 碰占位</h3>
<p>在舍牌序列中表示“此处有一次吃或碰”，不占一张舍牌；<b>语义为具体吃的/碰的牌</b>，如 <code>4mc3m5m</code> 表示必须是用 3m5m 吃的 4m。</p>
<ul>
<li><b>吃</b>：任意 <code>(牌)c(牌)(牌)</code> 或 <code>c(牌)(牌)</code>，如 <code>1sc2s3s</code>、<code>4mc3m5m</code>、<code>c5m6m</code></li>
<li><b>碰</b>：<code>p1z1z</code> 用两个东碰（只碰东）；<code>pkfkf</code> 用客风碰</li>
<li><b>等价变体</b>：含吃或数牌碰时<u>不</u>生成花色等价变体；仅碰字牌时仍可生成变体</li>
</ul>

<h3>七、字牌</h3>
<p><b>1z～7z</b> 对应：东、南、西、北、白、发、中</p>

<h3>八、逻辑符号 NOT / OR / AND / [xy] 范围</h3>
<ul>
<li><b>[xy] 数字范围</b>：<code>[25]m</code> = 2m、3m、4m、5m 其一；<code>[17]z</code> = 1z～7z 其一；可与花色 m/p/s/z 搭配，参与等价变换</li>
<li><b>NOT[xy] 排除范围</b>：<code>NOT[45]m</code> = 除 4m、5m 外任意牌；<code>NOT[12]z</code> = 除东、南外任意牌</li>
<li><b>NOT 字牌</b>：<code>zNOT1z</code> 表示任意字牌但排除东；<code>zNOT1zNOT2z</code> 排除东、南</li>
<li><b>NOT 花色</b>：<code>NOTm</code> 任意一张非万字（筒/索/字均可）；<code>NOTp</code> 非饼；<code>NOTs</code> 非索</li>
<li><b>OR</b>：<code>3mOR5m</code> 表示该位置为 3m 或 5m 其一；<code>3mOR[25]m</code> 等价 3m 或 2m～5m 其一</li>
<li><b>AND</b>：与 <b>-</b> 同级，作舍牌顺序分隔符。例：<code>3mAND4m-zNOT1z</code> = 第一张 3m、第二张 4m、第三张任意字牌（非东）</li>
</ul>

<h3>九、字牌占位符</h3>
<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">
<tr><th>符号</th><th>含义</th></tr>
<tr><td><code>z</code></td><td>任意字牌（手切）</td></tr>
<tr><td><code>zt</code></td><td>任意字牌且摸切</td></tr>
<tr><td><code>zf</code></td><td>自风（当前局座风）</td></tr>
<tr><td><code>kf</code></td><td>任意一张客风</td></tr>
<tr><td><code>z1</code> <code>z2</code> <code>z3</code></td><td>互不相同的字牌</td></tr>
<tr><td><code>kf1</code> <code>kf2</code> <code>kf3</code></td><td>互不相同的客风</td></tr>
</table>

<h3>十、示例</h3>
<ul>
<li><code>7s-9s</code>：相邻打出 7s、9s</li>
<li><code>7s-0m-9s</code>：7s、赤5万、9s</li>
<li><code>7s-4mc3m5m-9s</code>：7s、必须是用 3m5m 吃的 4m、9s（不含等价变体）</li>
<li><code>3m-z</code>：3m 后打任意字牌（6 种等价）</li>
<li><code>zt-zt</code>：连续两张摸切字牌（49 种等价）</li>
<li><code>z1-z2</code>：两张不同字牌（42 种等价）</li>
<li><code>3m-*-1m</code>：3m 与 1m 之间可隔若干摸切</li>
<li><code>zf-kf</code>：先打自风，再打客风</li>
<li><code>c0p6p-$</code>：用 0p6p 吃后打出任意一张手切牌</li>
<li><code>3m-5mr</code>：打 3m 后，打 5m 时立直（统计立直瞬间手牌目标数）</li>
<li><code>cd1-3m</code>：先拆某搭子（如 1m3m、4p5p），再打 3m</li>
<li><code>cd2-5s</code>：拆非索搭后打 5s</li>
<li><code>3m-NOTm-5m</code>：3m、任意非万字、5m</li>
<li><code>[25]m-6m</code>：2m～5m 其一，接着 6m（等价变换适用）</li>
<li><code>NOT[45]m-8s</code>：除 4m、5m 外任意一张，接着 8s</li>
<li><code>3mAND4m-zNOT1z</code>：3m、4m，第三张任意字牌但非东</li>
<li><code>3mOR5m-z</code>：第一张为 3m 或 5m，第二张任意字牌</li>
<li><code>[28]mf-6m</code>：2m～8m 其一（手摸切皆可），接着 6m；<code>mf</code>/<code>pf</code>/<code>sf</code> 同理</li>
</ul>

<h3>十一、前段禁打约束</h3>
<p>在约束区域「前段禁打」中输入模式，表示<u>巡目范围开始前</u>该玩家不能打出这些牌。支持与舍牌模式相同的语法，并与主模式同步等价变换。</p>
<ul>
<li><code>NOTm</code>：前段不能打出任何万字；<code>NOTmf</code> 同义且手摸切皆可；变体 1p-2p 时自动变为 NOTp</li>
<li><code>4mOR2m</code>：前段不能打出 4m 或 2m；用 OR 组合多牌</li>
<li>例：舍牌 <code>1m-2m</code> 目标 4m、4-9 巡，前段 <code>NOTm</code> → 排除第 3 巡前已打过万字的样本</li>
</ul>
"""


class PatternHelpDialog(QDialog):
    """舍牌模式使用说明对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("舍牌模式输入说明")
        self.setMinimumSize(480, 420)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setHtml(PATTERN_HELP_HTML)
        browser.setOpenExternalLinks(False)
        layout.addWidget(browser)
        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignRight)


TARGET_HELP_HTML = """
<h2>目标牌输入说明</h2>
<p>统计舍牌序列匹配时，手牌中<u>持有该目标牌</u>的数量分布。</p>

<h3>一、单张</h3>
<ul>
<li><b>6s</b>、<b>2m</b>、<b>东</b>、<b>1z</b> 等：统计该牌 0/1/2/3 张的概率</li>
<li>字牌可用中文或 1z～7z（东=1z、南=2z、西=3z、北=4z、白=5z、发=6z、中=7z）</li>
</ul>

<h3>二、搭子（多张组合）</h3>
<ul>
<li><b>1m3m</b> 或 <b>1m-3m</b>：统计手牌是否<u>同时</u>有 1m 和 3m（各至少 1 张）</li>
<li><b>13m</b>：简写，等价 1m3m</li>
<li>搭子模式结果只有两种：没有 / 有</li>
</ul>

<h3>三、多目标（同时分析多张牌）</h3>
<ul>
<li><b>6s 2m 5p</b> 或 <b>6s,2m,5p</b>：用空格或逗号分隔，一次分析同时统计 6s、2m、5p 各 0/1/2/3 张的分布</li>
<li>一次匹配、一次遍历，无需多次查询</li>
<li>生成样本时可选择为哪个目标牌筛选</li>
</ul>

<h3>四、示例</h3>
<ul>
<li>舍牌 <code>7s-9s</code> 目标 <code>8s</code>：听 8s 时，手牌有 0/1/2/3 张 8s 的概率</li>
<li>舍牌 <code>1s-3s</code> 目标 <code>2s</code>：听 2s 时，手牌有 2s 的概率</li>
<li>舍牌 <code>1m-3m</code> 目标 <code>1m3m</code>：已有 1m3m 搭子时，手牌是否握有该搭子</li>
<li>舍牌 <code>7s-9s</code> 目标 <code>6s 8s</code>：同时统计 6s 和 8s 的存量分布</li>
</ul>
"""


class TargetHelpDialog(QDialog):
    """目标牌使用说明对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("目标牌输入说明")
        self.setMinimumSize(440, 320)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setHtml(TARGET_HELP_HTML)
        browser.setOpenExternalLinks(False)
        layout.addWidget(browser)
        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn, alignment=Qt.AlignRight)


class TileIllustrationWidget(QWidget):
    """麻将示意图生成功能页（可嵌入标签页或对话框）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_image = None
        layout = QVBoxLayout(self)

        # 输入区
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("符号输入:"))
        self.notation_edit = QLineEdit()
        self.notation_edit.setPlaceholderText("例: 4mc3m5m（用3m5m吃4m）、p1z1z（碰东）、7s-?-9s、1m-b-3m（b=牌背）")
        self.notation_edit.setMinimumWidth(280)
        self.notation_edit.returnPressed.connect(self._generate)
        input_row.addWidget(self.notation_edit, 1)
        layout.addLayout(input_row)

        discard_row = QHBoxLayout()
        discard_row.addWidget(QLabel("舍牌输入:"))
        self.discard_edit = QLineEdit()
        self.discard_edit.setPlaceholderText("可选。语法同上，使用桌上视角牌。例: 7s-9s、4m-b-5m")
        self.discard_edit.setMinimumWidth(280)
        self.discard_edit.returnPressed.connect(self._generate)
        discard_row.addWidget(self.discard_edit, 1)
        self.gen_btn = QPushButton("生成")
        self.gen_btn.clicked.connect(self._generate)
        discard_row.addWidget(self.gen_btn)
        layout.addLayout(discard_row)

        hint = QLabel("上排=正放牌，下排=舍牌（桌上视角）。支持 ?、b（牌背）、4mc3m5m、p1z1z、7s-9s 等。")
        hint.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(hint)

        # 预览区（麻将牌需浅色底才清晰）
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(400, 220)
        self.preview_label.setStyleSheet(
            "background-color: #21262d; border: 1px solid #30363d; border-radius: 6px;"
        )
        self.preview_label.setText("输入符号后点击「生成」预览")
        layout.addWidget(self.preview_label, 1)

        # 保存按钮
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton("保存为图片")
        self.save_btn.clicked.connect(self._save_image)
        self.save_btn.setEnabled(False)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _generate(self):
        notation = self.notation_edit.text().strip()
        discard_notation = self.discard_edit.text().strip() or None
        if not notation and not discard_notation:
            return
        try:
            from PyQt5.QtGui import QPixmap
            img = render_illustration_to_qimage(notation, scale=3.0, discard_notation=discard_notation)
            self._current_image = img
            pix = QPixmap.fromImage(img)
            scaled = pix.scaled(380, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview_label.setPixmap(scaled)
            self.save_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.warning(self, "生成失败", str(e))

    def _save_image(self):
        if self._current_image is None:
            return
        from PyQt5.QtWidgets import QFileDialog
        from PyQt5.QtCore import QStandardPaths
        n = self.notation_edit.text().strip()
        d = self.discard_edit.text().strip()
        base = re.sub(r'[\\/:*?"<>|]', "_", (n + "_" + d if d else n).replace("-", "_")[:40])
        default_name = f"mahjong_{base}.png" if base else "mahjong.png"
        start_dir = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation) or str(Path.home())
        default_path = str(Path(start_dir) / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "保存示意图", default_path, "PNG 图片 (*.png)",
            options=QFileDialog.DontUseNativeDialog,
        )
        if path:
            if self._current_image.save(path):
                QMessageBox.information(self, "保存成功", f"已保存至 {path}")
            else:
                QMessageBox.critical(self, "保存失败", "无法写入文件")


class TileIllustrationDialog(QDialog):
    """麻将示意图生成对话框（弹窗形式，兼容旧入口）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("麻将示意图")
        self.setMinimumSize(520, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(TileIllustrationWidget(self))


def _parse_pattern_target(line: str) -> Optional[Tuple[List[str], str]]:
    """解析舍牌模式与目标牌，支持多种分隔：→ 、空格、Tab、逗号。例：7s-9s 6s、7s-9s→6s"""
    line = line.strip()
    if not line:
        return None
    parts = None
    if "→" in line:
        parts = line.split("→", 1)
    elif "\t" in line:
        parts = line.split("\t", 1)
    elif "," in line or "，" in line:
        sep = "，" if "，" in line else ","
        parts = line.replace("，", ",").split(",", 1)
    else:
        toks = line.split()
        if len(toks) >= 2:
            target = toks[-1]
            pattern_str = "-".join(toks[:-1])
            parts = [pattern_str, target]
    if not parts or len(parts) != 2:
        return None
    pattern_str, target = parts[0].strip(), parts[1].strip()
    if not pattern_str or not target:
        return None
    pattern = split_discard_pattern(pattern_str)
    return (pattern, target) if pattern else None


def _turn_range_validator():
    """输入 1-18 的整数验证器"""
    return QIntValidator(1, 18)


def _parse_turn_range(s: str) -> Optional[Tuple[int, int]]:
    """解析巡目范围，支持 1-3、1~3、1 3、1,3 等格式"""
    s = s.strip().replace("～", "-").replace("~", "-").replace("，", ",")
    parts = None
    if "-" in s:
        parts = s.split("-", 1)
    elif "," in s:
        parts = s.split(",", 1)
    elif " " in s:
        parts = s.split(None, 1)
    if parts and len(parts) == 2:
        try:
            a, b = int(parts[0].strip()), int(parts[1].strip())
            if 1 <= a <= b <= 18:
                return (a, b)
        except (ValueError, IndexError):
            pass
    return None


class GridQueryThread(QThread):
    """主界面矩阵分析线程：M>1 巡目时调用 analyze_discard_pattern_grid"""
    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)
    finished = pyqtSignal(bool, object)

    def __init__(self, analyzer, params: dict, patterns: List[Tuple[List[str], str]],
                 turn_ranges: List[Tuple[int, int]], analysis_target: str = "target_count"):
        super().__init__()
        self.analyzer = analyzer
        self.params = params
        self.patterns = patterns
        self.turn_ranges = turn_ranges
        self.analysis_target = analysis_target
        self._should_cancel = False

    def cancel(self):
        self._should_cancel = True

    def run(self):
        try:
            self.progress.emit(f"矩阵分析中：{len(self.patterns)} 模式 × {len(self.turn_ranges)} 巡目...")
            shared = {k: v for k, v in self.params.items()
                      if k in ("dora_constraint", "dora_position_spec", "riichi_constraint", "call_constraint",
                               "call_area_constraints", "visible_constraints", "sample_limit",
                               "total_logs_hint", "analysis_batch_size", "exclude_south4", "exclude_south3",
                               "prior_discard_exclusion", "max_workers")}

            def progress_fn(c, t):
                if t > 0:
                    self.progress_num.emit(c, t)
                if t and (t <= 100 or c <= 10 or c % max(1, t // 20) == 0):
                    self.progress.emit(f"已处理 {c:,}/{t:,} 场对局")

            result = self.analyzer.analyze_discard_pattern_grid(
                patterns=self.patterns,
                turn_ranges=self.turn_ranges,
                merge_keys=[1, 2],
                analysis_target=self.analysis_target,
                progress_callback=progress_fn,
                should_cancel=lambda: self._should_cancel,
                **shared,
            )
            if self._should_cancel:
                self.finished.emit(False, "用户取消")
                return
            result["patterns"] = self.patterns
            result["turn_ranges"] = self.turn_ranges
            result["analysis_target"] = self.analysis_target
            result["header_row"] = [f"{tmin}-{tmax}巡" for tmin, tmax in self.turn_ranges]
            result["header_col"] = [f"{'-'.join(p)}→{t}" for p, t in self.patterns]
            self.finished.emit(True, result)
        except Exception as e:
            logger.exception("矩阵分析失败")
            self.finished.emit(False, str(e))


class MatrixDisplayDialog(QDialog):
    """矩阵展示窗口：勾选 0/1/2/3 张，动态预览并复制 Excel 格式"""
    def __init__(self, parent, grid_result: dict):
        super().__init__(parent)
        self.setWindowTitle("矩阵数据展示")
        self.setMinimumSize(560, 420)
        self._result = grid_result
        self._table_dist = grid_result.get("table_dist", {})
        self._header_row = grid_result.get("header_row", [])
        self._header_col = grid_result.get("header_col", [])
        self._analysis_target = grid_result.get("analysis_target", "target_count")
        self._use_tenpai = (self._analysis_target == "tenpai")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        use_tenpai = self._use_tenpai
        labels = ["未听牌", "听牌"] if use_tenpai else ["0张", "1张", "2张", "3张"]
        keys = [0, 1] if use_tenpai else [0, 1, 2, 3]
        cb_row = QHBoxLayout()
        cb_row.addWidget(QLabel("勾选展示（可多选合并）:"))
        self._checkboxes = []
        for k, lbl in zip(keys, labels):
            cb = QCheckBox(lbl)
            cb.setChecked(k in (1, 2) if not use_tenpai else True)
            cb.stateChanged.connect(self._update_preview)
            self._checkboxes.append((k, cb))
            cb_row.addWidget(cb)
        cb_row.addStretch()
        layout.addLayout(cb_row)
        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMinimumHeight(200)
        layout.addWidget(self._preview)
        btn_row = QHBoxLayout()
        self._copy_btn = QPushButton("复制到 Excel")
        self._copy_btn.clicked.connect(self._copy_excel)
        btn_row.addWidget(self._copy_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self._update_preview()

    def _get_checked_keys(self):
        return [k for k, cb in self._checkboxes if cb.isChecked()]

    def _compute_table(self):
        keys = self._get_checked_keys()
        if not keys:
            return None
        out = {}
        for (tr_idx, pat_idx), dist in self._table_dist.items():
            val = sum(dist.get(k, 0) for k in keys)
            out[(tr_idx, pat_idx)] = round(val, 2)
        return out

    def _update_preview(self):
        tbl = self._compute_table()
        if tbl is None:
            self._preview.setPlainText("请至少勾选一项")
            return
        header_col = self._header_col
        header_row = self._header_row
        rows = []
        rows.append("巡目范围\t" + "\t".join(header_col))
        for tr_idx, tr_label in enumerate(header_row):
            cells = [tr_label]
            for pat_idx in range(len(header_col)):
                v = tbl.get((tr_idx, pat_idx), "")
                cells.append(str(v))
            rows.append("\t".join(cells))
        self._preview.setPlainText("\n".join(rows))

    def _copy_excel(self):
        tbl = self._compute_table()
        if tbl is None:
            QMessageBox.information(self, "提示", "请至少勾选一项")
            return
        header_col = self._header_col
        header_row = self._header_row
        rows = []
        rows.append("巡目范围\t" + "\t".join(header_col))
        for tr_idx, tr_label in enumerate(header_row):
            cells = [tr_label]
            for pat_idx in range(len(header_col)):
                v = tbl.get((tr_idx, pat_idx), "")
                cells.append(str(v))
            rows.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(rows))
        QMessageBox.information(self, "已复制", "表格已复制到剪贴板，可粘贴到 Excel 中绘制折线图。")


class BatchChartThread(QThread):
    """批量分析线程：依次运行 舍牌模式×巡目范围 组合"""
    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)
    finished = pyqtSignal(bool, object)  # success, (table_data, header_row, header_col) or error_str

    def __init__(self, analyzer, constraint_params: dict, patterns: List[Tuple[List[str], str]],
                 turn_ranges: List[Tuple[int, int]], merge_keys: List[int],
                 analysis_target: str = "target_count"):
        super().__init__()
        self.analyzer = analyzer
        self.constraint_params = constraint_params
        self.patterns = patterns
        self.turn_ranges = turn_ranges
        self.merge_keys = merge_keys
        self.analysis_target = analysis_target
        self._should_cancel = False

    def cancel(self):
        self._should_cancel = True

    def run(self):
        try:
            total = len(self.patterns) * len(self.turn_ranges)
            shared = self.constraint_params
            shared_filtered = {k: v for k, v in shared.items()
                              if k in ("dora_constraint", "dora_position_spec", "riichi_constraint", "call_constraint",
                                       "call_area_constraints", "visible_constraints", "sample_limit",
                                       "total_logs_hint", "analysis_batch_size", "exclude_south4", "exclude_south3",
                                       "prior_discard_exclusion", "max_workers", "gc_interval_batches")}
            self.progress.emit(f"单次扫描分析 {len(self.patterns)} 模式 × {len(self.turn_ranges)} 巡目...")

            def progress_fn(c, t):
                if t > 0:
                    self.progress_num.emit(c, t)
                if t and (t <= 100 or c <= 10 or c % max(1, t // 20) == 0):
                    self.progress.emit(f"已处理 {c:,}/{t:,} 场对局")

            result = self.analyzer.analyze_discard_pattern_grid(
                patterns=self.patterns,
                turn_ranges=self.turn_ranges,
                merge_keys=self.merge_keys,
                analysis_target=self.analysis_target,
                progress_callback=progress_fn,
                should_cancel=lambda: self._should_cancel,
                **shared_filtered,
            )
            if self._should_cancel:
                self.finished.emit(False, "用户取消")
                return
            table = result.get("table", {})
            header_row = [f"{tmin}-{tmax}巡" for tmin, tmax in self.turn_ranges]
            header_col = [f"{'-'.join(p)}→{t}" for p, t in self.patterns]
            elapsed = result.get("elapsed_seconds", 0)
            self.finished.emit(True, (table, header_row, header_col, elapsed))
        except Exception as e:
            logger.exception("批量分析失败")
            self.finished.emit(False, str(e))


class BatchChartDialog(QDialog):
    """批量生成折线图数据对话框"""
    def __init__(self, parent: "MainWindow"):
        super().__init__(parent)
        self.main_window = parent
        self.setWindowTitle("生成折线图数据")
        self.setMinimumSize(620, 520)
        self._table_data = None
        self._header_row = None
        self._header_col = None
        self.batch_thread = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # 说明
        hint = QLabel("批量分析 舍牌模式×巡目范围，生成表格：行=巡目范围，列=舍牌模式，单元格=合并概率")
        hint.setStyleSheet("color: #8b949e;")
        layout.addWidget(hint)

        # 舍牌模式（多行）
        layout.addWidget(QLabel("舍牌模式列表（每行 模式+目标，可用空格/→/逗号分隔）:"))
        self.patterns_edit = QTextEdit()
        self.patterns_edit.setPlaceholderText("7s-9s 6s\nc0p6p-$ 6s\n3m-5m→4m\n7s 9s 6s")
        self.patterns_edit.setMaximumHeight(90)
        layout.addWidget(self.patterns_edit)

        # 巡目范围（多行）
        layout.addWidget(QLabel("巡目范围列表（每行 最小-最大，支持 1-3、1~3、1 3）:"))
        self.turn_ranges_edit = QTextEdit()
        self.turn_ranges_edit.setPlaceholderText("1-3\n4-6\n7-9\n1 6\n10~12")
        self.turn_ranges_edit.setMaximumHeight(70)
        layout.addWidget(self.turn_ranges_edit)

        # 约束条件（勾选后使用本对话框设置，否则用主界面）
        self.constraint_group = QGroupBox("约束条件（勾选后使用下方设置，否则用主界面）")
        self.constraint_group.setCheckable(True)
        self.constraint_group.setChecked(False)
        cg_layout = QVBoxLayout()

        dora_row = QHBoxLayout()
        self.batch_dora_irrelevant = QRadioButton("宝牌无关")
        self.batch_dora_irrelevant.setChecked(True)
        self.batch_dora_specific = QRadioButton("宝牌为")
        self.batch_dora_input = QLineEdit()
        self.batch_dora_input.setPlaceholderText("例: 6s")
        self.batch_dora_input.setMaximumWidth(55)
        dora_row.addWidget(self.batch_dora_irrelevant)
        dora_row.addWidget(self.batch_dora_specific)
        dora_row.addWidget(self.batch_dora_input)
        dora_row.addStretch()
        cg_layout.addLayout(dora_row)

        riichi_row = QHBoxLayout()
        self.batch_riichi_any = QRadioButton("立直: 任意")
        self.batch_riichi_any.setChecked(True)
        self.batch_riichi_has = QRadioButton("有人立直")
        self.batch_riichi_no = QRadioButton("无人立直")
        riichi_row.addWidget(self.batch_riichi_any)
        riichi_row.addWidget(self.batch_riichi_has)
        riichi_row.addWidget(self.batch_riichi_no)
        riichi_row.addStretch()
        cg_layout.addLayout(riichi_row)

        call_row = QHBoxLayout()
        self.batch_call_any = QRadioButton("副露: 任意")
        self.batch_call_any.setChecked(True)
        self.batch_call_has = QRadioButton("有人副露")
        self.batch_call_no = QRadioButton("无人副露")
        call_row.addWidget(self.batch_call_any)
        call_row.addWidget(self.batch_call_has)
        call_row.addWidget(self.batch_call_no)
        call_row.addStretch()
        cg_layout.addLayout(call_row)

        call_area_row = QHBoxLayout()
        call_area_row.addWidget(QLabel("副露区域:"))
        self.batch_call_area_inputs = []
        for i in range(4):
            le = QLineEdit()
            le.setPlaceholderText("pzfzf")
            le.setMaximumWidth(70)
            self.batch_call_area_inputs.append(le)
            call_area_row.addWidget(le)
        call_area_row.addStretch()
        cg_layout.addLayout(call_area_row)

        prior_excl_row = QHBoxLayout()
        prior_excl_row.addWidget(QLabel("前段禁打:"))
        self.batch_prior_discard_exclusion_input = QLineEdit()
        self.batch_prior_discard_exclusion_input.setPlaceholderText("例: NOTm 或 4mOR2m")
        self.batch_prior_discard_exclusion_input.setToolTip(
            "巡目范围开始前不能打出这些牌。随 x 轴巡目变化：如 4-6 巡时指第 3 巡前；7-9 巡时指第 6 巡前"
        )
        self.batch_prior_discard_exclusion_input.setMinimumWidth(140)
        self.batch_prior_discard_exclusion_input.setMaximumWidth(200)
        prior_excl_row.addWidget(self.batch_prior_discard_exclusion_input)
        prior_excl_row.addStretch()
        cg_layout.addLayout(prior_excl_row)

        # 场上可见枚数
        vc_row = QHBoxLayout()
        vc_row.addWidget(QLabel("场上可见枚数:"))
        self.batch_use_main_constraints = QCheckBox("使用主界面场上可见枚数")
        self.batch_use_main_constraints.setChecked(True)
        vc_row.addWidget(self.batch_use_main_constraints)
        vc_row.addStretch()
        cg_layout.addLayout(vc_row)

        sync_btn = QPushButton("从主界面同步约束")
        sync_btn.clicked.connect(self._sync_constraints_from_main)
        cg_layout.addWidget(sync_btn)

        self.constraint_group.setLayout(cg_layout)
        layout.addWidget(self.constraint_group)

        # 分析目标
        at_row = QHBoxLayout()
        at_row.addWidget(QLabel("分析目标:"))
        self.analysis_target_combo = QComboBox()
        self.analysis_target_combo.addItem("目标牌存量", "target_count")
        self.analysis_target_combo.addItem("是否听牌", "tenpai")
        at_row.addWidget(self.analysis_target_combo)
        at_row.addStretch()
        layout.addLayout(at_row)

        # 概率合并：勾选要合并的项
        merge_row = QHBoxLayout()
        merge_row.addWidget(QLabel("概率合并（勾选要合并显示的项）:"))
        self.merge_0 = QCheckBox("0张")
        self.merge_1 = QCheckBox("1张")
        self.merge_2 = QCheckBox("2张")
        self.merge_3 = QCheckBox("3张")
        self.merge_1.setChecked(True)
        self.merge_2.setChecked(True)
        merge_row.addWidget(self.merge_0)
        merge_row.addWidget(self.merge_1)
        merge_row.addWidget(self.merge_2)
        merge_row.addWidget(self.merge_3)
        merge_row.addStretch()
        layout.addLayout(merge_row)
        merge_hint = QLabel("例：勾选 1张+2张 表示单元格显示「有1张或2张」的概率和。听牌/搭子模式仅用 0张、1张")
        merge_hint.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(merge_hint)

        # 开始 / 取消
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("开始批量分析")
        self.start_btn.clicked.connect(self._start_batch)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._cancel_batch)
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        # 结果预览
        layout.addWidget(QLabel("结果预览（可复制）:"))
        self.result_preview = QTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setMinimumHeight(120)
        layout.addWidget(self.result_preview)

        # 复制 / 保存
        out_row = QHBoxLayout()
        self.copy_btn = QPushButton("复制到剪贴板")
        self.copy_btn.clicked.connect(self._copy_result)
        self.copy_btn.setEnabled(False)
        self.save_btn = QPushButton("保存为文件")
        self.save_btn.clicked.connect(self._save_result)
        self.save_btn.setEnabled(False)
        out_row.addWidget(self.copy_btn)
        out_row.addWidget(self.save_btn)
        out_row.addStretch()
        layout.addLayout(out_row)

    def _sync_constraints_from_main(self):
        """从主界面同步约束到本对话框"""
        mw = self.main_window
        self.batch_dora_irrelevant.setChecked(mw.dora_irrelevant_radio.isChecked())
        self.batch_dora_specific.setChecked(mw.dora_specific_radio.isChecked())
        self.batch_dora_input.setText(mw.dora_tile_input.text())
        self.batch_riichi_any.setChecked(mw.riichi_any_radio.isChecked())
        self.batch_riichi_has.setChecked(mw.riichi_has_radio.isChecked())
        self.batch_riichi_no.setChecked(mw.riichi_no_radio.isChecked())
        self.batch_call_any.setChecked(mw.call_any_radio.isChecked())
        self.batch_call_has.setChecked(mw.call_has_radio.isChecked())
        self.batch_call_no.setChecked(mw.call_no_radio.isChecked())
        for i, le in enumerate(self.batch_call_area_inputs):
            if i < len(mw.call_area_inputs):
                le.setText(mw.call_area_inputs[i].text())
        self.batch_prior_discard_exclusion_input.setText(mw.prior_discard_exclusion_input.text())
        QMessageBox.information(self, "已同步", "约束条件已从主界面同步")

    def _build_constraint_params(self) -> dict:
        """构建约束参数字典（本对话框或主界面）"""
        mw = self.main_window
        cached = _load_db_status_from_cache(mw.db_path)
        # prior_discard_exclusion：勾选约束组时用本对话框，否则用主界面
        if self.constraint_group.isChecked():
            prior_excl = self.batch_prior_discard_exclusion_input.text().strip() or None
        else:
            prior_excl = mw.prior_discard_exclusion_input.text().strip() or None
        base = {
            "sample_limit": mw.sample_limit_input.value(),
            "total_logs_hint": cached.get("total_logs") if cached else None,
            "matched_states_cap": mw.matched_states_cap_spin.value(),
            "analysis_batch_size": mw.analysis_batch_size_spin.value(),
            "exclude_south4": mw.exclude_south4_check.isChecked(),
            "exclude_south3": mw.exclude_south3_check.isChecked(),
            "prior_discard_exclusion": prior_excl,
            "max_workers": mw.max_workers_spin.value(),
            "gc_interval_batches": mw.gc_interval_batches_spin.value(),
        }
        if self.constraint_group.isChecked():
            dora = "dora_unrelated" if self.batch_dora_irrelevant.isChecked() else (self.batch_dora_input.text().strip() or "any")
            dora_pos = []  # Batch dialog has no dora_matches_position UI
            riichi = "any" if self.batch_riichi_any.isChecked() else ("has_riichi" if self.batch_riichi_has.isChecked() else "no_riichi")
            call = "any" if self.batch_call_any.isChecked() else ("has_call" if self.batch_call_has.isChecked() else "no_call")
            call_area = [le.text().strip() for le in self.batch_call_area_inputs if le.text().strip()][:4]
            visible = {}
            if self.batch_use_main_constraints.isChecked():
                visible = mw._get_visible_constraints_from_ui() or {}
            base.update(dora_constraint=dora, dora_position_spec=dora_pos, riichi_constraint=riichi, call_constraint=call,
                call_area_constraints=call_area if call_area else None, visible_constraints=visible if visible else None)
        else:
            if mw.dora_irrelevant_radio.isChecked():
                dora = "dora_unrelated"
                dora_pos = []
            elif getattr(mw, "dora_matches_position_radio", None) and mw.dora_matches_position_radio.isChecked():
                dora = "dora_matches_position"
                dora_pos = mw._parse_dora_position_spec()
            else:
                dora = mw.dora_tile_input.text().strip() or "any"
                dora_pos = []
            riichi = "any" if mw.riichi_any_radio.isChecked() else ("has_riichi" if mw.riichi_has_radio.isChecked() else "no_riichi")
            call = "any" if mw.call_any_radio.isChecked() else ("has_call" if mw.call_has_radio.isChecked() else "no_call")
            call_area = [le.text().strip() for le in mw.call_area_inputs if le.text().strip()][:4]
            visible = mw._get_visible_constraints_from_ui() or {}
            base.update(dora_constraint=dora, dora_position_spec=dora_pos, riichi_constraint=riichi, call_constraint=call,
                call_area_constraints=call_area if call_area else None, visible_constraints=visible if visible else None)
        return base

    def _get_merge_keys(self) -> List[int]:
        keys = []
        if self.merge_0.isChecked():
            keys.append(0)
        if self.merge_1.isChecked():
            keys.append(1)
        if self.merge_2.isChecked():
            keys.append(2)
        if self.merge_3.isChecked():
            keys.append(3)
        return keys if keys else [1, 2]

    def _start_batch(self):
        patterns = []
        for line in self.patterns_edit.toPlainText().strip().split("\n"):
            pt = _parse_pattern_target(line)
            if pt:
                patterns.append(pt)
        if not patterns:
            QMessageBox.warning(self, "输入错误", "请至少输入一个有效的舍牌模式（例: 7s-9s 6s 或 7s-9s→6s）")
            return
        turn_ranges = []
        for line in self.turn_ranges_edit.toPlainText().strip().split("\n"):
            tr = _parse_turn_range(line)
            if tr:
                turn_ranges.append(tr)
        if not turn_ranges:
            QMessageBox.warning(self, "输入错误", "请至少输入一个有效的巡目范围（例: 1-3 或 1 3）")
            return
        total = len(patterns) * len(turn_ranges)
        if total > 100:
            if QMessageBox.question(self, "确认", f"将分析 {total} 个组合（单次扫描，可能较久）。确定继续？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return

        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.status_label.setText(f"单次扫描分析 {total} 个组合...")

        merge_keys = self._get_merge_keys()
        analysis_target = self.analysis_target_combo.currentData() or "target_count"
        constraint_params = self._build_constraint_params()
        self.batch_thread = BatchChartThread(
            self.main_window.analyzer, constraint_params,
            patterns, turn_ranges, merge_keys, analysis_target
        )
        self.batch_thread.progress.connect(self.status_label.setText)
        self.batch_thread.progress_num.connect(self._on_progress)
        self.batch_thread.finished.connect(self._on_finished)
        self.batch_thread.start()

    def _on_progress(self, current: int, total: int):
        if total > 0:
            self.progress_bar.setValue(int(current / total * 100))

    def _on_finished(self, success: bool, result):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        if success:
            table, header_row, header_col = result[:3]
            elapsed = result[3] if len(result) > 3 else 0
            self._table_data = table
            self._header_row = header_row
            self._header_col = header_col
            text = self._build_table_text()
            self.result_preview.setPlainText(text)
            self.copy_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self.status_label.setText(f"分析完成（耗时 {elapsed:.1f} 秒）")
        else:
            QMessageBox.critical(self, "分析失败", str(result))
            self.status_label.setText("分析失败")

    def _build_table_text(self) -> str:
        if not self._table_data or not self._header_row or not self._header_col:
            return ""
        rows = []
        header = "巡目范围\t" + "\t".join(self._header_col)
        rows.append(header)
        for tr_idx, tr_label in enumerate(self._header_row):
            cells = [tr_label]
            for pat_idx in range(len(self._header_col)):
                v = self._table_data.get((tr_idx, pat_idx), "")
                cells.append(str(v))
            rows.append("\t".join(cells))
        return "\n".join(rows)

    def _copy_result(self):
        text = self._build_table_text()
        if text:
            QApplication.clipboard().setText(text)
            QMessageBox.information(self, "已复制", "表格已复制到剪贴板，可粘贴到 Excel 中绘制折线图。")

    def _save_result(self):
        text = self._build_table_text()
        if not text:
            return
        from PyQt5.QtWidgets import QFileDialog
        from PyQt5.QtCore import QStandardPaths
        start_dir = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home())
        path, _ = QFileDialog.getSaveFileName(
            self, "保存折线图数据", str(Path(start_dir) / "chart_data.tsv"),
            "Tab 分隔 (*.tsv);;所有文件 (*)",
            options=QFileDialog.DontUseNativeDialog,
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                QMessageBox.information(self, "保存成功", f"已保存至 {path}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))

    def _cancel_batch(self):
        if self.batch_thread:
            self.batch_thread.cancel()


class DbStatusThread(QThread):
    """后台获取数据库状态，避免启动时阻塞主线程"""
    finished = pyqtSignal(dict)

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    def run(self):
        try:
            stats = get_database_stats(self.db_path)
            self.finished.emit(stats)
        except Exception as e:
            self.finished.emit({"error": str(e)})


def _stats_cache_path(db_path: str) -> Path:
    """数据库统计缓存文件路径"""
    p = Path(db_path)
    return p.parent / (p.name + ".stats_cache")


def _format_db_status_text(stats: dict) -> str:
    """将 stats 格式化为横向展示的状态文本"""
    return (
        f"数据库状态: 已初始化  |  对局总数: {_fmt_int(stats['total_logs'])}  |  "
        f"数据库大小: {stats['db_size_mb']:.2f} MB  |  分析模式: 实时解析（按需分析）"
    )


def _load_db_status_from_cache(db_path: str) -> Optional[dict]:
    """从缓存读取数据库统计，数据库未更新时可直接使用"""
    p = _stats_cache_path(db_path)
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "total_logs" in data and "db_size_mb" in data:
            return data
    except Exception:
        pass
    return None


def _save_db_status_to_cache(db_path: str, stats: dict) -> None:
    """将数据库统计写入缓存"""
    if "error" in stats:
        return
    try:
        p = _stats_cache_path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False)
    except Exception:
        pass


class MainWindow(QMainWindow):
    """主窗口"""
    
    def __init__(self):
        super().__init__()
        
        self.db_path = "data/tenhou.db"
        self.downloader = DataDownloader(self.db_path)
        self.analyzer = LiveAnalyzer(self.db_path)
        self.query_thread = None
        self.sample_thread = None
        self.last_query_params = None  # 上次查询参数，用于生成样本
        self.last_query_result = None  # 上次查询结果（含 sample_pool），用于快速采样
        self._forced_analysis_target = None  # 由铳率分析页强制指定 analysis_target
        self._excel_clipboard_text = ""  # 当前结果的 Excel 格式文本
        self._pattern_checkboxes = []  # 多模式勾选框列表
        self._archive_entries: List[Dict[str, Any]] = []  # 存档条目列表
        self._archive_folders: List[Dict[str, Any]] = []  # 文件夹列表 [{id, name}, ...]

        self.init_ui()
        self._archive_entries, self._archive_folders = _load_archive(self.db_path)
        self._populate_folder_combo()
        self._refresh_archive_list()
        # 优先使用缓存的统计值，数据库未更新时避免重复执行耗时的 COUNT(*)
        cached = _load_db_status_from_cache(self.db_path)
        if cached:
            self.db_status_label.setText(_format_db_status_text(cached))
        elif Path(self.db_path).exists():
            self.db_status_label.setText("数据库状态: 加载中...")
            QTimer.singleShot(0, self._start_db_status_load)
        else:
            self.db_status_label.setText("数据库状态: 未初始化")
    
    def init_ui(self):
        """初始化界面"""
        self.setWindowTitle("立直麻将数据统计")
        self.setGeometry(100, 100, 800, 700)
        
        # 主窗口部件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # 主布局
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)
        
        # 1. 数据管理区（顶部固定）
        data_group = self._create_data_management_group()
        
        # 2. 查询输入区 + 3. 结果显示区 -> 放入标签页
        query_group = self._create_query_input_group()
        result_group = self._create_result_display_group()
        instant_group = self._create_instant_deal_in_group()
        
        self.main_tab = QTabWidget()
        # 使用独立 TabBar 便于放到数据管理区标题旁
        tab_bar = QTabBar()
        self.main_tab.setTabBar(tab_bar)
        self.main_tab.addTab(query_group, "一般分析")
        self.main_tab.addTab(instant_group, "铳率分析")
        self.main_tab.addTab(result_group, "查询结果")
        illustration_group = QWidget()
        ill_layout = QVBoxLayout(illustration_group)
        ill_layout.addWidget(TileIllustrationWidget(self))
        self.main_tab.addTab(illustration_group, "麻将示意图")
        
        # 将标签页按钮放到数据管理区第一行（紧邻标题/状态）
        status_row = data_group.layout().itemAt(0).layout()
        if status_row:
            status_row.addWidget(tab_bar, 0)
        
        main_layout.addWidget(data_group)
        main_layout.addWidget(self.main_tab, 1)  # 标签页内容区域占据剩余空间
    
    def _create_data_management_group(self) -> QGroupBox:
        """创建数据管理区"""
        group = QGroupBox("数据管理")
        layout = QVBoxLayout()
        
        # 数据库状态（横向排列，清晰展示）
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        self.db_status_label = QLabel("数据库状态: 未初始化")
        status_row.addWidget(self.db_status_label)
        status_row.addStretch()
        layout.addLayout(status_row)
        
        # 分析批次大小（根据内存选择，默认使用上次设置）
        batch_row = QHBoxLayout()
        batch_row.addWidget(QLabel("分析批次大小:"))
        self.analysis_batch_size_spin = QSpinBox()
        self.analysis_batch_size_spin.setRange(100, 5000)
        self.analysis_batch_size_spin.setSingleStep(100)
        saved_batch = QSettings().value("analysis_batch_size", 400, type=int)
        self.analysis_batch_size_spin.blockSignals(True)
        self.analysis_batch_size_spin.setValue(max(100, min(5000, saved_batch or 400)))
        self.analysis_batch_size_spin.blockSignals(False)
        self.analysis_batch_size_spin.setToolTip(
            "每批从数据库读取的对局数。请根据本机内存选择：\n"
            "约 1.2GB/1000 场，如 1000 场≈1.2GB，5000 场≈6GB。\n"
            "内存充足可设大一些以减少 SQL 次数、略快；内存紧张请设小一些。"
        )
        self.analysis_batch_size_spin.valueChanged.connect(self._save_analysis_batch_size)
        batch_row.addWidget(self.analysis_batch_size_spin)
        batch_row.addWidget(QLabel("场/批"))
        batch_row.addWidget(QLabel("并行进程:"))
        self.max_workers_spin = QSpinBox()
        self.max_workers_spin.setRange(1, 16)
        saved_workers = QSettings().value("max_workers", 1, type=int)
        self.max_workers_spin.blockSignals(True)
        self.max_workers_spin.setValue(max(1, min(16, saved_workers or 1)))
        self.max_workers_spin.blockSignals(False)
        self.max_workers_spin.setToolTip(
            "per-log 并行分析的进程数。>1 时利用多核加速，1 为串行。\n"
            "建议设为 CPU 核数-1，保留一核给界面。"
        )
        self.max_workers_spin.valueChanged.connect(self._save_max_workers)
        batch_row.addWidget(self.max_workers_spin)
        batch_row.addWidget(QLabel("内存维护:"))
        self.gc_interval_batches_spin = QSpinBox()
        self.gc_interval_batches_spin.setRange(1, 20)
        saved_gc = QSettings().value("gc_interval_batches", 4, type=int) or 4
        self.gc_interval_batches_spin.blockSignals(True)
        self.gc_interval_batches_spin.setValue(max(1, min(20, saved_gc)))
        self.gc_interval_batches_spin.blockSignals(False)
        self.gc_interval_batches_spin.setToolTip("每 N 批执行 gc + DB 重连，释放 tenhou.db 缓存。可自行调整找到最合适数值。")
        self.gc_interval_batches_spin.valueChanged.connect(self._save_gc_interval_batches)
        self.gc_interval_batches_spin.setMinimumWidth(52)
        batch_row.addWidget(self.gc_interval_batches_spin)
        batch_row.addWidget(QLabel("批"))
        batch_row.addStretch()
        layout.addLayout(batch_row)
        
        # 按钮
        button_layout = QHBoxLayout()
        
        self.download_btn = QPushButton("下载历史数据")
        self.download_btn.clicked.connect(self.download_data)
        button_layout.addWidget(self.download_btn)
        
        self.update_btn = QPushButton("更新最新数据")
        self.update_btn.clicked.connect(self.update_data)
        button_layout.addWidget(self.update_btn)
        
        layout.addLayout(button_layout)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        group.setLayout(layout)
        return group
    
    def _create_query_input_group(self) -> QGroupBox:
        """创建查询条件：左侧舍牌模式（主展示），右侧分析选项与约束（横向紧凑）"""
        group = QGroupBox("一般分析")
        main_row = QHBoxLayout()
        main_row.setSpacing(16)
        
        # ========== 左列：舍牌模式（占主要空间） ==========
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        ph_row = QHBoxLayout()
        ph_row.addWidget(QLabel("舍牌模式 (可添加多条，满足任一即计入):"))
        pattern_help_btn = QPushButton("?")
        pattern_help_btn.setToolTip("舍牌模式输入说明")
        pattern_help_btn.setFixedWidth(28)
        pattern_help_btn.clicked.connect(self._show_pattern_help)
        ph_row.addWidget(pattern_help_btn)
        ph_row.addStretch()
        left_layout.addLayout(ph_row)
        self.pattern_rows_container = QWidget()
        self.pattern_rows_layout = QVBoxLayout(self.pattern_rows_container)
        self.pattern_rows_layout.setSpacing(6)
        self.pattern_rows_layout.setContentsMargins(0, 0, 0, 0)
        pattern_scroll = QScrollArea()
        pattern_scroll.setWidget(self.pattern_rows_container)
        pattern_scroll.setWidgetResizable(True)
        pattern_scroll.setMinimumHeight(80)
        pattern_scroll.setFrameShape(QFrame.NoFrame)
        pattern_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_layout.addWidget(pattern_scroll, 1)
        add_btn_row = QHBoxLayout()
        self.add_pattern_btn = QPushButton("+ 添加舍牌模式")
        self.add_pattern_btn.clicked.connect(self._add_pattern_row)
        add_btn_row.addWidget(self.add_pattern_btn)
        add_btn_row.addStretch()
        left_layout.addLayout(add_btn_row)
        self._pattern_row_widgets = []
        self._add_pattern_row()
        main_row.addWidget(left_widget, 1)
        
        # ========== 右列：开始分析相关（巡目、约束、样本、执行） ==========
        right_widget = QWidget()
        right_widget.setMinimumWidth(400)
        right_widget.setMaximumWidth(620)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        # 巡目范围
        turn_layout = QHBoxLayout()
        turn_layout.addWidget(QLabel("巡目:"))
        self.turn_range_slider = RangeSlider(min_val=1, max_val=18, default_min=1, default_max=6)
        self.turn_range_label = QLabel("1-6 巡")
        self.turn_range_label.setMinimumWidth(45)
        self.turn_range_label.setStyleSheet("font-size: 12px;")
        self.turn_range_slider.valueChanged.connect(
            lambda lo, hi: self.turn_range_label.setText(f"{lo}-{hi} 巡")
        )
        turn_layout.addWidget(self.turn_range_slider, 1)
        turn_layout.addWidget(self.turn_range_label, 0)
        right_layout.addLayout(turn_layout)

        # 巡目范围（并排输入框，矩阵分析可添加多组）
        turn_ranges_header = QHBoxLayout()
        turn_ranges_header.addWidget(QLabel("巡目范围（矩阵分析可添加多组）:"))
        turn_ranges_help_btn = QPushButton("?")
        turn_ranges_help_btn.setToolTip("巡目范围使用说明")
        turn_ranges_help_btn.setFixedWidth(28)
        turn_ranges_help_btn.clicked.connect(self._show_turn_range_help)
        turn_ranges_header.addWidget(turn_ranges_help_btn)
        turn_ranges_header.addStretch()
        right_layout.addLayout(turn_ranges_header)
        turn_ranges_row = QHBoxLayout()
        turn_ranges_row.setSpacing(6)
        self._turn_range_edits: List[QLineEdit] = []
        for _ in range(5):
            self._add_turn_range_pair(turn_ranges_row, None)
        add_tr_btn = QPushButton("+")
        add_tr_btn.setFixedWidth(28)
        add_tr_btn.setToolTip("添加一组巡目范围")
        add_tr_btn.clicked.connect(lambda: self._add_turn_range_pair(turn_ranges_row, add_tr_btn))
        turn_ranges_row.addWidget(add_tr_btn)
        turn_ranges_row.addStretch()
        right_layout.addLayout(turn_ranges_row)
        
        # 宝牌 / 立直 / 副露 各占一行，避免文字重叠
        dora_row = QHBoxLayout()
        dora_row.setSpacing(10)
        self.dora_group = QButtonGroup()
        self.dora_irrelevant_radio = QRadioButton("宝牌无关")
        self.dora_irrelevant_radio.setChecked(True)
        self.dora_group.addButton(self.dora_irrelevant_radio, 0)
        self.dora_specific_radio = QRadioButton("宝牌为")
        self.dora_group.addButton(self.dora_specific_radio, 1)
        self.dora_matches_position_radio = QRadioButton("宝牌=模式第")
        self.dora_group.addButton(self.dora_matches_position_radio, 2)
        self.dora_tile_input = QLineEdit()
        self.dora_tile_input.setPlaceholderText("例: 6s")
        self.dora_tile_input.setMinimumWidth(50)
        self.dora_tile_input.setMaximumWidth(70)
        self.dora_position_input = QLineEdit()
        self.dora_position_input.setPlaceholderText("例: 1 或 1,3")
        self.dora_position_input.setMinimumWidth(50)
        self.dora_position_input.setMaximumWidth(70)
        self.dora_position_input.setToolTip("1-based，如 1 表示第1张，1,3 表示第1、3张必须为宝牌")
        dora_row.addWidget(self.dora_irrelevant_radio)
        dora_row.addWidget(self.dora_specific_radio)
        dora_row.addWidget(self.dora_tile_input)
        dora_row.addWidget(self.dora_matches_position_radio)
        dora_row.addWidget(self.dora_position_input)
        dora_row.addWidget(QLabel("张"))
        dora_row.addStretch()
        right_layout.addLayout(dora_row)
        riichi_row = QHBoxLayout()
        riichi_row.setSpacing(10)
        self.riichi_group = QButtonGroup()
        self.riichi_any_radio = QRadioButton("立直: 任意")
        self.riichi_any_radio.setChecked(True)
        self.riichi_group.addButton(self.riichi_any_radio, 0)
        self.riichi_has_radio = QRadioButton("有人立直")
        self.riichi_group.addButton(self.riichi_has_radio, 1)
        self.riichi_no_radio = QRadioButton("无人立直")
        self.riichi_group.addButton(self.riichi_no_radio, 2)
        riichi_row.addWidget(self.riichi_any_radio)
        riichi_row.addWidget(self.riichi_has_radio)
        riichi_row.addWidget(self.riichi_no_radio)
        riichi_row.addStretch()
        right_layout.addLayout(riichi_row)
        call_row = QHBoxLayout()
        call_row.setSpacing(10)
        self.call_group = QButtonGroup()
        self.call_any_radio = QRadioButton("副露: 任意")
        self.call_any_radio.setChecked(True)
        self.call_group.addButton(self.call_any_radio, 0)
        self.call_has_radio = QRadioButton("有人副露")
        self.call_group.addButton(self.call_has_radio, 1)
        self.call_no_radio = QRadioButton("无人副露")
        self.call_group.addButton(self.call_no_radio, 2)
        call_row.addWidget(self.call_any_radio)
        call_row.addWidget(self.call_has_radio)
        call_row.addWidget(self.call_no_radio)
        call_row.addStretch()
        right_layout.addLayout(call_row)
        exclude_south_row = QHBoxLayout()
        self.exclude_south4_check = QCheckBox("禁止南四局")
        self.exclude_south4_check.setToolTip("南四局打法会根据点数状况有极大改变，勾选时跳过南四局")
        exclude_south_row.addWidget(self.exclude_south4_check)
        self.exclude_south3_check = QCheckBox("禁止南三局")
        self.exclude_south3_check.setToolTip("勾选时跳过南三局，可与南四局同时勾选")
        exclude_south_row.addWidget(self.exclude_south3_check)
        exclude_south_row.addStretch()
        right_layout.addLayout(exclude_south_row)
        prior_excl_row = QHBoxLayout()
        prior_excl_row.addWidget(QLabel("前段禁打:"))
        self.prior_discard_exclusion_input = QLineEdit()
        self.prior_discard_exclusion_input.setPlaceholderText("例: NOTm 或 4mOR2m")
        self.prior_discard_exclusion_input.setToolTip(
            "巡目范围开始前，该玩家不能打出这些牌。支持 NOTm/p/s、[25]m、4mOR2m 等，与舍牌模式同步等价变换"
        )
        self.prior_discard_exclusion_input.setMinimumWidth(140)
        self.prior_discard_exclusion_input.setMaximumWidth(200)
        prior_excl_row.addWidget(self.prior_discard_exclusion_input)
        prior_excl_row.addStretch()
        right_layout.addLayout(prior_excl_row)

        # 副露区域约束（目标玩家必须有这些副露，AND 关系，最多 4 个）
        call_area_row = QHBoxLayout()
        call_area_row.addWidget(QLabel("副露区域:"))
        self.call_area_inputs = []
        for i in range(4):
            le = QLineEdit()
            le.setPlaceholderText("例: pzfzf 4mc3m5m")
            le.setMinimumWidth(90)
            le.setMaximumWidth(120)
            self.call_area_inputs.append(le)
            call_area_row.addWidget(le)
        call_area_help = QPushButton("?")
        call_area_help.setFixedWidth(24)
        call_area_help.setToolTip("pzfzf=碰自风, pypyp=碰役牌, 4mc3m5m=吃4m用3m5m")
        call_area_help.clicked.connect(self._show_call_area_help)
        call_area_row.addWidget(call_area_help)
        call_area_row.addStretch()
        right_layout.addLayout(call_area_row)
        
        # 场上可见枚数（可添加多行，每行可并排两个条目）
        right_layout.addSpacing(4)
        vc_header = QHBoxLayout()
        vc_header.addWidget(QLabel("场上可见枚数"))
        self.add_visible_constraint_btn = QPushButton("+ 添加")
        self.add_visible_constraint_btn.setFixedWidth(72)
        self.add_visible_constraint_btn.clicked.connect(self._add_visible_constraint_row)
        vc_header.addWidget(self.add_visible_constraint_btn)
        vc_header.addStretch()
        right_layout.addLayout(vc_header)
        self.visible_constraint_scroll = QScrollArea()
        self.visible_constraint_scroll.setWidgetResizable(True)
        self.visible_constraint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.visible_constraint_scroll.setMinimumHeight(52)
        self.visible_constraint_scroll.setMaximumHeight(100)
        self.visible_constraint_rows_widget = QWidget()
        self.visible_constraint_rows_widget.setMinimumWidth(596)
        self.visible_constraint_rows_layout = QGridLayout(self.visible_constraint_rows_widget)
        self.visible_constraint_rows_layout.setContentsMargins(0, 0, 4, 0)
        self.visible_constraint_rows_layout.setSpacing(2)
        self.visible_constraint_rows_layout.setHorizontalSpacing(8)
        self.visible_constraint_rows_layout.setVerticalSpacing(4)
        self.visible_constraint_rows_layout.setColumnStretch(0, 0)
        self.visible_constraint_rows_layout.setColumnStretch(1, 0)
        self.visible_constraint_scroll.setWidget(self.visible_constraint_rows_widget)
        self.visible_constraint_row_refs = []
        right_layout.addWidget(self.visible_constraint_scroll)
        self._add_visible_constraint_row()
        
        # 分析目标 + 样本上限 + 匹配状态保留条数
        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("分析目标:"))
        self.analysis_target_combo = QComboBox()
        self.analysis_target_combo.addItem("目标牌存量", "target_count")
        self.analysis_target_combo.addItem("是否听牌", "tenpai")
        self.analysis_target_combo.addItem("和铳率", "outcome")
        self.analysis_target_combo.setMinimumWidth(100)
        self.analysis_target_combo.setToolTip(
            "目标牌存量：统计手牌中目标牌数量；"
            "是否听牌：统计匹配时已听牌/未听牌比例；"
            "和铳率：统计达成模式后该局和了率与放铳率（无需输入目标牌）"
        )
        self.analysis_target_combo.currentIndexChanged.connect(self._on_analysis_target_changed)
        opts_row.addWidget(self.analysis_target_combo)
        # 初始化时同步目标牌区域显隐（和铳率模式下隐藏）
        QTimer.singleShot(0, self._on_analysis_target_changed)
        opts_row.addWidget(QLabel("样本上限:"))
        self.sample_limit_input = QSpinBox()
        self.sample_limit_input.setRange(100, 10000000)
        self.sample_limit_input.setSingleStep(10000)
        self.sample_limit_input.setSuffix(" 半庄")
        saved_limit = QSettings().value("sample_limit", 10000, type=int) or 10000
        self.sample_limit_input.blockSignals(True)
        self.sample_limit_input.setValue(max(100, min(10000000, saved_limit)))
        self.sample_limit_input.blockSignals(False)
        self.sample_limit_input.valueChanged.connect(self._save_sample_limit)
        self.sample_limit_input.setMinimumWidth(100)
        opts_row.addWidget(self.sample_limit_input)
        opts_row.addWidget(QLabel("匹配保留:"))
        self.matched_states_cap_spin = QSpinBox()
        self.matched_states_cap_spin.setRange(1, 500)
        saved_cap = QSettings().value("matched_states_cap", 200, type=int) or 200
        self.matched_states_cap_spin.blockSignals(True)
        self.matched_states_cap_spin.setValue(max(1, min(500, saved_cap)))
        self.matched_states_cap_spin.blockSignals(False)
        self.matched_states_cap_spin.setToolTip("分析时最多保留的匹配状态条数（用于展示，越大占内存越多）")
        self.matched_states_cap_spin.valueChanged.connect(self._save_matched_states_cap)
        self.matched_states_cap_spin.setMinimumWidth(56)
        opts_row.addWidget(self.matched_states_cap_spin)
        opts_row.addStretch()
        right_layout.addLayout(opts_row)
        
        # 执行查询
        self.query_btn = QPushButton("开始分析")
        self.query_btn.setObjectName("query_btn")
        self.query_btn.clicked.connect(self.execute_query)
        right_layout.addWidget(self.query_btn)
        
        main_row.addWidget(right_widget, 0)
        group.setLayout(main_row)
        return group

    def _add_turn_range_pair(self, layout: QHBoxLayout, add_btn: Optional[QPushButton] = None):
        """添加一个巡目范围输入框，支持 1-12 格式，默认留空时用上方滑块"""
        edit = QLineEdit()
        edit.setPlaceholderText("1-12")
        edit.setFixedWidth(56)
        edit.setMaxLength(6)
        edit.setToolTip("输入 最小-最大，如 1-6、4-9")
        if add_btn is not None:
            idx = layout.indexOf(add_btn)
            layout.insertWidget(idx, edit)
        else:
            layout.addWidget(edit)
        self._turn_range_edits.append(edit)

    def _get_turn_ranges_from_ui(self) -> List[Tuple[int, int]]:
        """从巡目范围输入框读取有效范围列表（去重保留顺序）。留空则跳过，全部留空时由滑块决定"""
        seen = set()
        turn_ranges = []
        for edit in self._turn_range_edits:
            line = edit.text().strip()
            if not line:
                continue
            tr = _parse_turn_range(line)
            if tr and tr not in seen:
                seen.add(tr)
                turn_ranges.append(tr)
        return turn_ranges

    def _add_pattern_row(self):
        """添加一行舍牌模式+目标牌"""
        row = QHBoxLayout()
        row.setSpacing(8)
        pattern_edit = QLineEdit()
        pattern_edit.setPlaceholderText("例: 7s-9s、c0p6p-$、cd1-3m")
        pattern_edit.setMinimumWidth(150)
        target_edit = QLineEdit()
        target_edit.setPlaceholderText("例: 6s 或 6s 2m 5p")
        target_edit.setMaximumWidth(80)
        arrow_label = QLabel("→")
        target_help_btn = QPushButton("?")
        target_help_btn.setToolTip("目标牌说明")
        target_help_btn.setFixedWidth(24)
        target_help_btn.clicked.connect(self._show_target_help)
        del_btn = QPushButton("×")
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("删除此行")
        row.addWidget(QLabel("模式:"))
        row.addWidget(pattern_edit, 1)
        row.addWidget(arrow_label)
        row.addWidget(target_edit)
        row.addWidget(target_help_btn)
        row.addWidget(del_btn)
        entry = (pattern_edit, target_edit, arrow_label, target_help_btn, del_btn, row)
        self._pattern_row_widgets.append(entry)
        del_btn.clicked.connect(lambda checked=False, e=entry: self._remove_pattern_row(e))
        self.pattern_rows_layout.addLayout(row)

    def _remove_pattern_row(self, entry):
        """移除一行舍牌模式"""
        if len(self._pattern_row_widgets) <= 1:
            QMessageBox.warning(self, "提示", "至少需保留一个舍牌模式")
            return
        pattern_edit, target_edit, arrow_label, target_help_btn, del_btn, row = entry
        while row.count():
            item = row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pattern_rows_layout.removeItem(row)
        if entry in self._pattern_row_widgets:
            self._pattern_row_widgets.remove(entry)

    def _on_analysis_target_changed(self):
        """和铳率模式下隐藏目标牌输入（无需目标牌）"""
        if not hasattr(self, "analysis_target_combo"):
            return
        is_outcome = (self.analysis_target_combo.currentData() or "") == "outcome"
        for pattern_edit, target_edit, arrow_label, target_help_btn, del_btn, row in getattr(
            self, "_pattern_row_widgets", []
        ):
            arrow_label.setVisible(not is_outcome)
            target_edit.setVisible(not is_outcome)
            target_help_btn.setVisible(not is_outcome)
            if is_outcome:
                target_edit.clear()
                target_edit.setPlaceholderText("（和铳率无需）")
            else:
                target_edit.setPlaceholderText("例: 6s 或 6s 2m 5p")

    def _parse_dora_position_spec(self) -> List[int]:
        """解析「宝牌=模式第 N 张」的位置输入，返回 0-based 下标列表。如 1,3 -> [0, 2]"""
        widget = getattr(self, "dora_position_input", None)
        if widget is None:
            return []
        text = (widget.text() if hasattr(widget, "text") else "").strip()
        if not text:
            return []
        try:
            return [int(x.strip()) - 1 for x in text.replace("，", ",").split(",") if x.strip()]
        except ValueError:
            return []

    def _get_pattern_items(self, require_target: bool = True) -> List[Tuple[List[str], str]]:
        """从界面获取所有 (pattern, target) 对。require_target=False 时（听牌/和铳率模式）目标可为空，以 5z 占位"""
        items = []
        placeholder = "5z"  # 听牌/和铳率模式无目标牌时占位
        for pattern_edit, target_edit, _, _, _, _ in self._pattern_row_widgets:
            pt = pattern_edit.text().strip()
            tg = target_edit.text().strip()
            if pt and (tg or not require_target):
                items.append((split_discard_pattern(pt), tg or placeholder))
        return items

    def _show_pattern_help(self):
        """显示舍牌模式输入说明"""
        PatternHelpDialog(self).exec_()

    def _show_turn_range_help(self):
        """显示巡目范围使用说明"""
        QMessageBox.information(
            self,
            "巡目范围说明",
            "• 下方输入框全部留空时：使用上方滑块的巡目范围。\n\n"
            "• 下方输入框有填入内容时：仅使用输入框中的范围，滑块会被忽略。\n\n"
            "• 若希望矩阵分析包含滑块选中的范围，需在输入框中手动添加一组对应的最小/最大巡目。\n\n"
            "• 矩阵分析：填入多组范围（如 1-3、4-6、7-9）时，将进行 N 模式 × M 巡目的矩阵分析。"
        )

    def _show_target_help(self):
        """显示目标牌输入说明"""
        TargetHelpDialog(self).exec_()

    def _show_call_area_help(self):
        """显示副露区域约束说明"""
        QMessageBox.information(
            self,
            "副露区域约束说明",
            "格式与舍牌模式相同。目标玩家必须满足所有填写的副露（AND）。\n\n"
            "• pzfzf：碰出自风\n"
            "• pypyp：碰出任一役牌（自风/场风/三元牌）\n"
            "• pkfkf：碰出客风\n"
            "• 4mc3m5m：用3m5m吃过4m\n"
            "• p1z1z：碰出东\n\n"
            "最多 4 个约束，留空表示无此约束。"
        )

    def _show_matrix_display_dialog(self):
        """打开展示矩阵窗口"""
        if not self.last_query_result or self.last_query_result.get("table_dist") is None:
            QMessageBox.information(self, "提示", "请先完成矩阵分析（巡目范围列表输入多行）")
            return
        MatrixDisplayDialog(self, self.last_query_result).exec_()

    def _create_instant_deal_in_group(self) -> QWidget:
        """独立即时铳率入口页（完整约束，执行时复用主查询链路）。"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        hint = QLabel(
            "该页用于“即时铳率/铳点/铳度”分析。\n"
            "约束能力与主分析页一致；点击开始后会自动同步到主分析执行链路。"
        )
        hint.setStyleSheet("color: #8b949e;")
        layout.addWidget(hint)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("舍牌模式:"))
        self.instant_pattern_input = QLineEdit()
        self.instant_pattern_input.setPlaceholderText("例: 7s-9s、c0p6p-$、cd1-3m")
        row1.addWidget(self.instant_pattern_input, 1)
        row1.addWidget(QLabel("目标牌:"))
        self.instant_target_input = QLineEdit()
        self.instant_target_input.setPlaceholderText("例: 6s（仅单张）")
        self.instant_target_input.setMaximumWidth(80)
        row1.addWidget(self.instant_target_input)
        layout.addLayout(row1)

        # 约束区
        constraints_group = QGroupBox("约束条件")
        c_layout = QVBoxLayout()

        dora_row = QHBoxLayout()
        dora_row.setSpacing(8)
        self.instant_dora_group = QButtonGroup(self)
        self.instant_dora_irrelevant_radio = QRadioButton("宝牌无关")
        self.instant_dora_irrelevant_radio.setChecked(True)
        self.instant_dora_group.addButton(self.instant_dora_irrelevant_radio, 0)
        self.instant_dora_specific_radio = QRadioButton("宝牌为")
        self.instant_dora_group.addButton(self.instant_dora_specific_radio, 1)
        self.instant_dora_matches_position_radio = QRadioButton("宝牌=模式第")
        self.instant_dora_group.addButton(self.instant_dora_matches_position_radio, 2)
        self.instant_dora_tile_input = QLineEdit()
        self.instant_dora_tile_input.setPlaceholderText("例: 6s")
        self.instant_dora_tile_input.setMinimumWidth(50)
        self.instant_dora_tile_input.setMaximumWidth(70)
        self.instant_dora_position_input = QLineEdit()
        self.instant_dora_position_input.setPlaceholderText("例: 1 或 1,3")
        self.instant_dora_position_input.setMinimumWidth(50)
        self.instant_dora_position_input.setMaximumWidth(70)
        self.instant_dora_position_input.setToolTip("1-based，如 1 表示第1张，1,3 表示第1、3张必须为宝牌")
        dora_row.addWidget(self.instant_dora_irrelevant_radio)
        dora_row.addWidget(self.instant_dora_specific_radio)
        dora_row.addWidget(self.instant_dora_tile_input)
        dora_row.addWidget(self.instant_dora_matches_position_radio)
        dora_row.addWidget(self.instant_dora_position_input)
        dora_row.addWidget(QLabel("张"))
        dora_row.addStretch()
        c_layout.addLayout(dora_row)

        riichi_row = QHBoxLayout()
        riichi_row.setSpacing(8)
        self.instant_riichi_group = QButtonGroup(self)
        self.instant_riichi_any_radio = QRadioButton("立直: 任意")
        self.instant_riichi_any_radio.setChecked(True)
        self.instant_riichi_group.addButton(self.instant_riichi_any_radio, 0)
        self.instant_riichi_has_radio = QRadioButton("有人立直")
        self.instant_riichi_group.addButton(self.instant_riichi_has_radio, 1)
        self.instant_riichi_no_radio = QRadioButton("无人立直")
        self.instant_riichi_group.addButton(self.instant_riichi_no_radio, 2)
        riichi_row.addWidget(self.instant_riichi_any_radio)
        riichi_row.addWidget(self.instant_riichi_has_radio)
        riichi_row.addWidget(self.instant_riichi_no_radio)
        riichi_row.addStretch()
        c_layout.addLayout(riichi_row)

        call_row = QHBoxLayout()
        call_row.setSpacing(8)
        self.instant_call_group = QButtonGroup(self)
        self.instant_call_any_radio = QRadioButton("副露: 任意")
        self.instant_call_any_radio.setChecked(True)
        self.instant_call_group.addButton(self.instant_call_any_radio, 0)
        self.instant_call_has_radio = QRadioButton("有人副露")
        self.instant_call_group.addButton(self.instant_call_has_radio, 1)
        self.instant_call_no_radio = QRadioButton("无人副露")
        self.instant_call_group.addButton(self.instant_call_no_radio, 2)
        call_row.addWidget(self.instant_call_any_radio)
        call_row.addWidget(self.instant_call_has_radio)
        call_row.addWidget(self.instant_call_no_radio)
        call_row.addStretch()
        c_layout.addLayout(call_row)

        exclude_south_row = QHBoxLayout()
        self.instant_exclude_south4_check = QCheckBox("禁止南四局")
        self.instant_exclude_south4_check.setToolTip("南四局打法会根据点数状况有极大改变，勾选时跳过南四局")
        exclude_south_row.addWidget(self.instant_exclude_south4_check)
        self.instant_exclude_south3_check = QCheckBox("禁止南三局")
        self.instant_exclude_south3_check.setToolTip("勾选时跳过南三局，可与南四局同时勾选")
        exclude_south_row.addWidget(self.instant_exclude_south3_check)
        exclude_south_row.addStretch()
        c_layout.addLayout(exclude_south_row)

        prior_excl_row = QHBoxLayout()
        prior_excl_row.addWidget(QLabel("前段禁打:"))
        self.instant_prior_discard_exclusion_input = QLineEdit()
        self.instant_prior_discard_exclusion_input.setPlaceholderText("例: NOTm 或 4mOR2m")
        self.instant_prior_discard_exclusion_input.setToolTip(
            "巡目范围开始前，该玩家不能打出这些牌。支持 NOTm/p/s、[25]m、4mOR2m 等，与舍牌模式同步等价变换"
        )
        self.instant_prior_discard_exclusion_input.setMinimumWidth(140)
        self.instant_prior_discard_exclusion_input.setMaximumWidth(200)
        prior_excl_row.addWidget(self.instant_prior_discard_exclusion_input)
        prior_excl_row.addStretch()
        c_layout.addLayout(prior_excl_row)

        call_area_row = QHBoxLayout()
        call_area_row.addWidget(QLabel("副露区域:"))
        self.instant_call_area_inputs = []
        for _ in range(4):
            le = QLineEdit()
            le.setPlaceholderText("例: pzfzf 4mc3m5m")
            le.setMinimumWidth(90)
            le.setMaximumWidth(120)
            self.instant_call_area_inputs.append(le)
            call_area_row.addWidget(le)
        call_area_help = QPushButton("?")
        call_area_help.setFixedWidth(24)
        call_area_help.setToolTip("pzfzf=碰自风, pypyp=碰役牌, 4mc3m5m=吃4m用3m5m")
        call_area_help.clicked.connect(self._show_call_area_help)
        call_area_row.addWidget(call_area_help)
        call_area_row.addStretch()
        c_layout.addLayout(call_area_row)

        c_layout.addSpacing(4)
        vc_header = QHBoxLayout()
        vc_header.addWidget(QLabel("场上可见枚数"))
        self.instant_add_visible_constraint_btn = QPushButton("+ 添加")
        self.instant_add_visible_constraint_btn.setFixedWidth(72)
        self.instant_add_visible_constraint_btn.clicked.connect(self._instant_add_visible_constraint_row)
        vc_header.addWidget(self.instant_add_visible_constraint_btn)
        vc_header.addStretch()
        c_layout.addLayout(vc_header)

        self.instant_visible_constraint_scroll = QScrollArea()
        self.instant_visible_constraint_scroll.setWidgetResizable(True)
        self.instant_visible_constraint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.instant_visible_constraint_scroll.setMinimumHeight(52)
        self.instant_visible_constraint_scroll.setMaximumHeight(100)
        self.instant_visible_constraint_rows_widget = QWidget()
        self.instant_visible_constraint_rows_widget.setMinimumWidth(596)
        self.instant_visible_constraint_rows_layout = QGridLayout(self.instant_visible_constraint_rows_widget)
        self.instant_visible_constraint_rows_layout.setContentsMargins(0, 0, 4, 0)
        self.instant_visible_constraint_rows_layout.setSpacing(2)
        self.instant_visible_constraint_rows_layout.setHorizontalSpacing(8)
        self.instant_visible_constraint_rows_layout.setVerticalSpacing(4)
        self.instant_visible_constraint_rows_layout.setColumnStretch(0, 0)
        self.instant_visible_constraint_rows_layout.setColumnStretch(1, 0)
        self.instant_visible_constraint_scroll.setWidget(self.instant_visible_constraint_rows_widget)
        self.instant_visible_constraint_row_refs = []
        c_layout.addWidget(self.instant_visible_constraint_scroll)
        self._instant_add_visible_constraint_row()

        constraints_group.setLayout(c_layout)
        layout.addWidget(constraints_group)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("巡目范围:"))
        self.instant_turn_min = QSpinBox()
        self.instant_turn_min.setRange(1, 18)
        self.instant_turn_min.setValue(1)
        self.instant_turn_max = QSpinBox()
        self.instant_turn_max.setRange(1, 18)
        self.instant_turn_max.setValue(18)
        row2.addWidget(self.instant_turn_min)
        row2.addWidget(QLabel("~"))
        row2.addWidget(self.instant_turn_max)
        row2.addSpacing(12)
        row2.addWidget(QLabel("样本上限:"))
        self.instant_sample_limit_input = QSpinBox()
        self.instant_sample_limit_input.setRange(100, 10000000)
        self.instant_sample_limit_input.setSingleStep(10000)
        self.instant_sample_limit_input.setSuffix(" 半庄")
        saved_limit = QSettings().value("sample_limit", 10000, type=int) or 10000
        self.instant_sample_limit_input.setValue(max(100, min(10000000, saved_limit)))
        self.instant_sample_limit_input.setMinimumWidth(100)
        row2.addWidget(self.instant_sample_limit_input)
        row2.addSpacing(8)
        row2.addWidget(QLabel("匹配保留:"))
        self.instant_matched_states_cap_spin = QSpinBox()
        self.instant_matched_states_cap_spin.setRange(1, 500)
        saved_cap = QSettings().value("matched_states_cap", 200, type=int) or 200
        self.instant_matched_states_cap_spin.setValue(max(1, min(500, saved_cap)))
        self.instant_matched_states_cap_spin.setMinimumWidth(56)
        row2.addWidget(self.instant_matched_states_cap_spin)
        row2.addStretch()
        self.instant_start_btn = QPushButton("开始即时铳率分析")
        self.instant_start_btn.clicked.connect(self._start_instant_deal_in_analysis)
        row2.addWidget(self.instant_start_btn)
        layout.addLayout(row2)

        self.instant_status_label = QLabel("")
        layout.addWidget(self.instant_status_label)
        layout.addStretch()
        return widget

    def _instant_rebuild_visible_constraint_grid(self):
        """将即时铳率页的场上可见枚数条目按 2 列重新排列。"""
        layout = self.instant_visible_constraint_rows_layout
        while layout.count():
            item = layout.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)
        for i, (_, _, row_widget) in enumerate(self.instant_visible_constraint_row_refs):
            layout.addWidget(row_widget, i // 2, i % 2)

    def _instant_add_visible_constraint_row(self):
        """即时铳率页：添加一行场上可见枚数输入。"""
        row_widget = QWidget()
        row_widget.setFixedWidth(292)
        row_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 8, 2)
        row_layout.setSpacing(4)
        tile_edit = QLineEdit()
        tile_edit.setPlaceholderText("牌，如 8s")
        tile_edit.setFixedWidth(52)
        row_layout.addWidget(tile_edit)
        row_layout.addWidget(QLabel("可见"))
        range_slider = DiscreteRangeSlider()
        row_layout.addWidget(range_slider)
        row_layout.addWidget(QLabel("枚"))
        remove_btn = QPushButton("X")
        remove_btn.setFixedSize(32, 24)
        remove_btn.setToolTip("删除此行")
        row_layout.addWidget(remove_btn)
        self.instant_visible_constraint_row_refs.append((tile_edit, range_slider, row_widget))
        self._instant_rebuild_visible_constraint_grid()

        def do_remove():
            self.instant_visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self.instant_visible_constraint_row_refs.remove((tile_edit, range_slider, row_widget))
            self._instant_rebuild_visible_constraint_grid()

        remove_btn.clicked.connect(do_remove)

    def _instant_get_visible_constraints_from_ui(self) -> Dict[str, Tuple[int, int]]:
        """即时铳率页：读取场上可见枚数约束。"""
        visible_constraints = {}
        for tile_edit, range_slider, _ in self.instant_visible_constraint_row_refs:
            tile = tile_edit.text().strip()
            if not tile:
                continue
            min_count, max_count = range_slider.getRange()
            if min_count > max_count:
                continue
            visible_constraints[tile] = (min_count, max_count)
        return visible_constraints

    def _sync_instant_constraints_to_main(self):
        """将即时铳率页的约束与参数同步到主分析页控件。"""
        # 宝牌约束
        self.dora_irrelevant_radio.setChecked(self.instant_dora_irrelevant_radio.isChecked())
        self.dora_specific_radio.setChecked(self.instant_dora_specific_radio.isChecked())
        self.dora_matches_position_radio.setChecked(self.instant_dora_matches_position_radio.isChecked())
        self.dora_tile_input.setText(self.instant_dora_tile_input.text().strip())
        self.dora_position_input.setText(self.instant_dora_position_input.text().strip())

        # 立直/副露约束
        self.riichi_any_radio.setChecked(self.instant_riichi_any_radio.isChecked())
        self.riichi_has_radio.setChecked(self.instant_riichi_has_radio.isChecked())
        self.riichi_no_radio.setChecked(self.instant_riichi_no_radio.isChecked())
        self.call_any_radio.setChecked(self.instant_call_any_radio.isChecked())
        self.call_has_radio.setChecked(self.instant_call_has_radio.isChecked())
        self.call_no_radio.setChecked(self.instant_call_no_radio.isChecked())

        # 南三/南四
        self.exclude_south4_check.setChecked(self.instant_exclude_south4_check.isChecked())
        self.exclude_south3_check.setChecked(self.instant_exclude_south3_check.isChecked())

        # 前段禁打
        self.prior_discard_exclusion_input.setText(
            self.instant_prior_discard_exclusion_input.text().strip()
        )

        # 副露区域
        for i, le in enumerate(self.call_area_inputs):
            text = self.instant_call_area_inputs[i].text().strip() if i < len(self.instant_call_area_inputs) else ""
            le.setText(text)

        # 场上可见枚数（先清空主分析条目，再按即时页重建）
        instant_constraints = list(self._instant_get_visible_constraints_from_ui().items())
        for _, _, row_widget in list(self.visible_constraint_row_refs):
            self.visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self.visible_constraint_row_refs.clear()
        if not instant_constraints:
            self._add_visible_constraint_row()
        else:
            for tile, (min_count, max_count) in instant_constraints:
                self._add_visible_constraint_row()
                tile_edit, range_slider, _ = self.visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(min_count, max_count)

        # 样本上限、匹配保留
        self.sample_limit_input.setValue(self.instant_sample_limit_input.value())
        self.matched_states_cap_spin.setValue(self.instant_matched_states_cap_spin.value())

    def _start_instant_deal_in_analysis(self):
        pattern = self.instant_pattern_input.text().strip()
        target = self.instant_target_input.text().strip()
        tmin = self.instant_turn_min.value()
        tmax = self.instant_turn_max.value()

        if not pattern or not target:
            QMessageBox.warning(self, "输入错误", "请填写舍牌模式与目标牌（单张）")
            return
        if tmin > tmax:
            QMessageBox.warning(self, "输入错误", "巡目范围最小值不能大于最大值")
            return

        # 即时铳率仅支持单目标单张
        try:
            mt = parse_multi_targets(target)
            if len(mt) != 1 or mt[0][1] or len(mt[0][0]) != 1:
                QMessageBox.warning(self, "输入错误", "即时铳率仅支持单张目标牌（不支持 multi-target / combo）")
                return
        except Exception:
            QMessageBox.warning(self, "输入错误", "目标牌格式无效")
            return

        # 宝牌输入校验
        if self.instant_dora_specific_radio.isChecked() and not self.instant_dora_tile_input.text().strip():
            QMessageBox.warning(self, "输入错误", "请选择“宝牌为”时，请填写宝牌（例: 6s）")
            return
        if self.instant_dora_matches_position_radio.isChecked() and not self.instant_dora_position_input.text().strip():
            QMessageBox.warning(self, "输入错误", "请选择“宝牌=模式第”时，请填写位置（例: 1 或 1,3）")
            return

        # 先验证模式是否可解析，避免复制后才失败
        try:
            split_discard_pattern(pattern)
        except Exception:
            QMessageBox.warning(self, "输入错误", "舍牌模式格式无效")
            return

        # 复用主查询页控件，确保约束/采样/线程管理逻辑一致
        if not getattr(self, "_pattern_row_widgets", None):
            self._add_pattern_row()
        for idx, (pattern_edit, target_edit, _, _, _, _) in enumerate(self._pattern_row_widgets):
            if idx == 0:
                pattern_edit.setText(pattern)
                target_edit.setText(target)
            else:
                pattern_edit.clear()
                target_edit.clear()

        self._sync_instant_constraints_to_main()
        self._forced_analysis_target = "deal_in_instant"
        self.turn_range_slider.setRange(tmin, tmax)
        for edit in getattr(self, "_turn_range_edits", []):
            edit.clear()

        self.instant_status_label.setText("已提交分析，进度与结果请查看“查询结果”页。")
        self.main_tab.setCurrentIndex(2)
        self.execute_query()

    def _create_result_display_group(self) -> QGroupBox:
        """创建结果显示区"""
        group = QGroupBox("查询结果")
        main_layout = QVBoxLayout()

        splitter = QSplitter(Qt.Horizontal)

        # 左侧：结果展示区
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏：复制到 Excel、展示矩阵
        tool_row = QHBoxLayout()
        self.copy_excel_btn = QPushButton("复制到 Excel")
        self.copy_excel_btn.setToolTip("复制分析结果为 Tab 分隔格式，可直接粘贴到 Excel 中计算")
        self.copy_excel_btn.clicked.connect(self._copy_result_to_excel)
        self.copy_excel_btn.setEnabled(False)
        tool_row.addWidget(self.copy_excel_btn)
        self.matrix_display_btn = QPushButton("展示矩阵")
        self.matrix_display_btn.setToolTip("打开展示窗口，可勾选 0/1/2/3 张并复制 Excel 格式（仅矩阵分析结果可用）")
        self.matrix_display_btn.setEnabled(False)
        self.matrix_display_btn.clicked.connect(self._show_matrix_display_dialog)
        tool_row.addWidget(self.matrix_display_btn)
        scroll_bottom_btn = QPushButton("到底部")
        scroll_bottom_btn.setToolTip("一键滚动到结果最下方")
        scroll_bottom_btn.clicked.connect(self._scroll_result_to_bottom)
        tool_row.addWidget(scroll_bottom_btn)
        tool_row.addStretch()
        left_layout.addLayout(tool_row)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(200)
        left_layout.addWidget(self.result_text)

        # 多模式合并区（仅多舍牌模式时显示）
        self.multi_merge_widget = QWidget()
        self.multi_merge_layout = QVBoxLayout(self.multi_merge_widget)
        self.multi_merge_layout.setContentsMargins(0, 8, 0, 0)
        self.multi_merge_widget.setVisible(False)
        left_layout.addWidget(self.multi_merge_widget)

        # 样本生成区（查询成功后可用）
        sample_layout = QHBoxLayout()
        sample_layout.addWidget(QLabel("生成验证样本:"))
        self.sample_count_spin = QSpinBox()
        self.sample_count_spin.setRange(1, 500)
        sample_cap = QSettings().value("matched_states_cap", 100, type=int) or 100
        self.sample_count_spin.setValue(max(1, min(500, sample_cap)))
        sample_layout.addWidget(self.sample_count_spin)
        sample_layout.addWidget(QLabel("条"))
        sample_layout.addWidget(QLabel("舍牌模式:"))
        self.sample_pattern_combo = QComboBox()
        self.sample_pattern_combo.setToolTip("多舍牌模式时可选择为哪个模式生成样本")
        self.sample_pattern_combo.setMinimumWidth(120)
        sample_layout.addWidget(self.sample_pattern_combo)
        sample_layout.addWidget(QLabel("目标牌:"))
        self.sample_target_tile_combo = QComboBox()
        self.sample_target_tile_combo.setToolTip("多目标分析时可选择为哪个目标牌生成样本")
        self.sample_target_tile_combo.setMinimumWidth(60)
        sample_layout.addWidget(self.sample_target_tile_combo)
        sample_layout.addWidget(QLabel("目标:"))
        self.sample_target_combo = QComboBox()
        self.sample_target_combo.addItems(["全部", "有0张", "有1张", "有2张", "有3张"])
        self.sample_target_combo.setToolTip("单张模式: 有0~3张；搭子模式(执行查询后): 没有/有")
        sample_layout.addWidget(self.sample_target_combo)
        self.gen_sample_btn = QPushButton("生成样本")
        self.gen_sample_btn.clicked.connect(self.generate_samples)
        self.gen_sample_btn.setEnabled(False)
        sample_layout.addWidget(self.gen_sample_btn)
        sample_layout.addStretch()
        left_layout.addLayout(sample_layout)

        splitter.addWidget(left_widget)

        # 右侧：存档管理区（扩大显示区域）
        archive_widget = QGroupBox("存档管理")
        archive_layout = QVBoxLayout()

        self.save_archive_btn = QPushButton("存档当前结果")
        self.save_archive_btn.setToolTip("将当前查询结果保存到本地，避免闪退丢失")
        self.save_archive_btn.clicked.connect(self._save_current_to_archive)
        self.save_archive_btn.setEnabled(False)
        archive_layout.addWidget(self.save_archive_btn)

        # 文件夹设定
        folder_label = QLabel("文件夹:")
        folder_label.setStyleSheet("font-weight: bold; color: #58a6ff;")
        archive_layout.addWidget(folder_label)
        folder_row = QHBoxLayout()
        self.archive_folder_combo = QComboBox()
        self.archive_folder_combo.setMinimumWidth(140)
        self.archive_folder_combo.setToolTip("选择存档所属文件夹，新建存档时使用")
        folder_row.addWidget(self.archive_folder_combo, 1)
        new_folder_btn = QPushButton("新建")
        new_folder_btn.setMinimumWidth(56)
        new_folder_btn.setToolTip("创建新文件夹")
        new_folder_btn.clicked.connect(self._new_archive_folder)
        folder_row.addWidget(new_folder_btn)
        rename_folder_btn = QPushButton("重命名")
        rename_folder_btn.setMinimumWidth(68)
        rename_folder_btn.clicked.connect(self._rename_archive_folder)
        folder_row.addWidget(rename_folder_btn)
        del_folder_btn = QPushButton("删除")
        del_folder_btn.setMinimumWidth(56)
        del_folder_btn.clicked.connect(self._delete_archive_folder)
        folder_row.addWidget(del_folder_btn)
        archive_layout.addLayout(folder_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("显示:"))
        self.archive_filter_combo = QComboBox()
        self.archive_filter_combo.setMinimumWidth(100)
        self.archive_filter_combo.currentIndexChanged.connect(self._refresh_archive_list)
        filter_row.addWidget(self.archive_filter_combo, 1)
        archive_layout.addLayout(filter_row)

        archive_layout.addWidget(QLabel("搜索舍牌模式:"))
        self.archive_search_edit = QLineEdit()
        self.archive_search_edit.setPlaceholderText("输入关键词筛选...")
        self.archive_search_edit.textChanged.connect(self._filter_archive_list)
        archive_layout.addWidget(self.archive_search_edit)

        self.archive_list = QListWidget()
        self.archive_list.setMinimumWidth(280)
        self.archive_list.setMinimumHeight(180)
        self.archive_list.setToolTip("双击条目展开查看完整结果；可选中后移动到文件夹")
        self.archive_list.itemDoubleClicked.connect(self._on_archive_item_clicked)
        archive_layout.addWidget(self.archive_list, 1)

        move_archive_row = QHBoxLayout()
        move_label = QLabel("移动到:")
        self.archive_move_combo = QComboBox()
        self.archive_move_combo.setMinimumWidth(100)
        move_archive_row.addWidget(move_label)
        move_archive_row.addWidget(self.archive_move_combo, 1)
        move_archive_btn = QPushButton("移动")
        move_archive_btn.clicked.connect(self._move_archive_to_folder)
        move_archive_row.addWidget(move_archive_btn)
        archive_layout.addLayout(move_archive_row)

        del_archive_btn = QPushButton("删除选中")
        del_archive_btn.clicked.connect(self._delete_selected_archive)
        archive_layout.addWidget(del_archive_btn)

        archive_widget.setLayout(archive_layout)
        splitter.addWidget(archive_widget)
        splitter.setSizes([420, 480])  # 存档区至少约黄色框大小

        main_layout.addWidget(splitter)
        group.setLayout(main_layout)
        return group
    
    def _start_db_status_load(self):
        """启动后台线程获取数据库状态"""
        self._db_status_thread = DbStatusThread(self.db_path)
        self._db_status_thread.finished.connect(self._on_db_status_loaded)
        self._db_status_thread.start()

    def _on_db_status_loaded(self, stats: dict):
        """数据库状态加载完成回调"""
        if "error" in stats:
            self.db_status_label.setText(f"数据库状态: 未初始化 ({stats['error']})")
            return
        self.db_status_label.setText(_format_db_status_text(stats))
        _save_db_status_to_cache(self.db_path, stats)

    def update_db_status(self):
        """更新数据库状态（在后台线程执行，避免阻塞）"""
        self.db_status_label.setText("数据库状态: 加载中...")
        self._start_db_status_load()

    def _save_sample_limit(self, value: int):
        """保存样本上限到本地设置"""
        QSettings().setValue("sample_limit", value)

    def _save_matched_states_cap(self, value: int):
        """保存匹配状态保留条数到本地设置"""
        QSettings().setValue("matched_states_cap", value)

    def _save_gc_interval_batches(self, value: int):
        """保存内存维护间隔到本地设置"""
        QSettings().setValue("gc_interval_batches", value)

    def _save_analysis_batch_size(self, value: int):
        """保存分析批次大小到本地设置"""
        QSettings().setValue("analysis_batch_size", value)

    def _save_max_workers(self, value: int):
        QSettings().setValue("max_workers", value)

    def _copy_merged_result(self):
        """复制合并结果到剪贴板（Tab 分隔，可粘贴到 Excel）"""
        if self._excel_clipboard_text:
            cb = QApplication.clipboard()
            cb.setText(self._excel_clipboard_text)
            self.statusBar().showMessage("已复制合并结果到剪贴板", 2000)

    def _copy_result_to_excel(self):
        """复制当前分析结果到剪贴板（Tab 分隔，可直接粘贴到 Excel）"""
        if self._excel_clipboard_text:
            QApplication.clipboard().setText(self._excel_clipboard_text)
            QMessageBox.information(self, "已复制", "分析结果已复制到剪贴板，可直接粘贴到 Excel 中。")

    def _scroll_result_to_bottom(self):
        """将结果文本框滚动到最下方"""
        sb = self.result_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _populate_folder_combo(self):
        """刷新文件夹下拉框与筛选下拉框"""
        for combo in (self.archive_folder_combo, self.archive_move_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("未分类", None)
            for f in self._archive_folders:
                combo.addItem(f["name"], f["id"])
            combo.blockSignals(False)
        # 筛选下拉框
        if hasattr(self, "archive_filter_combo"):
            self.archive_filter_combo.blockSignals(True)
            self.archive_filter_combo.clear()
            self.archive_filter_combo.addItem("全部", "all")
            self.archive_filter_combo.addItem("未分类", None)
            for f in self._archive_folders:
                self.archive_filter_combo.addItem(f["name"], f["id"])
            self.archive_filter_combo.blockSignals(False)

    def _new_archive_folder(self):
        """新建文件夹"""
        name, ok = QInputDialog.getText(self, "新建文件夹", "请输入文件夹名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(f["name"] == name for f in self._archive_folders):
            QMessageBox.warning(self, "提示", f"已存在同名文件夹: {name}")
            return
        fid = _new_folder_id()
        self._archive_folders.append({"id": fid, "name": name})
        try:
            _save_archive(self.db_path, self._archive_entries, self._archive_folders)
            self._populate_folder_combo()
            QMessageBox.information(self, "成功", f"已创建文件夹: {name}")
        except Exception as e:
            QMessageBox.critical(self, "创建失败", str(e))

    def _rename_archive_folder(self):
        """重命名当前选中的文件夹"""
        idx = self.archive_folder_combo.currentIndex()
        if idx <= 0:
            QMessageBox.warning(self, "提示", "请先选择要重命名的文件夹（不能重命名「未分类」）")
            return
        fid = self.archive_folder_combo.currentData()
        folder = next((f for f in self._archive_folders if f["id"] == fid), None)
        if not folder:
            return
        name, ok = QInputDialog.getText(self, "重命名文件夹", "新名称:", text=folder["name"])
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(f["name"] == name and f["id"] != fid for f in self._archive_folders):
            QMessageBox.warning(self, "提示", f"已存在同名文件夹: {name}")
            return
        folder["name"] = name
        try:
            _save_archive(self.db_path, self._archive_entries, self._archive_folders)
            self._populate_folder_combo()
            QMessageBox.information(self, "成功", "文件夹已重命名")
        except Exception as e:
            QMessageBox.critical(self, "重命名失败", str(e))

    def _delete_archive_folder(self):
        """删除选中的文件夹（其中存档移至未分类）"""
        idx = self.archive_folder_combo.currentIndex()
        if idx <= 0:
            QMessageBox.warning(self, "提示", "请先选择要删除的文件夹（不能删除「未分类」）")
            return
        fid = self.archive_folder_combo.currentData()
        folder = next((f for f in self._archive_folders if f["id"] == fid), None)
        if not folder:
            return
        if QMessageBox.question(
            self, "确认删除", f"确定删除文件夹「{folder['name']}」？其中的存档将移至未分类。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        for e in self._archive_entries:
            if e.get("folder_id") == fid:
                e["folder_id"] = None
        self._archive_folders = [f for f in self._archive_folders if f["id"] != fid]
        try:
            _save_archive(self.db_path, self._archive_entries, self._archive_folders)
            self._populate_folder_combo()
            self._refresh_archive_list()
            QMessageBox.information(self, "成功", "文件夹已删除")
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def _move_archive_to_folder(self):
        """将选中的存档移动到指定文件夹"""
        item = self.archive_list.currentItem()
        if not item:
            QMessageBox.warning(self, "提示", "请先选择要移动的存档")
            return
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self._archive_entries):
            return
        folder_id = self.archive_move_combo.currentData()
        self._archive_entries[idx]["folder_id"] = folder_id
        try:
            _save_archive(self.db_path, self._archive_entries, self._archive_folders)
            self._refresh_archive_list()
            folder_name = self.archive_move_combo.currentText()
            QMessageBox.information(self, "成功", f"已移至「{folder_name}」")
        except Exception as e:
            QMessageBox.critical(self, "移动失败", str(e))

    def _save_current_to_archive(self):
        """将当前查询结果保存到存档"""
        if not self.last_query_result:
            QMessageBox.warning(self, "提示", "当前无查询结果可存档")
            return
        folder_id = self.archive_folder_combo.currentData()
        pattern_summary = _build_pattern_summary(self.last_query_result)
        entry = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S") + "_" + str(len(self._archive_entries)),
            "created_at": datetime.now().isoformat(),
            "pattern_summary": pattern_summary,
            "result_display_text": self.result_text.toPlainText(),
            "excel_text": self._excel_clipboard_text,
            "folder_id": folder_id,
        }
        self._archive_entries.insert(0, entry)
        try:
            _save_archive(self.db_path, self._archive_entries, self._archive_folders)
            self._refresh_archive_list()
            tip = pattern_summary[:50] + ("..." if len(pattern_summary) > 50 else "")
            QMessageBox.information(self, "存档成功", f"已保存: {tip}")
        except Exception as e:
            QMessageBox.critical(self, "存档失败", str(e))

    def _refresh_archive_list(self):
        """刷新存档列表显示（含文件夹筛选）"""
        self.archive_list.clear()
        filter_val = getattr(self, "archive_filter_combo", None)
        fdata = filter_val.currentData() if (filter_val and filter_val.count()) else "all"
        for i, e in enumerate(self._archive_entries):
            if fdata != "all":
                fid = e.get("folder_id")
                if (fdata is None and fid is not None) or (fdata is not None and fid != fdata):
                    continue
            folder_name = ""
            if e.get("folder_id"):
                f = next((x for x in self._archive_folders if x["id"] == e["folder_id"]), None)
                if f:
                    folder_name = f" [{f['name']}]"
            summary = e.get("pattern_summary", "")
            time_str = e.get("created_at", "")[:19].replace("T", " ")
            item = QListWidgetItem(f"{summary}{folder_name}\n  {time_str}")
            item.setData(Qt.UserRole, i)
            self.archive_list.addItem(item)
        self._filter_archive_list()

    def _filter_archive_list(self):
        """根据搜索框内容筛选存档列表"""
        kw = self.archive_search_edit.text().strip().lower()
        for i in range(self.archive_list.count()):
            item = self.archive_list.item(i)
            idx = item.data(Qt.UserRole)
            if idx is None or idx >= len(self._archive_entries):
                item.setHidden(True)
                continue
            entry = self._archive_entries[idx]
            summary = (entry.get("pattern_summary", "") or "").lower()
            item.setHidden(bool(kw) and kw not in summary)

    def _on_archive_item_clicked(self, item: QListWidgetItem):
        """点击存档项，展开显示具体结果"""
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self._archive_entries):
            return
        entry = self._archive_entries[idx]
        dlg = ArchiveViewDialog(self, entry)
        dlg.exec_()

    def _delete_selected_archive(self):
        """删除选中的存档项"""
        item = self.archive_list.currentItem()
        if not item:
            QMessageBox.warning(self, "提示", "请先选择要删除的存档")
            return
        idx = item.data(Qt.UserRole)
        if idx is None or idx >= len(self._archive_entries):
            return
        if QMessageBox.question(
            self, "确认删除", "确定要删除该存档吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        self._archive_entries.pop(idx)
        try:
            _save_archive(self.db_path, self._archive_entries, self._archive_folders)
            self._refresh_archive_list()
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def _build_excel_text(self, result: dict, pr_list: Optional[list], multi: bool) -> str:
        """生成 Tab 分隔的 Excel 友好格式，统一为：舍牌模式\t目标牌\t巡目范围\t0张\t1张\t2张\t3张（百分比）"""
        tr = result.get('turn_range')
        turn_str = f"{tr[0]}-{tr[1]}巡" if tr else "不限"
        use_tenpai = (result.get('analysis_target') == 'tenpai')
        header = "舍牌模式\t目标牌\t巡目范围\t0张\t1张\t2张\t3张"
        rows = [header]

        def _row(pattern: str, target: str, prob: dict) -> str:
            """生成一行数据，prob 为 {0:%, 1:%, ...} 概率分布"""
            p0 = prob.get(0, 0)
            p1 = prob.get(1, 0)
            p2 = prob.get(2, 0)
            p3 = prob.get(3, 0)
            return f"{pattern}\t{target}\t{turn_str}\t{p0:.2f}\t{p1:.2f}\t{p2:.2f}\t{p3:.2f}"

        if multi and pr_list:
            for pr in pr_list:
                prob = pr.get('probability_distribution', {})
                target = "听牌" if use_tenpai else pr.get('target', '')
                rows.append(_row(pr['pattern_str'], target, prob))
        else:
            prob = result.get('probability_distribution', {})
            pattern = result.get('query_pattern_str', '') or '-'.join(result.get('query_pattern', []))
            target = "听牌" if use_tenpai else result.get('target_tile', '')
            multi_target = result.get('multi_target', False)
            if multi_target:
                for tk in result.get('target_tiles', []):
                    pt = prob.get(tk, {})
                    rows.append(_row(pattern, tk, pt))
            else:
                rows.append(_row(pattern, target, prob))

        rows.append("")
        rows.append(f"分析半庄数\t{result.get('total_logs_analyzed', 0)}")
        rows.append(f"分析耗时(秒)\t{result.get('elapsed_seconds', 0):.1f}")
        return "\n".join(rows)

    def _setup_multi_pattern_merge(self, result: dict, pr_list: list) -> None:
        """设置多模式勾选与合并区域"""
        # 清除旧勾选框
        for cb in self._pattern_checkboxes:
            cb.setParent(None)
            cb.deleteLater()
        self._pattern_checkboxes.clear()

        def _clear_layout(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        _clear_layout(self.multi_merge_layout)

        use_tenpai = self.last_query_result and self.last_query_result.get("analysis_target") == "tenpai"
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("勾选合并（目标牌相同）:" if not use_tenpai else "勾选合并:"))
        header_row.addStretch()
        select_all_btn = QPushButton("全选")
        select_all_btn.setFixedWidth(44)
        select_all_btn.clicked.connect(lambda: self._set_all_pattern_checks(True, result, pr_list))
        header_row.addWidget(select_all_btn)
        self.multi_merge_layout.addLayout(header_row)

        # 舍牌模式勾选框：每行一个（QCheckBox 无 setWordWrap，长文本靠 tooltip 查看）
        cb_container = QWidget()
        cb_vlayout = QVBoxLayout(cb_container)
        cb_vlayout.setContentsMargins(0, 4, 0, 0)
        cb_vlayout.setSpacing(4)
        for i, pr in enumerate(pr_list):
            lbl = f"{pr['pattern_str']} → 听牌" if use_tenpai else f"{pr['pattern_str']} → {pr['target']}"
            cb = QCheckBox(lbl)
            cb.setToolTip(lbl)
            cb.setChecked(True)
            cb.setProperty("idx", i)
            cb.stateChanged.connect(lambda *_: self._update_merged_result(result, pr_list))
            self._pattern_checkboxes.append(cb)
            cb_vlayout.addWidget(cb)
        cb_scroll = QScrollArea()
        cb_scroll.setWidget(cb_container)
        cb_scroll.setWidgetResizable(True)
        cb_scroll.setFrameShape(QFrame.NoFrame)
        cb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cb_scroll.setMaximumHeight(150)
        cb_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.multi_merge_layout.addWidget(cb_scroll)

        merge_display_row = QHBoxLayout()
        self.merged_result_label = QLabel("")
        self.merged_result_label.setWordWrap(True)
        self.merged_result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.merged_result_label.setMinimumHeight(72)  # 确保多行合并结果有足够空间，避免文字叠在一起
        merge_display_row.addWidget(self.merged_result_label, 1)
        copy_merge_btn = QPushButton("复制合并")
        copy_merge_btn.setFixedWidth(70)
        copy_merge_btn.setToolTip("将合并结果复制到剪贴板（Tab 分隔，可粘贴到 Excel）")
        copy_merge_btn.clicked.connect(self._copy_merged_result)
        merge_display_row.addWidget(copy_merge_btn)
        self.multi_merge_layout.addLayout(merge_display_row)
        self.multi_merge_widget.setVisible(True)
        self._update_merged_result(result, pr_list)

    def _set_all_pattern_checks(self, checked: bool, result: dict, pr_list: list) -> None:
        """全选/取消勾选所有模式"""
        for cb in self._pattern_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._update_merged_result(result, pr_list)

    def _update_merged_result(self, result: dict, pr_list: list) -> None:
        """根据勾选更新合并结果显示及 Excel 文本"""
        checked = [pr_list[i] for i, cb in enumerate(self._pattern_checkboxes) if cb.isChecked()]
        if not checked:
            self.merged_result_label.setText("（请至少勾选一个模式）")
            self._excel_clipboard_text = self._build_excel_text(result, pr_list, multi=True)
            return
        total_matches = sum(pr['matches'] for pr in checked)
        use_tenpai = result.get("analysis_target") == "tenpai"
        keys = [0, 1] if use_tenpai else [0, 1, 2, 3]
        merged_dist = {k: 0 for k in keys}
        for pr in checked:
            d = pr.get('target_count_distribution', {})
            for k in keys:
                merged_dist[k] += d.get(k, 0)
        probs = [(merged_dist[k] / total_matches * 100) if total_matches > 0 else 0 for k in keys]
        if use_tenpai:
            target_label = "听牌"
            txt = (
                f"合并结果（分析目标 {target_label}）：总匹配 {_fmt_int(total_matches)} 次\n"
                f"  未听牌: {probs[0]:.1f}%  听牌: {probs[1]:.1f}%"
            )
        else:
            target = checked[0]['target']
            lines = [f"合并结果（目标 {target}）：总匹配 {_fmt_int(total_matches)} 次"]
            for k, label in enumerate(["有0张", "有1张", "有2张", "有3张"]):
                if k < len(probs):
                    lines.append(f"  {label}: {probs[k]:.1f}%")
            txt = "\n".join(lines)
        self.merged_result_label.setText(txt)
        # 更新 Excel 文本：统一格式 舍牌模式\t目标牌\t巡目范围\t0张\t1张\t2张\t3张（概率%）
        tr = result.get('turn_range')
        turn_str = f"{tr[0]}-{tr[1]}巡" if tr else "不限"
        header = "舍牌模式\t目标牌\t巡目范围\t0张\t1张\t2张\t3张"
        base_rows = [header]
        target = "听牌" if use_tenpai else checked[0]['target']
        for pr in checked:
            prob = pr.get('probability_distribution', {})
            pt = "听牌" if use_tenpai else pr.get('target', '')
            base_rows.append(f"{pr['pattern_str']}\t{pt}\t{turn_str}\t"
                f"{prob.get(0, 0):.2f}\t{prob.get(1, 0):.2f}\t{prob.get(2, 0):.2f}\t{prob.get(3, 0):.2f}")
        p0, p1 = probs[0], probs[1]
        p2 = probs[2] if len(probs) > 2 else 0
        p3 = probs[3] if len(probs) > 3 else 0
        base_rows.append(f"【合并】\t{target}\t{turn_str}\t{p0:.2f}\t{p1:.2f}\t{p2:.2f}\t{p3:.2f}")
        base_rows.append("")
        base_rows.append(f"分析半庄数\t{result.get('total_logs_analyzed', 0)}")
        base_rows.append(f"分析耗时(秒)\t{result.get('elapsed_seconds', 0):.1f}")
        self._excel_clipboard_text = "\n".join(base_rows)

    def download_data(self):
        """下载历史数据"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox
        from datetime import datetime
        
        # 创建自定义对话框
        dialog = QDialog(self)
        dialog.setWindowTitle("选择下载年份范围")
        dialog.setMinimumWidth(300)
        
        layout = QVBoxLayout()
        
        # 说明文字
        label = QLabel("请选择要下载的年份范围：\n可用范围：2015 - 至今")
        layout.addWidget(label)
        
        # 开始年份
        start_layout = QHBoxLayout()
        start_layout.addWidget(QLabel("开始年份:"))
        start_spin = QSpinBox()
        start_spin.setRange(2015, datetime.now().year)
        start_spin.setValue(2020)
        start_layout.addWidget(start_spin)
        layout.addLayout(start_layout)
        
        # 结束年份
        end_layout = QHBoxLayout()
        end_layout.addWidget(QLabel("结束年份:"))
        end_spin = QSpinBox()
        end_spin.setRange(2015, datetime.now().year)
        end_spin.setValue(datetime.now().year)
        end_layout.addWidget(end_spin)
        layout.addLayout(end_layout)
        
        # 按钮
        button_layout = QHBoxLayout()
        ok_button = QPushButton("确定")
        cancel_button = QPushButton("取消")
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)
        
        dialog.setLayout(layout)
        
        # 连接信号
        ok_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        
        # 显示对话框
        if dialog.exec_() == QDialog.Accepted:
            start_year = start_spin.value()
            end_year = end_spin.value()
            
            # 验证年份范围
            if start_year > end_year:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.warning(self, "输入错误", "开始年份不能大于结束年份！")
                return
            
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)  # 不确定进度
            
            self.download_thread = DownloadThread(self.downloader, start_year, end_year)
            self.download_thread.progress.connect(self.on_download_progress)
            self.download_thread.finished.connect(self.on_download_finished)
            self.download_thread.start()
            
            self.download_btn.setEnabled(False)
    
    def update_data(self):
        """更新最新数据"""
        try:
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            
            result = self.downloader.update_latest_logs()
            
            self.progress_bar.setVisible(False)
            QMessageBox.information(self, "更新完成", f"最新数据更新完成\n{result}")
            self.update_db_status()
        except Exception as e:
            self.progress_bar.setVisible(False)
            QMessageBox.critical(self, "更新失败", f"更新失败: {str(e)}")
    
    def on_download_progress(self, message: str):
        """下载进度回调"""
        self.result_text.append(message)
    
    def on_download_finished(self, success: bool, message: str):
        """下载完成回调"""
        self.progress_bar.setVisible(False)
        self.download_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "下载完成", message)
            self.update_db_status()
        else:
            QMessageBox.critical(self, "下载失败", message)
    
    def _rebuild_visible_constraint_grid(self):
        """将场上可见枚数条目按 2 列重新排列"""
        layout = self.visible_constraint_rows_layout
        while layout.count():
            item = layout.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)
        for i, (_, _, row_widget) in enumerate(self.visible_constraint_row_refs):
            layout.addWidget(row_widget, i // 2, i % 2)

    def _add_visible_constraint_row(self):
        """添加一行场上可见枚数输入（牌 + 范围滑块），每行可并排两个条目，每个条目固定宽度"""
        row_widget = QWidget()
        row_widget.setFixedWidth(292)
        row_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 8, 2)
        row_layout.setSpacing(4)
        tile_edit = QLineEdit()
        tile_edit.setPlaceholderText("牌，如 8s")
        tile_edit.setFixedWidth(52)
        row_layout.addWidget(tile_edit)
        row_layout.addWidget(QLabel("可见"))
        range_slider = DiscreteRangeSlider()
        row_layout.addWidget(range_slider)
        row_layout.addWidget(QLabel("枚"))
        remove_btn = QPushButton("X")
        remove_btn.setFixedSize(32, 24)
        remove_btn.setToolTip("删除此行")
        row_layout.addWidget(remove_btn)
        self.visible_constraint_row_refs.append((tile_edit, range_slider, row_widget))
        self._rebuild_visible_constraint_grid()

        def do_remove():
            self.visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self.visible_constraint_row_refs.remove((tile_edit, range_slider, row_widget))
            self._rebuild_visible_constraint_grid()
        remove_btn.clicked.connect(do_remove)

    def _get_visible_constraints_from_ui(self) -> Dict[str, Tuple[int, int]]:
        """从场上可见枚数行收集约束，空牌名跳过"""
        visible_constraints = {}
        for tile_edit, range_slider, _ in self.visible_constraint_row_refs:
            tile = tile_edit.text().strip()
            if not tile:
                continue
            min_count, max_count = range_slider.getRange()
            if min_count > max_count:
                continue
            visible_constraints[tile] = (min_count, max_count)
        return visible_constraints

    def execute_query(self):
        """执行查询"""
        forced_target = self._forced_analysis_target
        self._forced_analysis_target = None
        analysis_target = forced_target or (self.analysis_target_combo.currentData() or "target_count")
        use_tenpai = (analysis_target == "tenpai")
        use_instant = (analysis_target == "deal_in_instant")
        use_outcome = (analysis_target == "outcome")
        require_target = not (use_tenpai or use_outcome)
        query_items = self._get_pattern_items(require_target=require_target)
        if not query_items:
            msg = "请至少输入一个舍牌模式"
            if require_target:
                msg += "和对应的目标牌"
            elif use_outcome:
                msg += "（和铳率无需目标牌）"
            QMessageBox.warning(self, "输入错误", msg)
            return
        first_pattern, first_target = query_items[0]
        
        # 宝牌约束
        if self.dora_irrelevant_radio.isChecked():
            dora_constraint = "dora_unrelated"
            dora_position_spec = []
        elif self.dora_matches_position_radio.isChecked():
            dora_constraint = "dora_matches_position"
            pos_str = self.dora_position_input.text().strip()
            if not pos_str:
                QMessageBox.warning(self, "输入错误", "请输入模式位置（如 1 或 1,3）")
                return
            try:
                dora_position_spec = [int(x.strip()) - 1 for x in pos_str.replace("，", ",").split(",") if x.strip()]
            except ValueError:
                QMessageBox.warning(self, "输入错误", "位置请输入数字，如 1 或 1,3")
                return
            if not dora_position_spec or any(p < 0 for p in dora_position_spec):
                QMessageBox.warning(self, "输入错误", "位置须为正整数（1-based）")
                return
        else:
            dora_tile = self.dora_tile_input.text().strip()
            if not dora_tile:
                QMessageBox.warning(self, "输入错误", "请输入宝牌")
                return
            dora_constraint = dora_tile
            dora_position_spec = []
        
        # 立直约束
        if self.riichi_any_radio.isChecked():
            riichi_constraint = "any"
        elif self.riichi_has_radio.isChecked():
            riichi_constraint = "has_riichi"
        else:
            riichi_constraint = "no_riichi"
        
        # 副露约束
        if self.call_any_radio.isChecked():
            call_constraint = "any"
        elif self.call_has_radio.isChecked():
            call_constraint = "has_call"
        else:
            call_constraint = "no_call"

        # 副露区域约束（目标玩家必须有这些副露，AND，最多 4 个）
        call_area_constraints = [
            le.text().strip() for le in self.call_area_inputs
            if le.text().strip()
        ][:4]
        
        # 场上可见枚数
        visible_constraints = self._get_visible_constraints_from_ui()
        
        # 样本上限
        sample_limit = self.sample_limit_input.value()

        # 巡目范围：优先从输入框读取，为空则用滑块
        turn_ranges = self._get_turn_ranges_from_ui()
        if not turn_ranges:
            turn_min, turn_max = self.turn_range_slider.getRange()
            if turn_min <= turn_max:
                turn_ranges = [(turn_min, turn_max)]
        use_grid = len(turn_ranges) > 1 and not (use_outcome or use_instant)  # 和铳率/即时铳率不支持矩阵分析
        if (use_outcome or use_instant) and turn_ranges:
            t_min = min(r[0] for r in turn_ranges)
            t_max = max(r[1] for r in turn_ranges)
            turn_range = None if (t_min == 1 and t_max == 18) else (t_min, t_max)
        elif len(turn_ranges) == 1:
            tr = turn_ranges[0]
            turn_range = None if (tr[0] == 1 and tr[1] == 18) else tr
        else:
            turn_range = None
        
        # 从缓存读取对局总数，避免分析开始时执行耗时的 COUNT(*) 导致界面假死
        cached = _load_db_status_from_cache(self.db_path)
        total_logs_hint = cached.get("total_logs") if cached else None

        matched_states_cap = self.matched_states_cap_spin.value()
        analysis_batch_size = self.analysis_batch_size_spin.value()

        params = {
            "query_items": query_items,
            "analysis_target": analysis_target,
            "visible_constraints": visible_constraints if visible_constraints else None,
            "dora_constraint": dora_constraint,
            "dora_position_spec": dora_position_spec,
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "call_area_constraints": call_area_constraints if call_area_constraints else None,
            "turn_range": turn_range,
            "sample_limit": sample_limit,
            "total_logs_hint": total_logs_hint,
            "matched_states_cap": matched_states_cap,
            "analysis_batch_size": analysis_batch_size,
            "exclude_south4": self.exclude_south4_check.isChecked(),
            "exclude_south3": self.exclude_south3_check.isChecked(),
            "prior_discard_exclusion": self.prior_discard_exclusion_input.text().strip() or None,
            "max_workers": self.max_workers_spin.value(),
            "gc_interval_batches": self.gc_interval_batches_spin.value(),
        }
        
        # 启动查询线程
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        if use_outcome:
            self.query_thread = OutcomeQueryThread(self.analyzer, params)
        elif use_grid:
            self.query_thread = GridQueryThread(
                self.analyzer, params, query_items, turn_ranges, analysis_target
            )
        else:
            self.query_thread = QueryThread(self.analyzer, params)
        self.query_thread.progress.connect(self.on_query_progress)
        self.query_thread.progress_num.connect(self.on_query_progress_num)
        self.query_thread.finished.connect(self.on_query_finished)
        self.query_thread.start()
        
        self.query_btn.setEnabled(True)  # 保持可点击，以便用户点击「取消分析」
        self.query_btn.setText("取消分析")
        self.query_btn.clicked.disconnect()
        self.query_btn.clicked.connect(self.cancel_query)
    
    def cancel_query(self):
        """取消查询"""
        if self.query_thread:
            self.query_thread.cancel()
            self.query_btn.setText("取消中...")
            self.query_btn.setEnabled(False)

    def generate_samples(self):
        """生成验证样本（多舍牌模式时按当前选择的舍牌模式生成）"""
        if not self.last_query_params:
            QMessageBox.warning(self, "提示", "请先执行查询")
            return
        # 听牌模式：全部/未听牌/听牌；搭子模式：全部/没有/有；单张模式：全部/有0张~有3张；和铳率模式：全部/和牌/放铳/两者皆没有
        use_tenpai = self.last_query_params.get("analysis_target") == "tenpai"
        use_instant = self.last_query_params.get("analysis_target") == "deal_in_instant"
        use_outcome = self.last_query_params.get("analysis_target") == "outcome"
        is_combo = self.last_query_params.get("is_combo", False)
        if use_instant:
            if self.sample_target_combo.count() != 4 or self.sample_target_combo.itemText(1) != "可铳":
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(["全部", "可铳", "不可铳", "振听过滤掉"])
        elif use_outcome:
            if self.sample_target_combo.count() != 4 or self.sample_target_combo.itemText(1) != "和牌":
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(["全部", "和牌", "放铳", "两者皆没有"])
        elif use_tenpai:
            if self.sample_target_combo.count() != 3 or self.sample_target_combo.itemText(1) != "未听牌":
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(["全部", "未听牌", "听牌"])
        elif is_combo:
            if self.sample_target_combo.count() != 3 or self.sample_target_combo.itemText(1) != "没有":
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(["全部", "没有", "有"])
        else:
            if self.sample_target_combo.count() != 5 or self.sample_target_combo.itemText(1) != "有0张":
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(["全部", "有0张", "有1张", "有2张", "有3张"])
        count = self.sample_count_spin.value()
        idx = self.sample_target_combo.currentIndex()
        if use_instant:
            deal_in_filter = None if idx == 0 else ["hit", "miss", "furiten"][idx - 1]
            outcome_filter = None
            target_count_filter = None
        elif use_outcome:
            deal_in_filter = None
            outcome_filter = None if idx == 0 else ["win", "deal_in", "neither"][idx - 1]
            target_count_filter = None
        else:
            deal_in_filter = None
            outcome_filter = None
            target_count_filter = None if idx == 0 else idx - 1
        target_tile_filter = None
        if self.sample_target_tile_combo.isVisible() and self.sample_target_tile_combo.currentIndex() > 0:
            target_tile_filter = self.sample_target_tile_combo.currentText()

        # 当前选中的舍牌模式（多模式时用于过滤 sample_pool 或传给收集接口）
        pattern_index = self.sample_pattern_combo.currentIndex()
        query_items = self.last_query_params.get("query_items", [])
        if not query_items or pattern_index < 0 or pattern_index >= len(query_items):
            QMessageBox.warning(self, "提示", "请先执行查询后再生成样本")
            return
        selected_pattern, selected_target = query_items[pattern_index]
        self._last_sample_pattern_str = "-".join(selected_pattern)
        self._last_sample_target_tile = selected_target

        # 构建用于本次采样的参数（单模式用选中的 pattern/target）
        params_for_sample = {
            **self.last_query_params,
            "query_pattern": selected_pattern,
            "query_pattern_str": self._last_sample_pattern_str,
            "target_tile": selected_target,
        }

        # 若有预收集的 sample_pool 且为多模式，只保留当前选中模式的样本
        sample_pool = self.last_query_result.get("sample_pool") if self.last_query_result else None
        multi = self.last_query_params.get("multi_pattern", False) and len(query_items) > 1
        if sample_pool and multi:
            sample_pool = [s for s in sample_pool if s.get("matched_pattern_idx") == pattern_index]

        self.gen_sample_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.result_text.append("\n正在收集验证样本...")

        self.sample_thread = SampleThread(
            self.analyzer,
            params_for_sample,
            count,
            target_count_filter,
            sample_pool=sample_pool,
            target_tile_filter=target_tile_filter,
            outcome_filter=outcome_filter,
            deal_in_filter=deal_in_filter,
        )
        self.sample_thread.progress.connect(lambda s: self.result_text.append(s))
        self.sample_thread.progress_num.connect(self.on_sample_progress_num)
        self.sample_thread.finished.connect(self.on_sample_finished)
        self.sample_thread.start()

    def on_sample_progress_num(self, current: int, total: int):
        """样本收集数值进度"""
        if total > 0:
            percent = min(100, int(current / total * 100))
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
        else:
            self.progress_bar.setRange(0, 0)

    def on_sample_finished(self, success: bool, samples_or_error):
        """样本收集完成"""
        self.progress_bar.setVisible(False)
        self.gen_sample_btn.setEnabled(True)
        if success and samples_or_error:
            samples = samples_or_error
            query_str = getattr(self, "_last_sample_pattern_str", None) or self.last_query_params.get("query_pattern_str", "-".join(self.last_query_params.get("query_pattern", [])))
            target_tile = getattr(self, "_last_sample_target_tile", None) or self.last_query_params.get("target_tile", "")
            if self.last_query_params.get("analysis_target") == "tenpai":
                target_tile = "听牌"  # 样本展示用
            elif self.last_query_params.get("analysis_target") == "outcome":
                target_tile = "和铳率"  # 样本展示用
            text = format_samples_for_display(
                samples,
                query_str,
                target_tile,
                analysis_target=self.last_query_params.get("analysis_target", "target_count"),
            )
            dlg = SampleDialog(self, text, query_str.replace("-", "_"))
            dlg.exec_()
        elif not success:
            QMessageBox.critical(self, "生成失败", str(samples_or_error))
        else:
            QMessageBox.information(self, "提示", "未找到符合条件的样本")
    
    def on_query_progress(self, message: str):
        """查询进度回调"""
        self.result_text.append(message)
    
    def on_query_progress_num(self, current: int, total: int):
        """查询数值进度回调"""
        if total > 0:
            percent = min(100, int(current / total * 100))
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
        else:
            # 无总数时使用 indeterminant 样式
            self.progress_bar.setRange(0, 0)
    
    def on_query_finished(self, success: bool, result):
        """查询完成回调"""
        self.progress_bar.setVisible(False)
        self.query_btn.setEnabled(True)
        self.query_btn.setText("开始分析")
        self.query_btn.clicked.disconnect()
        self.query_btn.clicked.connect(self.execute_query)

        if success:
            # 和铳率统计结果
            if isinstance(result, dict) and {"total", "wins", "deal_ins"}.issubset(result.keys()):
                self.main_tab.setCurrentIndex(2)
                n = result.get("total", 0)
                wins = result.get("wins", 0)
                deal_ins = result.get("deal_ins", 0)
                wr = result.get("win_rate", 0)
                dr = result.get("deal_in_rate", 0)
                pattern_str = result.get("query_pattern_str", "")
                outcome_text = f"""结局统计完成

查询模式: {pattern_str}
总样本数: {_fmt_int(n)}
和了: {_fmt_int(wins)} 次  →  和了率: {wr:.2%}
放铳: {_fmt_int(deal_ins)} 次  →  放铳率: {dr:.2%}

分析半庄数: {_fmt_int(result.get('total_logs_analyzed', 0))}
耗时: {result.get('elapsed_seconds', 0):.1f} 秒"""
                self.result_text.setText(outcome_text)
                self.last_query_result = result
                qp = result.get("query_pattern", [])
                qt = result.get("target_tile", "5z")
                self.last_query_params = {
                    "analysis_target": "outcome",
                    "query_pattern": qp,
                    "query_pattern_str": pattern_str,
                    "target_tile": qt,
                    "query_items": [(qp, qt)] if qp else [],
                }
                self.gen_sample_btn.setEnabled(True)
                self.copy_excel_btn.setEnabled(True)
                self.save_archive_btn.setEnabled(True)
                self.matrix_display_btn.setEnabled(False)
                self.multi_merge_widget.setVisible(False)
                # 和铳率模式：样本按结局筛选
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(["全部", "和牌", "放铳", "两者皆没有"])
                self.sample_pattern_combo.clear()
                self.sample_pattern_combo.addItem(f"{pattern_str} → 和铳率")
                self.sample_target_tile_combo.setVisible(False)
                return

            # 矩阵结果（M>1 巡目）
            if result.get("table_dist") is not None:
                self.last_query_result = result
                self.last_query_params = {"analysis_target": result.get("analysis_target", "target_count")}
                n_pat = len(result.get("patterns", []))
                n_tr = len(result.get("turn_ranges", []))
                summary = (
                    f"矩阵分析完成：{n_pat} 模式 × {n_tr} 巡目，共 {n_pat * n_tr} 格\n"
                    f"分析半庄数: {_fmt_int(result.get('total_logs_analyzed', 0))}\n"
                    f"分析耗时: {result.get('elapsed_seconds', 0):.1f} 秒\n\n"
                    "点击「展示矩阵」查看并可勾选 0/1/2/3 张后复制 Excel 格式。"
                )
                self.result_text.setText(summary)
                self.multi_merge_widget.setVisible(False)
                self.gen_sample_btn.setEnabled(False)
                self.matrix_display_btn.setEnabled(True)
                header_col = result.get("header_col", [])
                header_row = result.get("header_row", [])
                _merge_keys = [1, 2] if result.get("analysis_target") != "tenpai" else [1]
                _tbl = {}
                for (tr_idx, pat_idx), dist in result.get("table_dist", {}).items():
                    _tbl[(tr_idx, pat_idx)] = round(sum(dist.get(k, 0) for k in _merge_keys), 2)
                _rows = ["巡目范围\t" + "\t".join(header_col)]
                for tr_idx, lbl in enumerate(header_row):
                    _cells = [lbl]
                    for pat_idx in range(len(header_col)):
                        _cells.append(str(_tbl.get((tr_idx, pat_idx), "")))
                    _rows.append("\t".join(_cells))
                self._excel_clipboard_text = "\n".join(_rows)
                self.copy_excel_btn.setEnabled(True)
                self.save_archive_btn.setEnabled(True)
                self.main_tab.setCurrentIndex(2)
                return

            self.last_query_result = result
            self.matrix_display_btn.setEnabled(False)
            self.main_tab.setCurrentIndex(2)  # 自动切换到查询结果标签页
            if result.get("multi_pattern") and result.get("pattern_results"):
                query_items = [(pr["pattern"], pr["target"]) for pr in result["pattern_results"]]
            else:
                query_items = self._get_pattern_items()
            if not query_items:
                query_items = [(result.get("query_pattern", []), result.get("target_tile", ""))]
            first_pattern, first_target = query_items[0]
            if self.dora_irrelevant_radio.isChecked():
                dora_constraint = "dora_unrelated"
            else:
                dora_constraint = self.dora_tile_input.text().strip() or "any"
            if self.riichi_any_radio.isChecked():
                riichi_constraint = "any"
            elif self.riichi_has_radio.isChecked():
                riichi_constraint = "has_riichi"
            else:
                riichi_constraint = "no_riichi"
            if self.call_any_radio.isChecked():
                call_constraint = "any"
            elif self.call_has_radio.isChecked():
                call_constraint = "has_call"
            else:
                call_constraint = "no_call"
            call_area_constraints = [le.text().strip() for le in self.call_area_inputs if le.text().strip()][:4]
            visible_constraints = self._get_visible_constraints_from_ui()
            turn_min, turn_max = self.turn_range_slider.getRange()
            turn_range = None
            if turn_min <= turn_max and not (turn_min == 1 and turn_max == 18):
                turn_range = (turn_min, turn_max)
            cached = _load_db_status_from_cache(self.db_path)
            total_logs_hint = cached.get("total_logs") if cached else None
            self.last_query_params = {
                "query_items": query_items,
                "query_pattern": first_pattern,
                "query_pattern_str": "-".join(first_pattern),
                "target_tile": first_target,
                "analysis_target": result.get("analysis_target", "target_count"),
                "dora_constraint": dora_constraint,
                "dora_position_spec": self._parse_dora_position_spec() if self.dora_matches_position_radio.isChecked() else [],
                "visible_constraints": visible_constraints if visible_constraints else None,
                "riichi_constraint": riichi_constraint,
                "call_constraint": call_constraint,
                "call_area_constraints": call_area_constraints,
                "turn_range": turn_range,
                "sample_limit": self.sample_limit_input.value(),
                "is_combo": result.get("is_combo", False),
                "total_logs_hint": total_logs_hint,
                "exclude_south4": self.exclude_south4_check.isChecked(),
            "exclude_south3": self.exclude_south3_check.isChecked(),
                "prior_discard_exclusion": self.prior_discard_exclusion_input.text().strip() or None,
                "analysis_batch_size": self.analysis_batch_size_spin.value(),
                "gc_interval_batches": self.gc_interval_batches_spin.value(),
            }
            self.gen_sample_btn.setEnabled(True)
            # 舍牌模式选择：多模式时列出每个模式供生成样本时选择
            self.sample_pattern_combo.clear()
            pr_list = result.get("pattern_results", []) if result.get("multi_pattern") else []
            use_tenpai = (result.get("analysis_target") == "tenpai")
            use_instant = (result.get("analysis_target") == "deal_in_instant")
            if result.get("multi_pattern") and pr_list:
                for pr in pr_list:
                    lbl = f"{pr['pattern_str']} → 听牌" if use_tenpai else f"{pr['pattern_str']} → {pr['target']}"
                    self.sample_pattern_combo.addItem(lbl)
            else:
                first_pattern, first_target = query_items[0]
                lbl = "-".join(first_pattern) + (" → 听牌" if use_tenpai else f" → {first_target}")
                self.sample_pattern_combo.addItem(lbl)
            # 多目标时：目标牌下拉（全部/6s/2m/5p）
            self.sample_target_tile_combo.clear()
            target_tiles = result.get("target_tiles") or []
            if target_tiles:
                self.sample_target_tile_combo.addItems(["全部"] + target_tiles)
                self.sample_target_tile_combo.setVisible(True)
            else:
                self.sample_target_tile_combo.addItem("全部")
                self.sample_target_tile_combo.setVisible(False)
            # 搭子模式：全部/没有/有；听牌模式：全部/未听牌/听牌；单张模式：全部/有0张~有3张
            self.sample_target_combo.clear()
            if use_instant:
                self.sample_target_combo.addItems(["全部", "可铳", "不可铳", "振听过滤掉"])
            elif use_tenpai:
                self.sample_target_combo.addItems(["全部", "未听牌", "听牌"])
            else:
                any_single = any(not pr.get("is_combo", True) for pr in pr_list) if pr_list else True
                if result.get("multi_pattern") and any_single:
                    self.sample_target_combo.addItems(["全部", "有0张", "有1张", "有2张", "有3张"])
                elif result.get("is_combo", False) and not result.get("multi_pattern"):
                    self.sample_target_combo.addItems(["全部", "没有", "有"])
                else:
                    self.sample_target_combo.addItems(["全部", "有0张", "有1张", "有2张", "有3张"])

            is_combo = result.get('is_combo', False)
            multi = result.get('multi_pattern', False)
            pr_list = result.get('pattern_results', []) if multi else []

            if multi and pr_list:
                use_tenpai = (result.get("analysis_target") == "tenpai")
                use_instant = (result.get("analysis_target") == "deal_in_instant")
                lines = ["查询完成！\n", f"总匹配数: {_fmt_int(result['total_matches'])}\n"]
                for pr in pr_list:
                    pr_label = f"{pr['pattern_str']} → 听牌" if use_tenpai else f"{pr['pattern_str']} → {pr['target']}"
                    tcd = pr.get('target_count_distribution', {})
                    lines.append(f"  {pr_label}: {_fmt_int(pr['matches'])} 次")
                    if use_instant:
                        continue
                    elif use_tenpai:
                        lines.append(
                            f"    未听牌: {pr['probability_distribution'][0]:.1f}% ({_fmt_int(tcd.get(0, 0))} 例)  "
                            f"听牌: {pr['probability_distribution'][1]:.1f}% ({_fmt_int(tcd.get(1, 0))} 例)"
                        )
                    elif pr.get('multi_target') and pr.get('target_tiles'):
                        for tk in pr['target_tiles']:
                            pd = pr['probability_distribution'].get(tk, {})
                            td = tcd.get(tk, {}) if isinstance(tcd, dict) else {}
                            lines.append(
                                f"    {tk}: 0张 {pd.get(0,0):.1f}% ({_fmt_int(td.get(0,0))} 例) 1张 {pd.get(1,0):.1f}% ({_fmt_int(td.get(1,0))} 例) "
                                f"2张 {pd.get(2,0):.1f}% ({_fmt_int(td.get(2,0))} 例) 3张 {pd.get(3,0):.1f}% ({_fmt_int(td.get(3,0))} 例)"
                            )
                    elif pr['is_combo']:
                        lines.append(
                            f"    没有: {pr['probability_distribution'][0]:.1f}% ({_fmt_int(tcd.get(0, 0))} 例)  "
                            f"有: {pr['probability_distribution'][1]:.1f}% ({_fmt_int(tcd.get(1, 0))} 例)"
                        )
                    else:
                        lines.append(
                            f"    有0张: {pr['probability_distribution'][0]:.1f}% ({_fmt_int(tcd.get(0, 0))} 例)  "
                            f"有1张: {pr['probability_distribution'][1]:.1f}% ({_fmt_int(tcd.get(1, 0))} 例)  "
                            f"有2张: {pr['probability_distribution'][2]:.1f}% ({_fmt_int(tcd.get(2, 0))} 例)  "
                            f"有3张: {pr['probability_distribution'][3]:.1f}% ({_fmt_int(tcd.get(3, 0))} 例)"
                        )
                lines.extend([
                    f"\n分析半庄数: {_fmt_int(result['total_logs_analyzed'])}",
                    f"巡目范围: {result.get('turn_range', '不限')}",
                    f"等价变体数: {result['variants_count']}",
                    f"分析耗时: {result.get('elapsed_seconds', 0):.1f} 秒",
                ])
                if use_instant:
                    lines.extend([
                        "",
                        f"即时铳率: {result.get('deal_in_rate', 0.0):.2%}",
                        f"可铳样本: {_fmt_int(result.get('deal_in_hits', 0))}",
                        f"平均铳点: {result.get('deal_in_point_avg', 0.0):.1f}",
                        f"铳度: {result.get('deal_in_intensity', 0.0):.2f}",
                    ])
                result_text = "\n".join(lines)

                # 多模式：勾选与合并（_setup_multi_pattern_merge 内 _update_merged_result 已设置 _excel_clipboard_text 为合并结果）
                if use_instant:
                    self.multi_merge_widget.setVisible(False)
                    self._excel_clipboard_text = self._build_excel_text(result, None, multi=False)
                else:
                    self._setup_multi_pattern_merge(result, pr_list)
            else:
                use_tenpai = (result.get("analysis_target") == "tenpai")
                use_instant = (result.get("analysis_target") == "deal_in_instant")
                multi_target = result.get("multi_target", False)
                if use_instant:
                    dist_text = (
                        f"  总样本: {_fmt_int(result.get('total_matches', 0))}\n"
                        f"  可铳样本: {_fmt_int(result.get('deal_in_hits', 0))}\n"
                        f"  即时铳率: {result.get('deal_in_rate', 0.0):.2%}\n"
                        f"  平均铳点: {result.get('deal_in_point_avg', 0.0):.1f}\n"
                        f"  铳度: {result.get('deal_in_intensity', 0.0):.2f}"
                    )
                    target_label = f"目标: {result['target_tile']} (即时铳率)"
                elif use_tenpai:
                    dist_text = (
                        f"  未听牌: {result['probability_distribution'][0]:.2f}% ({_fmt_int(result['target_count_distribution'][0])} 例)\n"
                        f"  听牌: {result['probability_distribution'][1]:.2f}% ({_fmt_int(result['target_count_distribution'][1])} 例)"
                    )
                    target_label = "分析目标: 听牌"
                elif multi_target:
                    tcd = result.get('target_count_distribution', {})
                    prob_d = result.get('probability_distribution', {})
                    lines = []
                    for tk in result.get('target_tiles', []):
                        pd = prob_d.get(tk, {})
                        td = tcd.get(tk, {})
                        lines.append(
                            f"  {tk}: 0张 {pd.get(0,0):.2f}% 1张 {pd.get(1,0):.2f}% "
                            f"2张 {pd.get(2,0):.2f}% 3张 {pd.get(3,0):.2f}%"
                        )
                    dist_text = "\n".join(lines)
                    target_label = f"目标: {result['target_tile']} (多目标)"
                elif is_combo:
                    dist_text = (
                        f"  没有: {result['probability_distribution'][0]:.2f}% ({_fmt_int(result['target_count_distribution'][0])} 例)\n"
                        f"  有: {result['probability_distribution'][1]:.2f}% ({_fmt_int(result['target_count_distribution'][1])} 例)"
                    )
                    target_label = f"目标: {result['target_tile']} (搭子)"
                else:
                    dist_text = (
                        f"  有0张: {result['probability_distribution'][0]:.2f}% ({_fmt_int(result['target_count_distribution'][0])} 例)\n"
                        f"  有1张: {result['probability_distribution'][1]:.2f}% ({_fmt_int(result['target_count_distribution'][1])} 例)\n"
                        f"  有2张: {result['probability_distribution'][2]:.2f}% ({_fmt_int(result['target_count_distribution'][2])} 例)\n"
                        f"  有3张: {result['probability_distribution'][3]:.2f}% ({_fmt_int(result['target_count_distribution'][3])} 例)"
                    )
                    target_label = f"目标: {result['target_tile']}"
                result_text = f"""
查询完成！

{target_label}
匹配状态数: {_fmt_int(result['total_matches'])}

概率分布:
{dist_text}

分析半庄数: {_fmt_int(result['total_logs_analyzed'])}
查询模式: {result['query_pattern_str']}
巡目范围: {result.get('turn_range', '不限')}
等价变体数: {result['variants_count']}
分析耗时: {result.get('elapsed_seconds', 0):.1f} 秒
"""
                result_text = result_text.strip()
                self.multi_merge_widget.setVisible(False)
                self._excel_clipboard_text = self._build_excel_text(result, None, multi=False)

            self.result_text.setText(result_text)
            self.copy_excel_btn.setEnabled(True)
            self.save_archive_btn.setEnabled(True)
        else:
            self.gen_sample_btn.setEnabled(False)
            self.save_archive_btn.setEnabled(False)
            self.last_query_params = None
            self.last_query_result = None
            self.copy_excel_btn.setEnabled(False)
            self.matrix_display_btn.setEnabled(False)
            self._excel_clipboard_text = ""
            self.multi_merge_widget.setVisible(False)
            QMessageBox.critical(self, "查询失败", f"查询失败: {result}")


def main():
    # 抑制 OpenGL/pyglet 的已知无害警告
    import warnings
    import logging
    warnings.filterwarnings("ignore", message="Could not set COM MTA mode")
    logging.getLogger("OpenGL.acceleratesupport").setLevel(logging.WARNING)
    """主函数"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 创建应用
    app = QApplication(sys.argv)
    app.setApplicationName("TenhouHandreading")
    app.setOrganizationName("TenhouHandreading")
    app.setStyleSheet(_get_app_stylesheet())

    # 创建主窗口
    window = MainWindow()
    window.show()
    
    # 运行应用
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
