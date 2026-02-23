"""
GUI主程序

使用 PyQt5 开发桌面界面
"""

import sys
import json
import logging
from pathlib import Path
from typing import List, Tuple, Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QTextBrowser, QGroupBox,
    QSpinBox, QRadioButton, QButtonGroup, QProgressBar,
    QMessageBox, QFormLayout, QListWidget, QListWidgetItem,
    QDialog, QComboBox, QDialogButtonBox, QSizePolicy
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QRect, QRectF, QSettings, QTimer
from PyQt5.QtGui import QPainter, QColor, QBrush, QPen, QFont

from .data_downloader import DataDownloader
from .live_analyzer import LiveAnalyzer, get_database_stats, format_samples_for_display
logger = logging.getLogger(__name__)


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

        # 轨道背景
        track_rect = QRectF(self._handle_radius, track_y,
                            self.width() - 2 * self._handle_radius, self._track_height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(200, 200, 210))
        painter.drawRoundedRect(track_rect, 2, 2)

        # 选中范围高亮
        pos_min = self._valueToPos(self._value_min)
        pos_max = self._valueToPos(self._value_max)
        hl_rect = QRectF(pos_min - self._handle_radius, track_y,
                         pos_max - pos_min, self._track_height)
        painter.setBrush(QColor(70, 130, 180))
        painter.drawRoundedRect(hl_rect, 2, 2)

        # 两个圆形手柄
        painter.setBrush(QColor(70, 130, 180))
        painter.setPen(QPen(QColor(50, 100, 150), 1))
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
                    self.progress.emit(f"正在分析: {current:,}/{total:,} ({percent:.1f}%)")
                else:
                    self.progress.emit(f"正在分析: 已扫描 {current:,} 场")
            
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

    def __init__(self, analyzer: LiveAnalyzer, params: dict, sample_count: int, target_count_filter):
        super().__init__()
        self.analyzer = analyzer
        self.params = params
        self.sample_count = sample_count
        self.target_count_filter = target_count_filter
        self._should_cancel = False

    def cancel(self):
        self._should_cancel = True

    def run(self):
        try:
            self.progress.emit("正在收集验证样本...")
            def progress_cb(cur, total):
                self.progress_num.emit(cur, total)
                self.progress.emit(f"已扫描: {cur:,} 场" + (f"/{total:,}" if total > 0 else ""))
            # 排除 collect_verification_samples 不接受的参数
            params = {k: v for k, v in self.params.items()
                      if k not in ("query_pattern_str", "is_combo")}
            samples = self.analyzer.collect_verification_samples(
                **params,
                sample_count=self.sample_count,
                target_count_filter=self.target_count_filter,
                progress_callback=progress_cb,
                should_cancel=lambda: self._should_cancel
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
        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(text)
        self.text_edit.setReadOnly(True)
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


PATTERN_HELP_HTML = """
<h2>舍牌模式输入说明</h2>
<p>用 <b>-</b> 分隔各张牌，按出牌顺序从左到右书写。</p>

<h3>一、数牌</h3>
<ul>
<li><b>1m～9m</b> 萬子、<b>1p～9p</b> 筒子、<b>1s～9s</b> 索子</li>
<li><b>赤五</b>：<code>0m</code> 赤5万、<code>0p</code> 赤5筒、<code>0s</code> 赤5索；参与花色变换，0m/0p/0s 三者与数牌一起等价（如 3m-2m-0m ≡ 7s-8s-0s）；含赤五时等价变体为 9+9 个</li>
<li>等价：1s-3s 与 1m-3m、9s-7s 等自动等价（花色与镜像）</li>
</ul>

<h3>二、摸切标记 t</h3>
<ul>
<li>牌后加 <b>t</b> 表示该牌必须为摸切，无 t 为手切</li>
<li>例：<code>3mt-1m</code> = 3m 摸切、1m 手切</li>
</ul>

<h3>三、通配符 * 与 $</h3>
<ul>
<li><b>*</b> 表示“任意摸切”，可匹配中间任意张摸切牌</li>
<li>例：<code>3m-*-1m</code> = 3m 与 1m 之间允许若干摸切，不允许手切</li>
<li><b>$</b> 表示“任意一张手切”（吃/碰之后的牌必为手切）</li>
<li>例：<code>c0p6p-$</code> = 用 0p6p 吃后打出任意一张手切牌</li>
</ul>

<h3>四、吃 / 碰占位</h3>
<p>在舍牌序列中表示“此处有一次吃或碰”，不占一张舍牌；<b>语义为具体吃的/碰的牌</b>，如 <code>4mc3m5m</code> 表示必须是用 3m5m 吃的 4m。</p>
<ul>
<li><b>吃</b>：任意 <code>(牌)c(牌)(牌)</code> 或 <code>c(牌)(牌)</code>，如 <code>1sc2s3s</code>、<code>4mc3m5m</code>、<code>c5m6m</code></li>
<li><b>碰</b>：<code>p1z1z</code> 用两个东碰（只碰东）；<code>pkfkf</code> 用客风碰</li>
<li><b>等价变体</b>：含吃或数牌碰时<u>不</u>生成花色/镜像等价变体（如 7s-4mc3m5m-9s 不考虑 7p-4mc3m5m-9p）；仅碰字牌时仍可生成变体</li>
</ul>

<h3>五、字牌</h3>
<p><b>1z～7z</b> 对应：东、南、西、北、白、发、中</p>

<h3>六、字牌占位符</h3>
<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">
<tr><th>符号</th><th>含义</th></tr>
<tr><td><code>z</code></td><td>任意字牌（手切）</td></tr>
<tr><td><code>zt</code></td><td>任意字牌且摸切</td></tr>
<tr><td><code>zf</code></td><td>自风（当前局座风）</td></tr>
<tr><td><code>kf</code></td><td>任意一张客风</td></tr>
<tr><td><code>z1</code> <code>z2</code> <code>z3</code></td><td>互不相同的字牌</td></tr>
<tr><td><code>kf1</code> <code>kf2</code> <code>kf3</code></td><td>互不相同的客风</td></tr>
</table>

<h3>七、示例</h3>
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

<h3>三、示例</h3>
<ul>
<li>舍牌 <code>7s-9s</code> 目标 <code>8s</code>：听 8s 时，手牌有 0/1/2/3 张 8s 的概率</li>
<li>舍牌 <code>1s-3s</code> 目标 <code>2s</code>：听 2s 时，手牌有 2s 的概率</li>
<li>舍牌 <code>1m-3m</code> 目标 <code>1m3m</code>：已有 1m3m 搭子时，手牌是否握有该搭子</li>
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
    """将 stats 格式化为状态文本"""
    return (
        f"数据库状态: 已初始化\n"
        f"对局总数: {stats['total_logs']:,}\n"
        f"数据库大小: {stats['db_size_mb']:.2f} MB\n"
        f"分析模式: 实时解析（按需分析）"
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

        self.init_ui()
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
        
        # 数据库状态
        self.db_status_label = QLabel("数据库状态: 未初始化")
        layout.addWidget(self.db_status_label)
        
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
        """创建查询输入区"""
        group = QGroupBox("查询条件")
        layout = QFormLayout()
        
        # 舍牌模式
        pattern_row = QHBoxLayout()
        pattern_row.setSpacing(6)
        pattern_help_btn = QPushButton("?")
        pattern_help_btn.setToolTip("舍牌模式输入说明")
        pattern_help_btn.setFixedWidth(28)
        pattern_help_btn.clicked.connect(self._show_pattern_help)
        pattern_row.addWidget(pattern_help_btn)
        self.pattern_input = QLineEdit()
        self.pattern_input.setPlaceholderText("例: 7s-9s、7s-0m-9s、c0p6p-$、3m-z、p1z1z")
        pattern_row.addWidget(self.pattern_input)
        layout.addRow("舍牌模式:", pattern_row)

        # 目标牌
        target_row = QHBoxLayout()
        target_row.setSpacing(6)
        target_help_btn = QPushButton("?")
        target_help_btn.setToolTip("目标牌输入说明")
        target_help_btn.setFixedWidth(28)
        target_help_btn.clicked.connect(self._show_target_help)
        target_row.addWidget(target_help_btn)
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("例: 6s 或 1m3m（搭子）")
        target_row.addWidget(self.target_input)
        layout.addRow("目标牌:", target_row)
        
        # 巡目范围（双柄滑块，默认 1-6 巡）
        turn_layout = QHBoxLayout()
        turn_layout.setSpacing(8)
        self.turn_range_slider = RangeSlider(min_val=1, max_val=18, default_min=1, default_max=6)
        self.turn_range_label = QLabel("1-6 巡")
        self.turn_range_label.setMinimumWidth(45)
        self.turn_range_label.setStyleSheet("color: #555; font-size: 12px;")
        self.turn_range_slider.valueChanged.connect(
            lambda lo, hi: self.turn_range_label.setText(f"{lo}-{hi} 巡")
        )
        turn_layout.addWidget(self.turn_range_slider, 1)
        turn_layout.addWidget(self.turn_range_label, 0)
        layout.addRow("巡目范围:", turn_layout)
        
        # 宝牌约束
        dora_layout = QVBoxLayout()
        self.dora_group = QButtonGroup()
        
        self.dora_irrelevant_radio = QRadioButton("宝牌无关")
        self.dora_irrelevant_radio.setChecked(True)
        self.dora_group.addButton(self.dora_irrelevant_radio, 0)
        dora_layout.addWidget(self.dora_irrelevant_radio)
        
        dora_specific_layout = QHBoxLayout()
        self.dora_specific_radio = QRadioButton("宝牌为:")
        self.dora_group.addButton(self.dora_specific_radio, 1)
        dora_specific_layout.addWidget(self.dora_specific_radio)
        
        self.dora_tile_input = QLineEdit()
        self.dora_tile_input.setPlaceholderText("例: 6s")
        self.dora_tile_input.setMaximumWidth(100)
        dora_specific_layout.addWidget(self.dora_tile_input)
        dora_specific_layout.addStretch()
        
        dora_layout.addLayout(dora_specific_layout)
        layout.addRow("宝牌约束:", dora_layout)
        
        # 立直约束（新增）
        riichi_layout = QVBoxLayout()
        
        self.riichi_group = QButtonGroup()
        
        self.riichi_any_radio = QRadioButton("无限制")
        self.riichi_any_radio.setChecked(True)
        self.riichi_group.addButton(self.riichi_any_radio, 0)
        riichi_layout.addWidget(self.riichi_any_radio)
        
        self.riichi_has_radio = QRadioButton("有人立直")
        self.riichi_group.addButton(self.riichi_has_radio, 1)
        riichi_layout.addWidget(self.riichi_has_radio)
        
        self.riichi_no_radio = QRadioButton("无人立直")
        self.riichi_group.addButton(self.riichi_no_radio, 2)
        riichi_layout.addWidget(self.riichi_no_radio)
        
        layout.addRow("立直约束:", riichi_layout)
        
        # 副露约束（其他家吃/碰/杠）
        call_layout = QVBoxLayout()
        self.call_group = QButtonGroup()
        self.call_any_radio = QRadioButton("无限制")
        self.call_any_radio.setChecked(True)
        self.call_group.addButton(self.call_any_radio, 0)
        call_layout.addWidget(self.call_any_radio)
        self.call_has_radio = QRadioButton("有人副露")
        self.call_group.addButton(self.call_has_radio, 1)
        call_layout.addWidget(self.call_has_radio)
        self.call_no_radio = QRadioButton("无人副露")
        self.call_group.addButton(self.call_no_radio, 2)
        call_layout.addWidget(self.call_no_radio)
        layout.addRow("副露约束:", call_layout)
        
        # 场况约束
        constraint_layout = QVBoxLayout()
        
        add_constraint_layout = QHBoxLayout()
        self.constraint_tile_input = QLineEdit()
        self.constraint_tile_input.setPlaceholderText("牌 (如8s)")
        self.constraint_tile_input.setMaximumWidth(80)
        add_constraint_layout.addWidget(self.constraint_tile_input)
        
        add_constraint_layout.addWidget(QLabel("可见"))
        self.constraint_range_slider = DiscreteRangeSlider()
        self.constraint_range_slider.setMaximumWidth(140)
        add_constraint_layout.addWidget(self.constraint_range_slider)
        add_constraint_layout.addWidget(QLabel("枚"))
        
        self.add_constraint_btn = QPushButton("添加约束")
        self.add_constraint_btn.clicked.connect(self.add_constraint)
        add_constraint_layout.addWidget(self.add_constraint_btn)
        
        add_constraint_layout.addStretch()
        constraint_layout.addLayout(add_constraint_layout)
        
        # 约束列表
        self.constraint_list = QListWidget()
        self.constraint_list.setMaximumHeight(100)
        constraint_layout.addWidget(self.constraint_list)
        
        layout.addRow("场况约束:", constraint_layout)
        
        # 样本上限（保存上次选择）
        self.sample_limit_input = QSpinBox()
        self.sample_limit_input.setRange(100, 100000)
        self.sample_limit_input.setSingleStep(1000)
        saved_limit = QSettings().value("sample_limit", 10000, type=int)
        self.sample_limit_input.setValue(max(100, min(100000, saved_limit)))
        self.sample_limit_input.valueChanged.connect(self._save_sample_limit)
        layout.addRow("样本上限:", self.sample_limit_input)
        
        # 执行查询按钮
        self.query_btn = QPushButton("执行查询")
        self.query_btn.clicked.connect(self.execute_query)
        self.query_btn.setStyleSheet("font-size: 14pt; padding: 10px;")
        layout.addRow("", self.query_btn)
        
        group.setLayout(layout)
        return group

    def _show_pattern_help(self):
        """显示舍牌模式输入说明"""
        PatternHelpDialog(self).exec_()

    def _show_target_help(self):
        """显示目标牌输入说明"""
        TargetHelpDialog(self).exec_()

    def _create_result_display_group(self) -> QGroupBox:
        """创建结果显示区"""
        group = QGroupBox("查询结果")
        layout = QVBoxLayout()

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMinimumHeight(200)
        layout.addWidget(self.result_text)

        # 样本生成区（查询成功后可用）
        sample_layout = QHBoxLayout()
        sample_layout.addWidget(QLabel("生成验证样本:"))
        self.sample_count_spin = QSpinBox()
        self.sample_count_spin.setRange(1, 100)
        self.sample_count_spin.setValue(10)
        sample_layout.addWidget(self.sample_count_spin)
        sample_layout.addWidget(QLabel("条"))
        self.sample_target_combo = QComboBox()
        self.sample_target_combo.addItems(["全部", "有0张", "有1张", "有2张", "有3张"])
        self.sample_target_combo.setToolTip("单张模式: 有0~3张；搭子模式(执行查询后): 没有/有")
        sample_layout.addWidget(self.sample_target_combo)
        self.gen_sample_btn = QPushButton("生成样本")
        self.gen_sample_btn.clicked.connect(self.generate_samples)
        self.gen_sample_btn.setEnabled(False)
        sample_layout.addWidget(self.gen_sample_btn)
        sample_layout.addStretch()
        layout.addLayout(sample_layout)

        group.setLayout(layout)
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
        # 获取输入参数
        pattern_text = self.pattern_input.text().strip()
        target_tile = self.target_input.text().strip()
        
        if not pattern_text or not target_tile:
            QMessageBox.warning(self, "输入错误", "请输入舍牌模式和目标牌")
            return
        
        # 解析舍牌模式
        pattern = [p.strip() for p in pattern_text.split('-')]
        
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

        # 构造查询参数
        params = {
            "query_pattern": pattern,
            "target_tile": target_tile,
            "visible_constraints": visible_constraints if visible_constraints else None,
            "dora_constraint": dora_constraint,
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "turn_range": turn_range,
            "sample_limit": sample_limit,
            "total_logs_hint": total_logs_hint,
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
        
        self.query_btn.setEnabled(False)
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
        """生成验证样本"""
        if not self.last_query_params:
            QMessageBox.warning(self, "提示", "请先执行查询")
            return
        # 搭子模式：样本筛选 全部/没有/有；单张模式：全部/有0张~有3张
        is_combo = self.last_query_params.get("is_combo", False)
        if is_combo:
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

        self.gen_sample_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.result_text.append("\n正在收集验证样本...")

        self.sample_thread = SampleThread(
            self.analyzer,
            self.last_query_params,
            count,
            target_count_filter
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
            query_str = self.last_query_params.get("query_pattern_str", "-".join(self.last_query_params["query_pattern"]))
            text = format_samples_for_display(
                samples,
                query_str,
                self.last_query_params["target_tile"]
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
        self.query_btn.setText("执行查询")
        self.query_btn.clicked.disconnect()
        self.query_btn.clicked.connect(self.execute_query)

        if success:
            # 保存查询参数供生成样本使用
            pattern_text = self.pattern_input.text().strip()
            target_tile = self.target_input.text().strip()
            pattern = [p.strip() for p in pattern_text.split("-")]
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
                "query_pattern": pattern,
                "query_pattern_str": "-".join(pattern),
                "target_tile": target_tile,
                "dora_constraint": dora_constraint,
                "visible_constraints": visible_constraints if visible_constraints else None,
                "riichi_constraint": riichi_constraint,
                "call_constraint": call_constraint,
                "turn_range": turn_range,
                "sample_limit": self.sample_limit_input.value(),
                "is_combo": result.get("is_combo", False),
                "total_logs_hint": total_logs_hint,
            }
            self.gen_sample_btn.setEnabled(True)
            # 搭子模式：样本筛选显示 全部/没有/有；单张模式：显示 全部/有0张~有3张
            self.sample_target_combo.clear()
            if result.get("is_combo", False):
                self.sample_target_combo.addItems(["全部", "没有", "有"])
            else:
                self.sample_target_combo.addItems(["全部", "有0张", "有1张", "有2张", "有3张"])

            # 显示结果
            is_combo = result.get('is_combo', False)
            if is_combo:
                dist_text = (
                    f"  没有: {result['probability_distribution'][0]:.2f}% ({result['target_count_distribution'][0]:,} 例)\n"
                    f"  有: {result['probability_distribution'][1]:.2f}% ({result['target_count_distribution'][1]:,} 例)"
                )
            else:
                dist_text = (
                    f"  有0张: {result['probability_distribution'][0]:.2f}% ({result['target_count_distribution'][0]:,} 例)\n"
                    f"  有1张: {result['probability_distribution'][1]:.2f}% ({result['target_count_distribution'][1]:,} 例)\n"
                    f"  有2张: {result['probability_distribution'][2]:.2f}% ({result['target_count_distribution'][2]:,} 例)\n"
                    f"  有3张: {result['probability_distribution'][3]:.2f}% ({result['target_count_distribution'][3]:,} 例)"
                )
            result_text = f"""
查询完成！

目标: {result['target_tile']}{' (搭子)' if is_combo else ''}
匹配状态数: {result['total_matches']:,}

概率分布:
{dist_text}

分析对局数: {result['total_logs_analyzed']:,}
查询模式: {result['query_pattern_str']}
巡目范围: {result.get('turn_range', '不限')}
等价变体数: {result['variants_count']}
"""
            self.result_text.setText(result_text)
        else:
            self.gen_sample_btn.setEnabled(False)
            self.last_query_params = None
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
    
    # 创建主窗口
    window = MainWindow()
    window.show()
    
    # 运行应用
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
