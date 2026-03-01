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
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QRect, QRectF, QSettings, QTimer
from PyQt5.QtGui import QPainter, QColor, QBrush, QPen, QFont

from .data_downloader import DataDownloader
from .live_analyzer import LiveAnalyzer, get_database_stats, format_samples_for_display
from .simple_normalizer import split_discard_pattern
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
    QTabWidget::pane { border: 1px solid #30363d; border-radius: 6px; }
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
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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


class SampleThread(QThread):
    """样本收集线程"""
    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)
    finished = pyqtSignal(bool, object)  # success, samples list

    def __init__(self, analyzer: LiveAnalyzer, params: dict, sample_count: int, target_count_filter,
                 sample_pool=None, target_tile_filter=None):
        super().__init__()
        self.analyzer = analyzer
        self.params = params
        self.sample_count = sample_count
        self.target_count_filter = target_count_filter
        self.sample_pool = sample_pool  # 主统计时预收集的样本池，有则无需二次遍历
        self.target_tile_filter = target_tile_filter  # 多目标时指定按哪个目标筛选
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


def _load_archive(db_path: str) -> List[Dict[str, Any]]:
    """加载存档列表"""
    path = _archive_file_path(db_path)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items", [])
    except Exception:
        return []


def _save_archive(db_path: str, items: List[Dict[str, Any]]) -> None:
    """保存存档列表"""
    path = _archive_file_path(db_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise RuntimeError(f"保存存档失败: {e}")


def _build_pattern_summary(result: dict) -> str:
    """从查询结果提取舍牌模式摘要，用于列表展示与搜索"""
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


class TileIllustrationDialog(QDialog):
    """麻将示意图生成对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("麻将示意图")
        self.setMinimumSize(520, 420)
        self._current_image = None
        layout = QVBoxLayout(self)

        # 输入区
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("符号输入:"))
        self.notation_edit = QLineEdit()
        self.notation_edit.setPlaceholderText("例: 4mc3m5m（用3m5m吃4m）、p1z1z（碰东）、7s-?-9s（?=未知牌）")
        self.notation_edit.setMinimumWidth(260)
        self.notation_edit.returnPressed.connect(self._generate)
        input_row.addWidget(self.notation_edit, 1)
        self.gen_btn = QPushButton("生成")
        self.gen_btn.clicked.connect(self._generate)
        input_row.addWidget(self.gen_btn)
        layout.addLayout(input_row)

        hint = QLabel("支持：?（单独大问号）、7s-?-9s（? 为问号牌）、4mc3m5m、p1z1z、7s-9s 等")
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
        if not notation:
            return
        try:
            from PyQt5.QtGui import QPixmap
            from PyQt5.QtCore import QByteArray, QBuffer
            img = render_illustration_to_qimage(notation, scale=3.0)
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
        base = re.sub(r'[\\/:*?"<>|]', "_", self.notation_edit.text().strip().replace("-", "_")[:30])
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
                              if k in ("dora_constraint", "riichi_constraint", "call_constraint",
                                       "call_area_constraints", "visible_constraints", "sample_limit",
                                       "total_logs_hint", "analysis_batch_size", "exclude_south4",
                                       "prior_discard_exclusion", "max_workers")}
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

        # 场况约束
        vc_row = QHBoxLayout()
        vc_row.addWidget(QLabel("场况约束:"))
        self.batch_use_main_constraints = QCheckBox("使用主界面场况约束")
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
            "prior_discard_exclusion": prior_excl,
            "max_workers": mw.max_workers_spin.value(),
        }
        if self.constraint_group.isChecked():
            dora = "dora_unrelated" if self.batch_dora_irrelevant.isChecked() else (self.batch_dora_input.text().strip() or "any")
            riichi = "any" if self.batch_riichi_any.isChecked() else ("has_riichi" if self.batch_riichi_has.isChecked() else "no_riichi")
            call = "any" if self.batch_call_any.isChecked() else ("has_call" if self.batch_call_has.isChecked() else "no_call")
            call_area = [le.text().strip() for le in self.batch_call_area_inputs if le.text().strip()][:4]
            visible = {}
            if self.batch_use_main_constraints.isChecked():
                for i in range(mw.constraint_list.count()):
                    item = mw.constraint_list.item(i)
                    tile, min_c, max_c = item.data(Qt.UserRole)
                    visible[tile] = (min_c, max_c)
            base.update(dora_constraint=dora, riichi_constraint=riichi, call_constraint=call,
                call_area_constraints=call_area if call_area else None, visible_constraints=visible if visible else None)
        else:
            dora = "dora_unrelated" if mw.dora_irrelevant_radio.isChecked() else (mw.dora_tile_input.text().strip() or "any")
            riichi = "any" if mw.riichi_any_radio.isChecked() else ("has_riichi" if mw.riichi_has_radio.isChecked() else "no_riichi")
            call = "any" if mw.call_any_radio.isChecked() else ("has_call" if mw.call_has_radio.isChecked() else "no_call")
            call_area = [le.text().strip() for le in mw.call_area_inputs if le.text().strip()][:4]
            visible = {}
            for i in range(mw.constraint_list.count()):
                item = mw.constraint_list.item(i)
                tile, min_c, max_c = item.data(Qt.UserRole)
                visible[tile] = (min_c, max_c)
            base.update(dora_constraint=dora, riichi_constraint=riichi, call_constraint=call,
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
        self._excel_clipboard_text = ""  # 当前结果的 Excel 格式文本
        self._pattern_checkboxes = []  # 多模式勾选框列表
        self._archive_entries: List[Dict[str, Any]] = []  # 存档条目列表

        self.init_ui()
        self._archive_entries = _load_archive(self.db_path)
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
        
        # 1. 数据管理区
        data_group = self._create_data_management_group()
        main_layout.addWidget(data_group)
        
        # 2. 查询输入区
        query_group = self._create_query_input_group()
        main_layout.addWidget(query_group)
        
        # 3. 结果显示区
        result_group = self._create_result_display_group()
        main_layout.addWidget(result_group)
    
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
        self.analysis_batch_size_spin.setRange(500, 10000)
        self.analysis_batch_size_spin.setSingleStep(500)
        saved_batch = QSettings().value("analysis_batch_size", 2000, type=int)
        self.analysis_batch_size_spin.blockSignals(True)
        self.analysis_batch_size_spin.setValue(max(500, min(10000, saved_batch or 2000)))
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
        group = QGroupBox("查询条件")
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
        right_widget.setMaximumWidth(400)
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
        
        # 宝牌 / 立直 / 副露 各占一行，避免文字重叠
        dora_row = QHBoxLayout()
        dora_row.setSpacing(10)
        self.dora_group = QButtonGroup()
        self.dora_irrelevant_radio = QRadioButton("宝牌无关")
        self.dora_irrelevant_radio.setChecked(True)
        self.dora_group.addButton(self.dora_irrelevant_radio, 0)
        self.dora_specific_radio = QRadioButton("宝牌为")
        self.dora_group.addButton(self.dora_specific_radio, 1)
        self.dora_tile_input = QLineEdit()
        self.dora_tile_input.setPlaceholderText("例: 6s")
        self.dora_tile_input.setMinimumWidth(50)
        self.dora_tile_input.setMaximumWidth(70)
        dora_row.addWidget(self.dora_irrelevant_radio)
        dora_row.addWidget(self.dora_specific_radio)
        dora_row.addWidget(self.dora_tile_input)
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
        exclude_south4_row = QHBoxLayout()
        self.exclude_south4_check = QCheckBox("不考虑南四局")
        self.exclude_south4_check.setToolTip("南四局打法会根据点数状况有极大改变，勾选时跳过南四局")
        exclude_south4_row.addWidget(self.exclude_south4_check)
        exclude_south4_row.addStretch()
        right_layout.addLayout(exclude_south4_row)
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
        
        # 场况约束（两行：输入行 + 按钮与列表）
        right_layout.addSpacing(4)
        add_constraint_row = QHBoxLayout()
        add_constraint_row.setSpacing(8)
        add_constraint_row.addWidget(QLabel("场况约束"))
        self.constraint_tile_input = QLineEdit()
        self.constraint_tile_input.setPlaceholderText("牌，如 8s")
        self.constraint_tile_input.setMinimumWidth(52)
        self.constraint_tile_input.setMaximumWidth(72)
        add_constraint_row.addWidget(self.constraint_tile_input)
        add_constraint_row.addWidget(QLabel("可见"))
        self.constraint_range_slider = DiscreteRangeSlider()
        self.constraint_range_slider.setMinimumWidth(100)
        self.constraint_range_slider.setMaximumWidth(140)
        add_constraint_row.addWidget(self.constraint_range_slider)
        add_constraint_row.addWidget(QLabel("枚"))
        add_constraint_row.addStretch()
        right_layout.addLayout(add_constraint_row)
        add_btn_row_constraint = QHBoxLayout()
        self.add_constraint_btn = QPushButton("添加场况约束")
        self.add_constraint_btn.clicked.connect(self.add_constraint)
        add_btn_row_constraint.addWidget(self.add_constraint_btn)
        add_btn_row_constraint.addStretch()
        right_layout.addLayout(add_btn_row_constraint)
        self.constraint_list = QListWidget()
        self.constraint_list.setMinimumHeight(52)
        self.constraint_list.setMaximumHeight(88)
        right_layout.addWidget(self.constraint_list)
        
        # 分析目标 + 样本上限 + 匹配状态保留条数
        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("分析目标:"))
        self.analysis_target_combo = QComboBox()
        self.analysis_target_combo.addItem("目标牌存量", "target_count")
        self.analysis_target_combo.addItem("是否听牌", "tenpai")
        self.analysis_target_combo.setToolTip("目标牌存量：统计手牌中目标牌数量；是否听牌：统计匹配时已听牌/未听牌比例")
        opts_row.addWidget(self.analysis_target_combo)
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
        self.sample_limit_input.setMaximumWidth(120)
        opts_row.addWidget(self.sample_limit_input)
        opts_row.addWidget(QLabel("匹配保留:"))
        self.matched_states_cap_spin = QSpinBox()
        self.matched_states_cap_spin.setRange(1, 500)
        saved_cap = QSettings().value("matched_states_cap", 100, type=int) or 100
        self.matched_states_cap_spin.blockSignals(True)
        self.matched_states_cap_spin.setValue(max(1, min(500, saved_cap)))
        self.matched_states_cap_spin.blockSignals(False)
        self.matched_states_cap_spin.setToolTip("分析时最多保留的匹配状态条数（用于展示，越大占内存越多）")
        self.matched_states_cap_spin.valueChanged.connect(self._save_matched_states_cap)
        self.matched_states_cap_spin.setMaximumWidth(64)
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
        target_help_btn = QPushButton("?")
        target_help_btn.setToolTip("目标牌说明")
        target_help_btn.setFixedWidth(24)
        target_help_btn.clicked.connect(self._show_target_help)
        del_btn = QPushButton("删除")
        del_btn.setMaximumWidth(50)
        row.addWidget(QLabel("模式:"))
        row.addWidget(pattern_edit, 1)
        row.addWidget(QLabel("→"))
        row.addWidget(target_edit)
        row.addWidget(target_help_btn)
        row.addWidget(del_btn)
        entry = (pattern_edit, target_edit, del_btn, row)
        self._pattern_row_widgets.append(entry)
        del_btn.clicked.connect(lambda checked=False, e=entry: self._remove_pattern_row(e))
        self.pattern_rows_layout.addLayout(row)

    def _remove_pattern_row(self, entry):
        """移除一行舍牌模式"""
        if len(self._pattern_row_widgets) <= 1:
            QMessageBox.warning(self, "提示", "至少需保留一个舍牌模式")
            return
        pattern_edit, target_edit, del_btn, row = entry
        while row.count():
            item = row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pattern_rows_layout.removeItem(row)
        if entry in self._pattern_row_widgets:
            self._pattern_row_widgets.remove(entry)

    def _get_pattern_items(self, require_target: bool = True) -> List[Tuple[List[str], str]]:
        """从界面获取所有 (pattern, target) 对。require_target=False 时（听牌模式）目标可为空，以 5z 占位"""
        items = []
        placeholder = "5z"  # 听牌模式无目标牌时占位，仅用于等价变体生成
        for pattern_edit, target_edit, _, _ in self._pattern_row_widgets:
            pt = pattern_edit.text().strip()
            tg = target_edit.text().strip()
            if pt and (tg or not require_target):
                items.append((split_discard_pattern(pt), tg or placeholder))
        return items

    def _show_pattern_help(self):
        """显示舍牌模式输入说明"""
        PatternHelpDialog(self).exec_()

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

    def _show_tile_illustration_dialog(self):
        """显示麻将示意图生成对话框"""
        TileIllustrationDialog(self).exec_()

    def _show_batch_chart_dialog(self):
        """显示批量折线图数据生成对话框"""
        BatchChartDialog(self).exec_()

    def _create_result_display_group(self) -> QGroupBox:
        """创建结果显示区"""
        group = QGroupBox("查询结果")
        main_layout = QVBoxLayout()

        splitter = QSplitter(Qt.Horizontal)

        # 左侧：结果展示区
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏：复制到 Excel、麻将示意图
        tool_row = QHBoxLayout()
        self.copy_excel_btn = QPushButton("复制到 Excel")
        self.copy_excel_btn.setToolTip("复制分析结果为 Tab 分隔格式，可直接粘贴到 Excel 中计算")
        self.copy_excel_btn.clicked.connect(self._copy_result_to_excel)
        self.copy_excel_btn.setEnabled(False)
        tool_row.addWidget(self.copy_excel_btn)
        self.tile_illustration_btn = QPushButton("麻将示意图")
        self.tile_illustration_btn.setToolTip("根据舍牌/副露符号生成示意图图片，如 4mc3m5m")
        self.tile_illustration_btn.clicked.connect(self._show_tile_illustration_dialog)
        tool_row.addWidget(self.tile_illustration_btn)
        self.batch_chart_btn = QPushButton("生成折线图数据")
        self.batch_chart_btn.setToolTip("批量分析多个舍牌模式×巡目范围，生成折线图用 Excel 表格")
        self.batch_chart_btn.clicked.connect(self._show_batch_chart_dialog)
        tool_row.addWidget(self.batch_chart_btn)
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

        # 右侧：存档管理区
        archive_widget = QGroupBox("存档管理")
        archive_layout = QVBoxLayout()

        self.save_archive_btn = QPushButton("存档当前结果")
        self.save_archive_btn.setToolTip("将当前查询结果保存到本地，避免闪退丢失")
        self.save_archive_btn.clicked.connect(self._save_current_to_archive)
        self.save_archive_btn.setEnabled(False)
        archive_layout.addWidget(self.save_archive_btn)

        archive_layout.addWidget(QLabel("搜索舍牌模式:"))
        self.archive_search_edit = QLineEdit()
        self.archive_search_edit.setPlaceholderText("输入关键词筛选...")
        self.archive_search_edit.textChanged.connect(self._filter_archive_list)
        archive_layout.addWidget(self.archive_search_edit)

        self.archive_list = QListWidget()
        self.archive_list.setMinimumWidth(240)
        self.archive_list.setToolTip("双击条目展开查看完整结果")
        self.archive_list.itemDoubleClicked.connect(self._on_archive_item_clicked)
        archive_layout.addWidget(self.archive_list)

        del_archive_btn = QPushButton("删除选中")
        del_archive_btn.clicked.connect(self._delete_selected_archive)
        archive_layout.addWidget(del_archive_btn)

        archive_widget.setLayout(archive_layout)
        splitter.addWidget(archive_widget)
        splitter.setSizes([600, 280])

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

    def _save_current_to_archive(self):
        """将当前查询结果保存到存档"""
        if not self.last_query_result:
            QMessageBox.warning(self, "提示", "当前无查询结果可存档")
            return
        pattern_summary = _build_pattern_summary(self.last_query_result)
        entry = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S") + "_" + str(len(self._archive_entries)),
            "created_at": datetime.now().isoformat(),
            "pattern_summary": pattern_summary,
            "result_display_text": self.result_text.toPlainText(),
            "excel_text": self._excel_clipboard_text,
        }
        self._archive_entries.insert(0, entry)
        try:
            _save_archive(self.db_path, self._archive_entries)
            self._refresh_archive_list()
            tip = pattern_summary[:50] + ("..." if len(pattern_summary) > 50 else "")
            QMessageBox.information(self, "存档成功", f"已保存: {tip}")
        except Exception as e:
            QMessageBox.critical(self, "存档失败", str(e))

    def _refresh_archive_list(self):
        """刷新存档列表显示"""
        self.archive_list.clear()
        for i, e in enumerate(self._archive_entries):
            summary = e.get("pattern_summary", "")
            time_str = e.get("created_at", "")[:19].replace("T", " ")
            item = QListWidgetItem(f"{summary}\n  {time_str}")
            item.setData(Qt.UserRole, i)
            self.archive_list.addItem(item)

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
            _save_archive(self.db_path, self._archive_entries)
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

        cb_row = QHBoxLayout()
        use_tenpai = self.last_query_result and self.last_query_result.get("analysis_target") == "tenpai"
        cb_row.addWidget(QLabel("勾选合并（目标牌相同）:" if not use_tenpai else "勾选合并:"))
        for i, pr in enumerate(pr_list):
            lbl = f"{pr['pattern_str']} → 听牌" if use_tenpai else f"{pr['pattern_str']} → {pr['target']}"
            cb = QCheckBox(lbl)
            cb.setChecked(True)
            cb.setProperty("idx", i)
            cb.stateChanged.connect(lambda *_: self._update_merged_result(result, pr_list))
            self._pattern_checkboxes.append(cb)
            cb_row.addWidget(cb)
        select_all_btn = QPushButton("全选")
        select_all_btn.setFixedWidth(44)
        select_all_btn.clicked.connect(lambda: self._set_all_pattern_checks(True, result, pr_list))
        cb_row.addWidget(select_all_btn)
        cb_row.addStretch()
        self.multi_merge_layout.addLayout(cb_row)

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
    
    def add_constraint(self):
        """添加场况约束"""
        tile = self.constraint_tile_input.text().strip()
        min_count, max_count = self.constraint_range_slider.getRange()
        
        if not tile:
            QMessageBox.warning(self, "输入错误", "请输入牌")
            return
        
        if min_count > max_count:
            QMessageBox.warning(self, "输入错误", "最小值不能大于最大值")
            return
        
        # 添加到列表
        item_text = f"{tile} 可见 {min_count}-{max_count} 枚"
        item = QListWidgetItem(item_text)
        item.setData(Qt.UserRole, (tile, min_count, max_count))
        self.constraint_list.addItem(item)
        
        # 清空输入
        self.constraint_tile_input.clear()
    
    def execute_query(self):
        """执行查询"""
        analysis_target = self.analysis_target_combo.currentData() or "target_count"
        use_tenpai = (analysis_target == "tenpai")
        query_items = self._get_pattern_items(require_target=not use_tenpai)
        if not query_items:
            msg = "请至少输入一个舍牌模式" + ("" if use_tenpai else "和对应的目标牌")
            QMessageBox.warning(self, "输入错误", msg)
            return
        first_pattern, first_target = query_items[0]
        
        # 宝牌约束
        if self.dora_irrelevant_radio.isChecked():
            dora_constraint = "dora_unrelated"
        else:
            dora_tile = self.dora_tile_input.text().strip()
            if not dora_tile:
                QMessageBox.warning(self, "输入错误", "请输入宝牌")
                return
            dora_constraint = dora_tile
        
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
        
        # 场况约束
        visible_constraints = {}
        for i in range(self.constraint_list.count()):
            item = self.constraint_list.item(i)
            tile, min_count, max_count = item.data(Qt.UserRole)
            visible_constraints[tile] = (min_count, max_count)
        
        # 样本上限
        sample_limit = self.sample_limit_input.value()
        
        # 巡目范围（1-18，1-18 表示不限制）
        turn_min, turn_max = self.turn_range_slider.getRange()
        turn_range = (turn_min, turn_max) if (turn_min <= turn_max and not (turn_min == 1 and turn_max == 18)) else None
        
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
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "call_area_constraints": call_area_constraints if call_area_constraints else None,
            "turn_range": turn_range,
            "sample_limit": sample_limit,
            "total_logs_hint": total_logs_hint,
            "matched_states_cap": matched_states_cap,
            "analysis_batch_size": analysis_batch_size,
            "exclude_south4": self.exclude_south4_check.isChecked(),
            "prior_discard_exclusion": self.prior_discard_exclusion_input.text().strip() or None,
            "max_workers": self.max_workers_spin.value(),
        }
        
        # 启动查询线程
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        
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
        # 听牌模式：全部/未听牌/听牌；搭子模式：全部/没有/有；单张模式：全部/有0张~有3张
        use_tenpai = self.last_query_params.get("analysis_target") == "tenpai"
        is_combo = self.last_query_params.get("is_combo", False)
        if use_tenpai:
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
            self.last_query_result = result
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
            visible_constraints = {}
            for i in range(self.constraint_list.count()):
                item = self.constraint_list.item(i)
                tile, min_count, max_count = item.data(Qt.UserRole)
                visible_constraints[tile] = (min_count, max_count)
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
                "visible_constraints": visible_constraints if visible_constraints else None,
                "riichi_constraint": riichi_constraint,
                "call_constraint": call_constraint,
                "call_area_constraints": call_area_constraints,
                "turn_range": turn_range,
                "sample_limit": self.sample_limit_input.value(),
                "is_combo": result.get("is_combo", False),
                "total_logs_hint": total_logs_hint,
                "exclude_south4": self.exclude_south4_check.isChecked(),
                "prior_discard_exclusion": self.prior_discard_exclusion_input.text().strip() or None,
            }
            self.gen_sample_btn.setEnabled(True)
            # 舍牌模式选择：多模式时列出每个模式供生成样本时选择
            self.sample_pattern_combo.clear()
            pr_list = result.get("pattern_results", []) if result.get("multi_pattern") else []
            use_tenpai = (result.get("analysis_target") == "tenpai")
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
            if use_tenpai:
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
                lines = ["查询完成！\n", f"总匹配数: {_fmt_int(result['total_matches'])}\n"]
                for pr in pr_list:
                    pr_label = f"{pr['pattern_str']} → 听牌" if use_tenpai else f"{pr['pattern_str']} → {pr['target']}"
                    tcd = pr.get('target_count_distribution', {})
                    lines.append(f"  {pr_label}: {_fmt_int(pr['matches'])} 次")
                    if use_tenpai:
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
                result_text = "\n".join(lines)

                # 多模式：勾选与合并（_setup_multi_pattern_merge 内 _update_merged_result 已设置 _excel_clipboard_text 为合并结果）
                self._setup_multi_pattern_merge(result, pr_list)
            else:
                use_tenpai = (result.get("analysis_target") == "tenpai")
                multi_target = result.get("multi_target", False)
                if use_tenpai:
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
            self.save_archive_btn.setEnabled(False)
            self._excel_clipboard_text = ""
            self.multi_merge_widget.setVisible(False)
            QMessageBox.critical(self, "查询失败", f"查询失败: {result}")


def main():
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
