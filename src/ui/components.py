"""
GUI 通用组件

抽离自 gui_app.py 的自定义控件：RangeSlider、DiscreteRangeSlider、TileIllustrationWidget。
"""

import re
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QFileDialog, QSizePolicy,
)
from PyQt5.QtCore import Qt, pyqtSignal, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QFont

from ..tile_illustration import render_illustration_to_qimage


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
        from PyQt5.QtWidgets import QSizePolicy
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
