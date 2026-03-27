"""
GUI主程序

使用 PyQt5 开发桌面界面
"""

import sys
import re
import html
import json
import logging
import configparser
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
from PyQt5.QtCore import Qt, QSettings, QTimer
from PyQt5.QtGui import QIntValidator

from .data_downloader import DataDownloader
from .live_analyzer import (
    LiveAnalyzer,
    get_database_stats,
    format_samples_for_display,
    YAKU_HAI_PAIR_FILTER_GE3,
)
from .equivalent_variants import split_discard_pattern, parse_multi_targets
from .ui.styles import _get_app_stylesheet
from .ui.components import RangeSlider, DiscreteRangeSlider, TileIllustrationWidget
from .ui.workers import DownloadThread, QueryThread, OutcomeQueryThread, SampleThread, GridQueryThread, BatchChartThread, DbStatusThread
from .ui.dialogs import (
    SampleDialog,
    ArchiveViewDialog,
    PatternHelpDialog,
    TargetHelpDialog,
    TileIllustrationDialog,
    MatrixDisplayDialog,
    BatchChartDialog,
    instant_matrix_export_tsv,
)

logger = logging.getLogger(__name__)

# 数据库默认路径（可被 config.ini 覆盖，便于将大库移出项目目录）
_DEFAULT_DB_PATH = r"E:\Cursor\Tenhou data\data\tenhou.db"


def _get_database_path() -> str:
    """从 config.ini 读取 [Database] path，若无则使用默认路径。"""
    root = Path(__file__).resolve().parent.parent
    config_file = root / "config.ini"
    if config_file.exists():
        try:
            cfg = configparser.ConfigParser()
            cfg.read(config_file, encoding="utf-8")
            path = cfg.get("Database", "path", fallback=None)
            if path:
                path = path.strip()
                if path:
                    return path
        except Exception as e:
            logger.warning("读取 config.ini 失败，使用默认数据库路径: %s", e)
    return _DEFAULT_DB_PATH


def _fmt_int(n) -> str:
    """整数格式化，不用千分位符避免中文环境 Qt 渲染乱码（如 8 显示成 日）"""
    return str(int(n))




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
    use_yaku_hai = result.get("analysis_target") == "yaku_hai_hand"
    if multi and pr_list:
        parts = [
            f"{pr['pattern_str']} → {'听牌' if use_tenpai else ('役牌' if use_yaku_hai else pr['target'])}"
            for pr in pr_list
        ]
        return "  |  ".join(parts)
    qp = result.get("query_pattern_str", "") or "-".join(result.get("query_pattern", []))
    target = "听牌" if use_tenpai else ("役牌统计" if use_yaku_hai else result.get("target_tile", ""))
    return f"{qp} → {target}"








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
        
        self.db_path = _get_database_path()
        self.downloader = DataDownloader(self.db_path)
        self.analyzer = LiveAnalyzer(self.db_path)
        self.query_thread = None
        self.sample_thread = None
        self.last_query_params = None  # 上次查询参数，用于生成样本
        self.last_query_result = None  # 上次查询结果（含 sample_pool），用于快速采样
        self._forced_analysis_target = None  # 由铳率分析页强制指定 analysis_target
        self._excel_clipboard_text = ""  # 当前结果的 Excel 格式文本
        self._pattern_checkboxes = []  # 多模式勾选框列表
        self.visible_constraint_row_refs = []  # 场上可见枚数约束引用
        self.hand_visible_constraint_row_refs = []  # 手牌可见枚数约束引用
        self.player_visible_constraint_row_refs = []  # 玩家可见（手牌+场上合并计数）约束引用
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
        self.analysis_batch_size_spin.setRange(100, 20000)
        self.analysis_batch_size_spin.setSingleStep(100)
        saved_batch = QSettings().value("analysis_batch_size", 400, type=int)
        self.analysis_batch_size_spin.blockSignals(True)
        self.analysis_batch_size_spin.setValue(max(100, min(20000, saved_batch or 400)))
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
        self.gc_interval_batches_spin.setRange(1, 100)
        saved_gc = QSettings().value("gc_interval_batches", 4, type=int) or 4
        self.gc_interval_batches_spin.blockSignals(True)
        self.gc_interval_batches_spin.setValue(max(1, min(100, saved_gc)))
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
        pattern_help_btn.setToolTip("舍牌模式输入说明（含关联牌后缀 k）")
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
        self.gen_share_btn = QPushButton("生成分享串")
        self.gen_share_btn.setToolTip("将当前舍牌模式与约束生成可读字符串，便于保存或分享")
        self.gen_share_btn.clicked.connect(lambda: self._show_generate_share_dialog(from_instant=False))
        add_btn_row.addWidget(self.gen_share_btn)
        self.import_share_btn = QPushButton("从分享串导入")
        self.import_share_btn.setToolTip("粘贴分享串，自动填充舍牌模式与约束")
        self.import_share_btn.clicked.connect(self._show_import_share_dialog)
        add_btn_row.addWidget(self.import_share_btn)
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
        self._turn_range_containers: List[Tuple[QWidget, QLineEdit]] = []
        for _ in range(5):
            self._add_turn_range_pair(turn_ranges_row, None)
        add_tr_btn = QPushButton("+")
        add_tr_btn.setFixedWidth(28)
        add_tr_btn.setToolTip("添加一组巡目范围")
        add_tr_btn.clicked.connect(lambda: self._add_turn_range_pair(turn_ranges_row, add_tr_btn))
        self._turn_ranges_row_layout = turn_ranges_row
        self._add_turn_range_btn = add_tr_btn
        turn_ranges_row.addWidget(add_tr_btn)
        turn_ranges_row.addStretch()
        right_layout.addLayout(turn_ranges_row)
        
        # 宝牌 / 立直 / 副露 各占一行，避免文字重叠
        dora_row = QHBoxLayout()
        dora_row.setSpacing(10)
        self.dora_group = QButtonGroup()
        self.dora_any_radio = QRadioButton("宝牌不问")
        self.dora_group.addButton(self.dora_any_radio, 0)
        self.dora_irrelevant_radio = QRadioButton("宝牌无关目标牌")
        self.dora_irrelevant_radio.setChecked(True)
        self.dora_group.addButton(self.dora_irrelevant_radio, 1)
        self.dora_specific_radio = QRadioButton("宝牌为")
        self.dora_group.addButton(self.dora_specific_radio, 2)
        self.dora_matches_position_radio = QRadioButton("宝牌=模式第")
        self.dora_group.addButton(self.dora_matches_position_radio, 3)
        self.dora_tile_input = QLineEdit()
        self.dora_tile_input.setPlaceholderText("例: 6s")
        self.dora_tile_input.setMinimumWidth(50)
        self.dora_tile_input.setMaximumWidth(70)
        self.dora_position_input = QLineEdit()
        self.dora_position_input.setPlaceholderText("例: 1 或 1,3")
        self.dora_position_input.setMinimumWidth(50)
        self.dora_position_input.setMaximumWidth(70)
        self.dora_position_input.setToolTip("1-based，如 1 表示第1张，1,3 表示第1、3张必须为宝牌")
        dora_row.addWidget(self.dora_any_radio)
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
        self.target_no_call_check = QCheckBox("目标无副露")
        self.target_no_call_check.setToolTip("仅针对分析目标的玩家在满足舍牌模式的瞬间没有副露")
        call_row.addWidget(self.call_any_radio)
        call_row.addWidget(self.call_has_radio)
        call_row.addWidget(self.call_no_radio)
        call_row.addWidget(self.target_no_call_check)
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
        # 前段禁打与有打同一行：禁打框限制最大宽度（短规则居多），剩余空间给「前段有打」长模式串
        prior_pair_row = QHBoxLayout()
        prior_pair_row.addWidget(QLabel("前段禁打:"))
        self.prior_discard_exclusion_input = QTextEdit()
        self.prior_discard_exclusion_input.setPlaceholderText("每行一条表达式（换行），多条并集禁打")
        self.prior_discard_exclusion_input.setToolTip(
            "巡目范围开始前，该玩家不能打出这些牌。每行一条表达式，多条之间为并集禁打。"
            "支持 NOTm/p/s、[25]m、4mOR2m 等，与舍牌模式同步等价变换（equivalent mapping）。"
        )
        self.prior_discard_exclusion_input.setLineWrapMode(QTextEdit.WidgetWidth)
        self.prior_discard_exclusion_input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.prior_discard_exclusion_input.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.prior_discard_exclusion_input.setFixedHeight(46)
        self.prior_discard_exclusion_input.setMinimumWidth(140)
        self.prior_discard_exclusion_input.setMaximumWidth(280)
        self.prior_discard_exclusion_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        prior_pair_row.addWidget(self.prior_discard_exclusion_input)

        prior_pair_row.addWidget(QLabel("前段有打:"))
        self.prior_discard_required_input = QLineEdit()
        self.prior_discard_required_input.setPlaceholderText("例: [29]m-3pf")
        self.prior_discard_required_input.setToolTip(
            "匹配前须出现过该舍牌模式，语法同舍牌模式。前段有打 … 舍牌模式，中间不要求；与主模式同步等价变换"
        )
        self.prior_discard_required_input.setMinimumWidth(140)
        self.prior_discard_required_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        prior_pair_row.addWidget(self.prior_discard_required_input, stretch=1)
        right_layout.addLayout(prior_pair_row)

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
        # QGridLayout(parent) 已自动安装到 parent，勿再 setLayout 以免重复安装
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
        
        # 手牌可见枚数（延伸手牌：手牌+牌山，对除目标玩家外的三名玩家分别判断）
        right_layout.addSpacing(4)
        hvc_header = QHBoxLayout()
        hvc_header.addWidget(QLabel("手牌可见枚数 (延伸手牌)"))
        self.add_hand_visible_constraint_btn = QPushButton("+ 添加")
        self.add_hand_visible_constraint_btn.setFixedWidth(72)
        self.add_hand_visible_constraint_btn.clicked.connect(self._add_hand_visible_constraint_row)
        hvc_header.addWidget(self.add_hand_visible_constraint_btn)
        hvc_help_btn = QPushButton("?")
        hvc_help_btn.setFixedWidth(24)
        hvc_help_btn.setToolTip("手牌可见枚数说明")
        hvc_help_btn.clicked.connect(self._show_hand_visible_help)
        hvc_header.addWidget(hvc_help_btn)
        hvc_header.addStretch()
        right_layout.addLayout(hvc_header)
        self.hand_visible_constraint_scroll = QScrollArea()
        self.hand_visible_constraint_scroll.setWidgetResizable(True)
        self.hand_visible_constraint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hand_visible_constraint_scroll.setMinimumHeight(52)
        self.hand_visible_constraint_scroll.setMaximumHeight(100)
        self.hand_visible_constraint_rows_widget = QWidget()
        self.hand_visible_constraint_rows_widget.setMinimumWidth(596)
        self.hand_visible_constraint_rows_layout = QGridLayout(self.hand_visible_constraint_rows_widget)
        self.hand_visible_constraint_rows_layout.setContentsMargins(0, 0, 4, 0)
        self.hand_visible_constraint_rows_layout.setSpacing(2)
        self.hand_visible_constraint_rows_layout.setHorizontalSpacing(8)
        self.hand_visible_constraint_rows_layout.setVerticalSpacing(4)
        self.hand_visible_constraint_rows_layout.setColumnStretch(0, 0)
        self.hand_visible_constraint_rows_layout.setColumnStretch(1, 0)
        self.hand_visible_constraint_scroll.setWidget(self.hand_visible_constraint_rows_widget)
        self.hand_visible_constraint_row_refs = []
        right_layout.addWidget(self.hand_visible_constraint_scroll)
        self._add_hand_visible_constraint_row()

        # 玩家可见枚数（目标玩家手牌中含牌枚数 + 场上可见，与「场上可见」同源统计）
        right_layout.addSpacing(4)
        pvc_header = QHBoxLayout()
        pvc_header.addWidget(QLabel("玩家可见枚数 (手牌+场上)"))
        self.add_player_visible_constraint_btn = QPushButton("+ 添加")
        self.add_player_visible_constraint_btn.setFixedWidth(72)
        self.add_player_visible_constraint_btn.clicked.connect(self._add_player_visible_constraint_row)
        pvc_header.addWidget(self.add_player_visible_constraint_btn)
        pvc_help_btn = QPushButton("?")
        pvc_help_btn.setFixedWidth(24)
        pvc_help_btn.setToolTip("玩家可见枚数说明")
        pvc_help_btn.clicked.connect(self._show_player_visible_help)
        pvc_header.addWidget(pvc_help_btn)
        pvc_header.addStretch()
        right_layout.addLayout(pvc_header)
        self.player_visible_constraint_scroll = QScrollArea()
        self.player_visible_constraint_scroll.setWidgetResizable(True)
        self.player_visible_constraint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.player_visible_constraint_scroll.setMinimumHeight(52)
        self.player_visible_constraint_scroll.setMaximumHeight(100)
        self.player_visible_constraint_rows_widget = QWidget()
        self.player_visible_constraint_rows_widget.setMinimumWidth(596)
        self.player_visible_constraint_rows_layout = QGridLayout(self.player_visible_constraint_rows_widget)
        self.player_visible_constraint_rows_layout.setContentsMargins(0, 0, 4, 0)
        self.player_visible_constraint_rows_layout.setSpacing(2)
        self.player_visible_constraint_rows_layout.setHorizontalSpacing(8)
        self.player_visible_constraint_rows_layout.setVerticalSpacing(4)
        self.player_visible_constraint_rows_layout.setColumnStretch(0, 0)
        self.player_visible_constraint_rows_layout.setColumnStretch(1, 0)
        self.player_visible_constraint_scroll.setWidget(self.player_visible_constraint_rows_widget)
        self.player_visible_constraint_row_refs = []
        right_layout.addWidget(self.player_visible_constraint_scroll)
        self._add_player_visible_constraint_row()
        
        # 分析目标 + 样本上限 + 匹配状态保留条数
        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("分析目标:"))
        self.analysis_target_combo = QComboBox()
        self.analysis_target_combo.addItem("目标牌存量", "target_count")
        self.analysis_target_combo.addItem("是否听牌", "tenpai")
        self.analysis_target_combo.addItem("关联牌判断", "related_tile")
        self.analysis_target_combo.addItem("和铳率", "outcome")
        self.analysis_target_combo.addItem("役牌手持统计", "yaku_hai_hand")
        self.analysis_target_combo.setMinimumWidth(100)
        self.analysis_target_combo.setToolTip(
            "目标牌存量：统计手牌中目标牌数量；"
            "是否听牌：统计匹配时已听牌/未听牌比例；"
            "关联牌判断：按目标牌在模式中最后一次出现的那一打，判断是否关联牌（与手牌搭子距离≤2）；"
            "舍牌模式中若要规定「某张打出时必为关联牌」，请在数牌后加后缀 k（如 2pk），详见舍牌模式 ? 帮助；"
            "和铳率：统计达成模式后该局和了率与放铳率（无需输入目标牌）；"
            "役牌手持统计：按自风/场风/三元统计该座役牌在手——役牌种类(≥1枚)分布，以及役牌对副数(每种役牌≥2枚计1副，刻子仍计1副)之零对/一对/两对/三对及以上，无需目标牌"
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
        self.matched_states_cap_spin.setRange(1, 2000)
        saved_cap = QSettings().value("matched_states_cap", 200, type=int) or 200
        self.matched_states_cap_spin.blockSignals(True)
        self.matched_states_cap_spin.setValue(max(1, min(2000, saved_cap)))
        self.matched_states_cap_spin.blockSignals(False)
        self.matched_states_cap_spin.setToolTip("分析时最多保留的匹配状态条数（用于展示，越大占内存越多）")
        self.matched_states_cap_spin.valueChanged.connect(self._save_matched_states_cap)
        self.matched_states_cap_spin.setMinimumWidth(56)
        opts_row.addWidget(self.matched_states_cap_spin)
        # 搭子独立性筛选：仅「目标牌存量」分析目标下显示（与 live_analyzer 中 eff_independence 一致）
        self.independence_filter_check = QCheckBox("独立性筛选")
        self.independence_filter_check.setToolTip(
            "仅「目标牌存量」且目标为搭子（如 4m5m）时生效：单花色上最优结构价值为 (M,T)（面子数 M、"
            "在 M 最大下搭子/对子块数 T）；强制抽走该两枚后若满足 V原=(V后[0], V后[1]+1) 才计为「有」，"
            "可抑制长顺里误把边界两枚当独立搭子。"
        )
        self.independence_filter_check.setVisible(False)
        opts_row.addWidget(self.independence_filter_check)
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
        """添加一组巡目范围（输入框 + 删除按钮），支持 1-12 格式，默认留空时用上方滑块"""
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        edit = QLineEdit()
        edit.setPlaceholderText("1-12")
        edit.setFixedWidth(56)
        edit.setMaxLength(6)
        edit.setToolTip("输入 最小-最大，如 1-6、4-9")
        row.addWidget(edit)
        del_btn = QPushButton("×")
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip("删除此组巡目范围")
        row.addWidget(del_btn)
        if add_btn is not None:
            idx = layout.indexOf(add_btn)
            layout.insertWidget(idx, container)
        else:
            layout.addWidget(container)
        self._turn_range_edits.append(edit)
        self._turn_range_containers.append((container, edit))

        def do_remove():
            # 从布局移除并销毁容器，同步移除 _turn_range_edits 中的引用
            layout.removeWidget(container)
            container.deleteLater()
            self._turn_range_edits.remove(edit)
            self._turn_range_containers.remove((container, edit))

        del_btn.clicked.connect(do_remove)

    def _instant_add_turn_range_pair(self, layout: QHBoxLayout, add_btn: Optional[QPushButton] = None):
        """铳率页：添加一组巡目输入（与主分析 `_add_turn_range_pair` 同款语义）。"""
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        edit = QLineEdit()
        edit.setPlaceholderText("1-6")
        edit.setFixedWidth(56)
        edit.setMaxLength(6)
        edit.setToolTip("最小-最大巡目，如 1-6；多组即矩阵分析")
        row.addWidget(edit)
        del_btn = QPushButton("×")
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip("删除此组巡目范围")
        row.addWidget(del_btn)
        if add_btn is not None:
            idx = layout.indexOf(add_btn)
            layout.insertWidget(idx, container)
        else:
            layout.addWidget(container)
        self._instant_turn_range_edits.append(edit)
        self._instant_turn_range_containers.append((container, edit))

        def do_remove():
            if len(self._instant_turn_range_edits) <= 1:
                edit.clear()
                return
            layout.removeWidget(container)
            container.deleteLater()
            self._instant_turn_range_edits.remove(edit)
            self._instant_turn_range_containers.remove((container, edit))

        del_btn.clicked.connect(do_remove)

    def _instant_get_turn_ranges_from_ui(self) -> List[Tuple[int, int]]:
        """铳率页巡目：多行输入有效行优先（去重保序）；若全空则用连续巡目 SpinBox 退化为单段。"""
        seen = set()
        turn_ranges = []
        for edit in getattr(self, "_instant_turn_range_edits", []):
            line = edit.text().strip()
            if not line:
                continue
            tr = _parse_turn_range(line)
            if tr and tr not in seen:
                seen.add(tr)
                turn_ranges.append(tr)
        if not turn_ranges:
            t_lo = self.instant_turn_min.value()
            t_hi = self.instant_turn_max.value()
            if t_lo <= t_hi:
                turn_ranges = [(t_lo, t_hi)]
        return turn_ranges

    def _sync_instant_turn_ranges_to_main_edits(self):
        """将铳率页巡目（多行或连续巡目回退）同步到主分析页的矩阵巡目输入与滑块。"""
        tr_list = self._instant_get_turn_ranges_from_ui()
        if not tr_list:
            return
        row_layout = getattr(self, "_turn_ranges_row_layout", None)
        add_btn = getattr(self, "_add_turn_range_btn", None)
        while len(self._turn_range_edits) < len(tr_list) and row_layout and add_btn:
            self._add_turn_range_pair(row_layout, add_btn)
        for i, (a, b) in enumerate(tr_list):
            if i < len(self._turn_range_edits):
                self._turn_range_edits[i].setText("%d-%d" % (a, b))
        for i in range(len(tr_list), len(self._turn_range_edits)):
            self._turn_range_edits[i].clear()
        t_lo = min(r[0] for r in tr_list)
        t_hi = max(r[1] for r in tr_list)
        self.turn_range_slider.setRange(t_lo, t_hi)

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
        pattern_edit.setPlaceholderText("例: 7s-9s、2pk-1m、c0p6p-$、cd1-3m")
        pattern_edit.setMinimumWidth(150)
        target_edit = QLineEdit()
        target_edit.setPlaceholderText("例: 6s、45.p、6s 2m 5p")
        target_edit.setMaximumWidth(80)
        arrow_label = QLabel("→")
        target_help_btn = QPushButton("?")
        target_help_btn.setToolTip("目标牌说明（含 5.p 五/赤五通配；即时铳率不支持 .）")
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
        """和铳率模式下隐藏目标牌输入（无需目标牌）；关联牌判断需目标牌（分析该牌的关联度）"""
        if not hasattr(self, "analysis_target_combo"):
            return
        data = self.analysis_target_combo.currentData() or ""
        is_outcome = data == "outcome"
        is_yaku_hai = data == "yaku_hai_hand"
        hide_target = is_outcome or is_yaku_hai
        for pattern_edit, target_edit, arrow_label, target_help_btn, del_btn, row in getattr(
            self, "_pattern_row_widgets", []
        ):
            arrow_label.setVisible(not hide_target)
            target_edit.setVisible(not hide_target)
            target_help_btn.setVisible(not hide_target)
            if hide_target:
                target_edit.clear()
                target_edit.setPlaceholderText("（无需目标牌）")
            elif data == "related_tile":
                target_edit.setPlaceholderText("例: 2p（分析该牌的关联度，须在模式中出现）")
            else:
                target_edit.setPlaceholderText("例: 6s、45.p、6s 2m 5p")
        if hasattr(self, "independence_filter_check"):
            self.independence_filter_check.setVisible(data == "target_count")

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

    def _show_batch_chart_dialog(self):
        """打开批量折线图数据对话框（依赖注入避免循环导入）"""
        dlg = BatchChartDialog(
            self,
            load_db_status_fn=_load_db_status_from_cache,
            parse_pattern_fn=_parse_pattern_target,
            parse_turn_range_fn=_parse_turn_range,
        )
        dlg.exec_()

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

        ph_row = QHBoxLayout()
        ph_row.addWidget(QLabel("舍牌模式 (可添加多条，满足任一即计入):"))
        instant_pattern_help_btn = QPushButton("?")
        instant_pattern_help_btn.setToolTip("舍牌模式输入说明（含关联牌后缀 k）")
        instant_pattern_help_btn.setFixedWidth(28)
        instant_pattern_help_btn.clicked.connect(self._show_pattern_help)
        ph_row.addWidget(instant_pattern_help_btn)
        ph_row.addStretch()
        layout.addLayout(ph_row)
        self.instant_pattern_rows_container = QWidget()
        self.instant_pattern_rows_layout = QVBoxLayout(self.instant_pattern_rows_container)
        self.instant_pattern_rows_layout.setSpacing(6)
        self.instant_pattern_rows_layout.setContentsMargins(0, 0, 0, 0)
        instant_pattern_scroll = QScrollArea()
        instant_pattern_scroll.setWidget(self.instant_pattern_rows_container)
        instant_pattern_scroll.setWidgetResizable(True)
        instant_pattern_scroll.setMinimumHeight(72)
        instant_pattern_scroll.setMaximumHeight(120)
        instant_pattern_scroll.setFrameShape(QFrame.NoFrame)
        instant_pattern_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(instant_pattern_scroll)
        add_instant_row = QHBoxLayout()
        self.instant_add_pattern_btn = QPushButton("+ 添加舍牌模式")
        self.instant_add_pattern_btn.clicked.connect(self._instant_add_pattern_row)
        add_instant_row.addWidget(self.instant_add_pattern_btn)
        add_instant_row.addStretch()
        layout.addLayout(add_instant_row)
        self._instant_pattern_row_widgets = []
        self._instant_add_pattern_row()

        row1b = QHBoxLayout()
        row1b.addWidget(QLabel("假想振听牌:"))
        self.instant_hypothetical_furiten_input = QLineEdit()
        self.instant_hypothetical_furiten_input.setPlaceholderText("例: 6p 或 6p,7p")
        self.instant_hypothetical_furiten_input.setToolTip(
            "若假想振听牌在该时点也会放铳，则该匹配不计入主铳率，而是计入「因假想振听牌被排除的案列数」"
        )
        row1b.addWidget(self.instant_hypothetical_furiten_input, 1)
        # 功能区旁说明：多张用逗号分隔，若该牌也会放铳则不计入主铳率
        furiten_hint = QLabel("多张用逗号分隔，如 6p,7p；若该牌也会放铳则不计入主铳率")
        furiten_hint.setStyleSheet("color: gray; font-size: 11px;")
        furiten_hint.setWordWrap(True)
        row1b.addWidget(furiten_hint, 0)
        layout.addLayout(row1b)

        # 约束区
        constraints_group = QGroupBox("约束条件")
        c_layout = QVBoxLayout()

        dora_row = QHBoxLayout()
        dora_row.setSpacing(8)
        self.instant_dora_group = QButtonGroup(self)
        self.instant_dora_any_radio = QRadioButton("宝牌不问")
        self.instant_dora_group.addButton(self.instant_dora_any_radio, 0)
        self.instant_dora_irrelevant_radio = QRadioButton("宝牌无关目标牌")
        self.instant_dora_irrelevant_radio.setChecked(True)
        self.instant_dora_group.addButton(self.instant_dora_irrelevant_radio, 1)
        self.instant_dora_specific_radio = QRadioButton("宝牌为")
        self.instant_dora_group.addButton(self.instant_dora_specific_radio, 2)
        self.instant_dora_matches_position_radio = QRadioButton("宝牌=模式第")
        self.instant_dora_group.addButton(self.instant_dora_matches_position_radio, 3)
        self.instant_dora_tile_input = QLineEdit()
        self.instant_dora_tile_input.setPlaceholderText("例: 6s")
        self.instant_dora_tile_input.setMinimumWidth(50)
        self.instant_dora_tile_input.setMaximumWidth(70)
        self.instant_dora_position_input = QLineEdit()
        self.instant_dora_position_input.setPlaceholderText("例: 1 或 1,3")
        self.instant_dora_position_input.setMinimumWidth(50)
        self.instant_dora_position_input.setMaximumWidth(70)
        self.instant_dora_position_input.setToolTip("1-based，如 1 表示第1张，1,3 表示第1、3张必须为宝牌")
        dora_row.addWidget(self.instant_dora_any_radio)
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
        self.instant_target_no_call_check = QCheckBox("目标无副露")
        self.instant_target_no_call_check.setToolTip("仅针对分析目标的玩家在满足舍牌模式的瞬间没有副露")
        call_row.addWidget(self.instant_call_any_radio)
        call_row.addWidget(self.instant_call_has_radio)
        call_row.addWidget(self.instant_call_no_radio)
        call_row.addWidget(self.instant_target_no_call_check)
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

        instant_prior_pair_row = QHBoxLayout()
        instant_prior_pair_row.addWidget(QLabel("前段禁打:"))
        self.instant_prior_discard_exclusion_input = QTextEdit()
        self.instant_prior_discard_exclusion_input.setPlaceholderText("每行一条表达式（换行），多条并集禁打")
        self.instant_prior_discard_exclusion_input.setToolTip(
            "巡目范围开始前，该玩家不能打出这些牌。每行一条表达式，多条之间为并集禁打。"
            "支持 NOTm/p/s、[25]m、4mOR2m 等，与舍牌模式同步等价变换（equivalent mapping）。"
        )
        self.instant_prior_discard_exclusion_input.setLineWrapMode(QTextEdit.WidgetWidth)
        self.instant_prior_discard_exclusion_input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.instant_prior_discard_exclusion_input.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.instant_prior_discard_exclusion_input.setFixedHeight(46)
        self.instant_prior_discard_exclusion_input.setMinimumWidth(140)
        self.instant_prior_discard_exclusion_input.setMaximumWidth(280)
        self.instant_prior_discard_exclusion_input.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        instant_prior_pair_row.addWidget(self.instant_prior_discard_exclusion_input)

        instant_prior_pair_row.addWidget(QLabel("前段有打:"))
        self.instant_prior_discard_required_input = QLineEdit()
        self.instant_prior_discard_required_input.setPlaceholderText("例: [29]m-3pf")
        self.instant_prior_discard_required_input.setToolTip(
            "匹配前须出现过该舍牌模式，语法同舍牌模式。前段有打 … 舍牌模式，中间不要求；与主模式同步等价变换"
        )
        self.instant_prior_discard_required_input.setMinimumWidth(140)
        self.instant_prior_discard_required_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        instant_prior_pair_row.addWidget(self.instant_prior_discard_required_input, stretch=1)
        c_layout.addLayout(instant_prior_pair_row)

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

        c_layout.addSpacing(4)
        hvc_header = QHBoxLayout()
        hvc_header.addWidget(QLabel("手牌可见枚数 (延伸手牌)"))
        self.instant_add_hand_visible_constraint_btn = QPushButton("+ 添加")
        self.instant_add_hand_visible_constraint_btn.setFixedWidth(72)
        self.instant_add_hand_visible_constraint_btn.clicked.connect(self._instant_add_hand_visible_constraint_row)
        hvc_header.addWidget(self.instant_add_hand_visible_constraint_btn)
        hvc_help_btn = QPushButton("?")
        hvc_help_btn.setFixedWidth(24)
        hvc_help_btn.setToolTip("手牌可见枚数说明")
        hvc_help_btn.clicked.connect(self._show_hand_visible_help)
        hvc_header.addWidget(hvc_help_btn)
        hvc_header.addStretch()
        c_layout.addLayout(hvc_header)
        self.instant_hand_visible_constraint_scroll = QScrollArea()
        self.instant_hand_visible_constraint_scroll.setWidgetResizable(True)
        self.instant_hand_visible_constraint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.instant_hand_visible_constraint_scroll.setMinimumHeight(52)
        self.instant_hand_visible_constraint_scroll.setMaximumHeight(100)
        self.instant_hand_visible_constraint_rows_widget = QWidget()
        self.instant_hand_visible_constraint_rows_widget.setMinimumWidth(596)
        self.instant_hand_visible_constraint_rows_layout = QGridLayout(self.instant_hand_visible_constraint_rows_widget)
        self.instant_hand_visible_constraint_rows_layout.setContentsMargins(0, 0, 4, 0)
        self.instant_hand_visible_constraint_rows_layout.setSpacing(2)
        self.instant_hand_visible_constraint_rows_layout.setHorizontalSpacing(8)
        self.instant_hand_visible_constraint_rows_layout.setVerticalSpacing(4)
        self.instant_hand_visible_constraint_rows_layout.setColumnStretch(0, 0)
        self.instant_hand_visible_constraint_rows_layout.setColumnStretch(1, 0)
        self.instant_hand_visible_constraint_scroll.setWidget(self.instant_hand_visible_constraint_rows_widget)
        self.instant_hand_visible_constraint_row_refs = []
        c_layout.addWidget(self.instant_hand_visible_constraint_scroll)
        self._instant_add_hand_visible_constraint_row()

        c_layout.addSpacing(4)
        pvc_header_i = QHBoxLayout()
        pvc_header_i.addWidget(QLabel("玩家可见枚数 (手牌+场上)"))
        self.instant_add_player_visible_constraint_btn = QPushButton("+ 添加")
        self.instant_add_player_visible_constraint_btn.setFixedWidth(72)
        self.instant_add_player_visible_constraint_btn.clicked.connect(
            self._instant_add_player_visible_constraint_row
        )
        pvc_header_i.addWidget(self.instant_add_player_visible_constraint_btn)
        pvc_help_btn_i = QPushButton("?")
        pvc_help_btn_i.setFixedWidth(24)
        pvc_help_btn_i.setToolTip("玩家可见枚数说明")
        pvc_help_btn_i.clicked.connect(self._show_player_visible_help)
        pvc_header_i.addWidget(pvc_help_btn_i)
        pvc_header_i.addStretch()
        c_layout.addLayout(pvc_header_i)
        self.instant_player_visible_constraint_scroll = QScrollArea()
        self.instant_player_visible_constraint_scroll.setWidgetResizable(True)
        self.instant_player_visible_constraint_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.instant_player_visible_constraint_scroll.setMinimumHeight(52)
        self.instant_player_visible_constraint_scroll.setMaximumHeight(100)
        self.instant_player_visible_constraint_rows_widget = QWidget()
        self.instant_player_visible_constraint_rows_widget.setMinimumWidth(596)
        self.instant_player_visible_constraint_rows_layout = QGridLayout(
            self.instant_player_visible_constraint_rows_widget
        )
        self.instant_player_visible_constraint_rows_layout.setContentsMargins(0, 0, 4, 0)
        self.instant_player_visible_constraint_rows_layout.setSpacing(2)
        self.instant_player_visible_constraint_rows_layout.setHorizontalSpacing(8)
        self.instant_player_visible_constraint_rows_layout.setVerticalSpacing(4)
        self.instant_player_visible_constraint_rows_layout.setColumnStretch(0, 0)
        self.instant_player_visible_constraint_rows_layout.setColumnStretch(1, 0)
        self.instant_player_visible_constraint_scroll.setWidget(
            self.instant_player_visible_constraint_rows_widget
        )
        self.instant_player_visible_constraint_row_refs = []
        c_layout.addWidget(self.instant_player_visible_constraint_scroll)
        self._instant_add_player_visible_constraint_row()

        constraints_group.setLayout(c_layout)
        layout.addWidget(constraints_group)

        # 多组巡目（与主分析矩阵一致）：有填写则参与矩阵；全空时用下方连续巡目 SpinBox
        tr_head = QHBoxLayout()
        tr_head.addWidget(QLabel("巡目范围（多组即矩阵；全空则用下方连续巡目）:"))
        instant_tr_help = QPushButton("?")
        instant_tr_help.setFixedWidth(28)
        instant_tr_help.setToolTip("巡目范围使用说明")
        instant_tr_help.clicked.connect(self._show_turn_range_help)
        tr_head.addWidget(instant_tr_help)
        tr_head.addStretch()
        layout.addLayout(tr_head)
        self._instant_turn_range_edits = []
        self._instant_turn_range_containers = []
        instant_tr_row = QHBoxLayout()
        instant_tr_row.setSpacing(6)
        self._instant_turn_ranges_row_layout = instant_tr_row
        self._instant_add_turn_range_pair(instant_tr_row, None)
        self._instant_add_turn_range_btn = QPushButton("+ 添加")
        self._instant_add_turn_range_btn.setToolTip("添加一组巡目范围")
        self._instant_add_turn_range_btn.clicked.connect(
            lambda: self._instant_add_turn_range_pair(
                instant_tr_row, self._instant_add_turn_range_btn
            )
        )
        instant_tr_row.addWidget(self._instant_add_turn_range_btn)
        instant_tr_row.addStretch()
        layout.addLayout(instant_tr_row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("连续巡目（仅当上方全空时生效）:"))
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
        self.instant_matched_states_cap_spin.setRange(1, 2000)
        saved_cap = QSettings().value("matched_states_cap", 200, type=int) or 200
        self.instant_matched_states_cap_spin.setValue(max(1, min(2000, saved_cap)))
        self.instant_matched_states_cap_spin.setMinimumWidth(56)
        row2.addWidget(self.instant_matched_states_cap_spin)
        row2.addSpacing(8)
        self.instant_use_theory_point_only_check = QCheckBox("不考虑里宝（平均铳点仅按理论点）")
        self.instant_use_theory_point_only_check.setChecked(True)
        self.instant_use_theory_point_only_check.setToolTip(
            "勾选时：平均铳点 = 可铳样本的理论点（仅表宝牌）求平均。不勾选时：可考虑里宝（若已实现里宝随机模拟则用其求平均）。\n"
            "当前版本：勾选与不勾选效果相同（尚未实现里宝模拟，均按理论点）。"
        )
        row2.addWidget(self.instant_use_theory_point_only_check)
        self.instant_use_theory_point_only_hint = QLabel("（当前效果相同）")
        self.instant_use_theory_point_only_hint.setStyleSheet("color: #888; font-size: 11px;")
        self.instant_use_theory_point_only_hint.setToolTip("里宝随机模拟尚未实现，故勾选与不勾选均为理论点求平均。")
        row2.addWidget(self.instant_use_theory_point_only_hint)
        self.instant_normalize_oya_ron_to_ko_check = QCheckBox("亲家和牌以子家计算")
        self.instant_normalize_oya_ron_to_ko_check.setChecked(False)
        self.instant_normalize_oya_ron_to_ko_check.setToolTip(
            "勾选时：亲家荣和时的铳点按子家换算（折半），统一统计口径，减少亲家样本带来的偏差。"
        )
        row2.addWidget(self.instant_normalize_oya_ron_to_ko_check)
        row2.addStretch()
        self.instant_gen_share_btn = QPushButton("生成分享串")
        self.instant_gen_share_btn.setToolTip("将当前舍牌模式与约束生成可读字符串")
        self.instant_gen_share_btn.clicked.connect(lambda: self._show_generate_share_dialog(from_instant=True))
        row2.addWidget(self.instant_gen_share_btn)
        self.instant_import_share_btn = QPushButton("从分享串导入")
        self.instant_import_share_btn.setToolTip("粘贴分享串，自动填充舍牌模式与约束")
        self.instant_import_share_btn.clicked.connect(self._show_import_share_dialog)
        row2.addWidget(self.instant_import_share_btn)
        self.instant_start_btn = QPushButton("开始即时铳率分析")
        self.instant_start_btn.clicked.connect(self._start_instant_deal_in_analysis)
        row2.addWidget(self.instant_start_btn)
        layout.addLayout(row2)

        self.instant_status_label = QLabel("")
        layout.addWidget(self.instant_status_label)
        layout.addStretch()
        return widget

    def _instant_add_pattern_row(self):
        """铳率分析页：添加一行舍牌模式+目标牌"""
        row = QHBoxLayout()
        row.setSpacing(8)
        pattern_edit = QLineEdit()
        pattern_edit.setPlaceholderText("例: 7s-9s、2pk-1m、c0p6p-$、cd1-3m")
        pattern_edit.setMinimumWidth(120)
        target_edit = QLineEdit()
        target_edit.setPlaceholderText("例: 6s、3p,4p、5.p（铳率勿用 .）")
        target_edit.setMaximumWidth(100)
        del_btn = QPushButton("×")
        del_btn.setFixedWidth(28)
        del_btn.setToolTip("删除此行")
        row.addWidget(QLabel("模式:"))
        row.addWidget(pattern_edit, 1)
        row.addWidget(QLabel("目标牌:"))
        row.addWidget(target_edit)
        row.addWidget(del_btn)
        entry = (pattern_edit, target_edit, del_btn, row)
        self._instant_pattern_row_widgets.append(entry)
        del_btn.clicked.connect(lambda checked=False, e=entry: self._instant_remove_pattern_row(e))
        self.instant_pattern_rows_layout.addLayout(row)

    def _instant_remove_pattern_row(self, entry):
        """铳率分析页：移除一行舍牌模式"""
        if len(self._instant_pattern_row_widgets) <= 1:
            QMessageBox.warning(self, "提示", "至少需保留一个舍牌模式")
            return
        pattern_edit, target_edit, del_btn, row = entry
        while row.count():
            item = row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.instant_pattern_rows_layout.removeItem(row)
        if entry in self._instant_pattern_row_widgets:
            self._instant_pattern_row_widgets.remove(entry)

    def _instant_rebuild_visible_constraint_grid(self):
        """将即时铳率页场上可见枚数条目按 2 列重新排列（删除行后调用）。"""
        layout = self.instant_visible_constraint_rows_layout
        parent = self.instant_visible_constraint_rows_widget
        if not layout or not parent:
            return
        while layout.count():
            layout.takeAt(0)
        for i, (_, _, row_widget) in enumerate(self.instant_visible_constraint_row_refs):
            row_widget.setParent(parent)
            layout.addWidget(row_widget, i // 2, i % 2)
            row_widget.show()

    def _instant_add_visible_constraint_row(self):
        """即时铳率页：添加一行场上可见枚数输入。"""
        row_widget = QWidget(self.instant_visible_constraint_rows_widget)
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
        idx = len(self.instant_visible_constraint_row_refs) - 1
        self.instant_visible_constraint_rows_layout.addWidget(row_widget, idx // 2, idx % 2)
        row_widget.show()

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

    def _instant_rebuild_hand_visible_constraint_grid(self):
        """将即时铳率页手牌可见枚数条目按 2 列重新排列（删除行后调用）。"""
        layout = self.instant_hand_visible_constraint_rows_layout
        parent = self.instant_hand_visible_constraint_rows_widget
        if not layout or not parent:
            return
        while layout.count():
            layout.takeAt(0)
        for i, (_, _, row_widget) in enumerate(self.instant_hand_visible_constraint_row_refs):
            row_widget.setParent(parent)
            layout.addWidget(row_widget, i // 2, i % 2)
            row_widget.show()

    def _instant_add_hand_visible_constraint_row(self):
        """即时铳率页：添加一行手牌可见枚数输入。"""
        row_widget = QWidget(self.instant_hand_visible_constraint_rows_widget)
        row_widget.setFixedWidth(292)
        row_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 8, 2)
        row_layout.setSpacing(4)
        tile_edit = QLineEdit()
        tile_edit.setPlaceholderText("牌，如 8m")
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
        self.instant_hand_visible_constraint_row_refs.append((tile_edit, range_slider, row_widget))
        idx = len(self.instant_hand_visible_constraint_row_refs) - 1
        self.instant_hand_visible_constraint_rows_layout.addWidget(row_widget, idx // 2, idx % 2)
        row_widget.show()

        def do_remove():
            if self.instant_hand_visible_constraint_rows_layout:
                self.instant_hand_visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self.instant_hand_visible_constraint_row_refs = [
                r for r in self.instant_hand_visible_constraint_row_refs if r[2] != row_widget
            ]
            self._instant_rebuild_hand_visible_constraint_grid()

        remove_btn.clicked.connect(do_remove)

    def _instant_get_hand_visible_constraints_from_ui(self) -> Dict[str, Tuple[int, int]]:
        """即时铳率页：读取手牌可见枚数约束。"""
        hand_visible_constraints = {}
        for tile_edit, range_slider, _ in self.instant_hand_visible_constraint_row_refs:
            tile = tile_edit.text().strip()
            if not tile:
                continue
            min_count, max_count = range_slider.getRange()
            if min_count > max_count:
                continue
            hand_visible_constraints[tile] = (min_count, max_count)
        return hand_visible_constraints

    def _instant_rebuild_player_visible_constraint_grid(self):
        layout = self.instant_player_visible_constraint_rows_layout
        parent = self.instant_player_visible_constraint_rows_widget
        if not layout or not parent:
            return
        while layout.count():
            layout.takeAt(0)
        for i, (_, _, row_widget) in enumerate(self.instant_player_visible_constraint_row_refs):
            row_widget.setParent(parent)
            layout.addWidget(row_widget, i // 2, i % 2)
            row_widget.show()

    def _instant_add_player_visible_constraint_row(self):
        row_widget = QWidget(self.instant_player_visible_constraint_rows_widget)
        row_widget.setFixedWidth(292)
        row_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 8, 2)
        row_layout.setSpacing(4)
        tile_edit = QLineEdit()
        tile_edit.setPlaceholderText("牌，如 5p")
        tile_edit.setFixedWidth(52)
        row_layout.addWidget(tile_edit)
        row_layout.addWidget(QLabel("合计"))
        range_slider = DiscreteRangeSlider()
        row_layout.addWidget(range_slider)
        row_layout.addWidget(QLabel("枚"))
        remove_btn = QPushButton("X")
        remove_btn.setFixedSize(32, 24)
        remove_btn.setToolTip("删除此行")
        row_layout.addWidget(remove_btn)
        self.instant_player_visible_constraint_row_refs.append((tile_edit, range_slider, row_widget))
        idx = len(self.instant_player_visible_constraint_row_refs) - 1
        self.instant_player_visible_constraint_rows_layout.addWidget(row_widget, idx // 2, idx % 2)
        row_widget.show()

        def do_remove():
            if self.instant_player_visible_constraint_rows_layout:
                self.instant_player_visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self.instant_player_visible_constraint_row_refs = [
                r for r in self.instant_player_visible_constraint_row_refs if r[2] != row_widget
            ]
            self._instant_rebuild_player_visible_constraint_grid()

        remove_btn.clicked.connect(do_remove)

    def _instant_get_player_visible_constraints_from_ui(self) -> Dict[str, Tuple[int, int]]:
        out = {}
        for tile_edit, range_slider, _ in self.instant_player_visible_constraint_row_refs:
            tile = tile_edit.text().strip()
            if not tile:
                continue
            min_count, max_count = range_slider.getRange()
            if min_count > max_count:
                continue
            out[tile] = (min_count, max_count)
        return out

    def _sync_instant_constraints_to_main(self):
        """将即时铳率页的约束与参数同步到主分析页控件。"""
        # 宝牌约束
        self.dora_any_radio.setChecked(self.instant_dora_any_radio.isChecked())
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
        self.target_no_call_check.setChecked(self.instant_target_no_call_check.isChecked())

        # 南三/南四
        self.exclude_south4_check.setChecked(self.instant_exclude_south4_check.isChecked())
        self.exclude_south3_check.setChecked(self.instant_exclude_south3_check.isChecked())

        # 前段禁打 / 前段有打
        self.prior_discard_exclusion_input.setPlainText(
            self.instant_prior_discard_exclusion_input.toPlainText().strip()
        )
        self.prior_discard_required_input.setText(
            self.instant_prior_discard_required_input.text().strip()
        )

        # 副露区域
        for i, le in enumerate(self.call_area_inputs):
            text = self.instant_call_area_inputs[i].text().strip() if i < len(self.instant_call_area_inputs) else ""
            le.setText(text)

        # 场上可见枚数 / 手牌可见枚数（先清空主分析条目，再按即时页重建）
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

        instant_h_constraints = list(self._instant_get_hand_visible_constraints_from_ui().items())
        for _, _, row_widget in list(self.hand_visible_constraint_row_refs):
            self.hand_visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self.hand_visible_constraint_row_refs.clear()
        if not instant_h_constraints:
            self._add_hand_visible_constraint_row()
        else:
            for tile, (min_count, max_count) in instant_h_constraints:
                self._add_hand_visible_constraint_row()
                tile_edit, range_slider, _ = self.hand_visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(min_count, max_count)

        instant_p_constraints = list(self._instant_get_player_visible_constraints_from_ui().items())
        for _, _, row_widget in list(self.player_visible_constraint_row_refs):
            self.player_visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self.player_visible_constraint_row_refs.clear()
        if not instant_p_constraints:
            self._add_player_visible_constraint_row()
        else:
            for tile, (min_count, max_count) in instant_p_constraints:
                self._add_player_visible_constraint_row()
                tile_edit, range_slider, _ = self.player_visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(min_count, max_count)

        # 样本上限、匹配保留
        self.sample_limit_input.setValue(self.instant_sample_limit_input.value())
        self.matched_states_cap_spin.setValue(self.instant_matched_states_cap_spin.value())
        self._sync_instant_turn_ranges_to_main_edits()

    def _start_instant_deal_in_analysis(self):
        turn_ranges_inst = self._instant_get_turn_ranges_from_ui()
        for tmin, tmax in turn_ranges_inst:
            if tmin > tmax:
                QMessageBox.warning(self, "输入错误", "巡目范围最小值不能大于最大值")
                return

        # 从铳率分析页多行收集 (pattern, target) 原始字符串
        items = []
        for pattern_edit, target_edit, _, _ in self._instant_pattern_row_widgets:
            pt = pattern_edit.text().strip()
            tg = target_edit.text().strip()
            if not pt or not tg:
                continue
            try:
                split_discard_pattern(pt)
            except Exception:
                QMessageBox.warning(self, "输入错误", "舍牌模式格式无效: %s" % pt[:20])
                return
            try:
                mt = parse_multi_targets(tg)
                if any(is_combo for tiles, is_combo in mt):
                    QMessageBox.warning(self, "输入错误", "即时铳率暂不支持 combo 目标牌（如 4s-5s），请使用逗号分隔的多目标（如 4s,5s）")
                    return
                if "." in tg:
                    QMessageBox.warning(
                        self,
                        "输入错误",
                        "即时铳率不支持目标通配符（如 5.p），请使用 5p 或 0p 明确指定",
                    )
                    return
            except Exception:
                QMessageBox.warning(self, "输入错误", "目标牌格式无效: %s" % tg[:20])
                return
            items.append((pt, tg))

        if not items:
            QMessageBox.warning(self, "输入错误", "请至少填写一行舍牌模式与目标牌")
            return

        # 宝牌输入校验
        if self.instant_dora_specific_radio.isChecked() and not self.instant_dora_tile_input.text().strip():
            QMessageBox.warning(self, "输入错误", "请选择“宝牌为”时，请填写宝牌（例: 6s）")
            return
        if self.instant_dora_matches_position_radio.isChecked() and not self.instant_dora_position_input.text().strip():
            QMessageBox.warning(self, "输入错误", "请选择“宝牌=模式第”时，请填写位置（例: 1 或 1,3）")
            return

        # 复用主查询页控件：确保有足够行并填入 instant 的多条，多余行清空
        if not getattr(self, "_pattern_row_widgets", None):
            self._add_pattern_row()
        while len(self._pattern_row_widgets) < len(items):
            self._add_pattern_row()
        for idx, (pattern_edit, target_edit, _, _, _, _) in enumerate(self._pattern_row_widgets):
            if idx < len(items):
                pattern_edit.setText(items[idx][0])
                target_edit.setText(items[idx][1])
            else:
                pattern_edit.clear()
                target_edit.clear()

        self._sync_instant_constraints_to_main()
        self._forced_analysis_target = "deal_in_instant"
        # 巡目已在 _sync_instant_constraints_to_main 末尾同步到主界面列表

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
        batch_chart_btn = QPushButton("生成折线图数据")
        batch_chart_btn.setToolTip("批量分析 舍牌模式×巡目范围，生成表格供 Excel 绘制折线图")
        batch_chart_btn.clicked.connect(self._show_batch_chart_dialog)
        tool_row.addWidget(batch_chart_btn)
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

    def _copy_per_mode_result(self):
        """复制各模式分别的即时铳率数据到剪贴板（Tab 分隔，可粘贴到 Excel）"""
        result = getattr(self, "last_query_result", None)
        if not result or result.get("analysis_target") != "deal_in_instant":
            self.statusBar().showMessage("当前无多模式即时铳率结果可复制", 2000)
            return
        pr_list = result.get("pattern_results") or []
        if not pr_list:
            self.statusBar().showMessage("当前无多模式即时铳率结果可复制", 2000)
            return
        text = self._build_excel_text(result, pr_list, multi=True)
        if text:
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage("已复制各模式数据到剪贴板", 2000)

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
        """生成 Tab 分隔的 Excel 友好格式"""
        tr = result.get('turn_range')
        turn_str = f"{tr[0]}-{tr[1]}巡" if tr else "不限"
        use_tenpai = (result.get('analysis_target') == 'tenpai')
        use_instant = (result.get('analysis_target') == 'deal_in_instant')
        
        if use_instant:
            header = "舍牌模式\t目标牌\t巡目范围\t铳率\t可铳样本\t平均铳点\t铳度"
            rows = [header]
            pattern = result.get('query_pattern_str', '') or '-'.join(result.get('query_pattern', []))
            
            def _instant_row(p, t, r):
                rate = r.get('deal_in_rate', r.get('rate', 0.0))
                hits = r.get('deal_in_hits', r.get('hits', 0))
                p_avg = r.get('deal_in_point_avg', r.get('point_avg', 0.0))
                intensity = r.get('deal_in_intensity', r.get('intensity', 0.0))
                return f"{p}\t{t}\t{turn_str}\t{rate:.2%}\t{hits}\t{p_avg:.1f}\t{intensity:.2f}"

            multi_target = result.get('multi_target', False)
            if multi and pr_list:
                for pr in pr_list:
                    mis = pr.get("multi_instant_stats") or {}
                    for tk in pr.get("target_tiles") or []:
                        s = mis.get(tk, {})
                        if s:
                            rows.append(_instant_row(pr["pattern_str"], tk, s))
            elif multi_target and result.get('multi_instant_stats'):
                for tk, stats in result['multi_instant_stats'].items():
                    rows.append(_instant_row(pattern, tk, stats))
            else:
                rows.append(_instant_row(pattern, result.get('target_tile', ''), result))
        else:
            use_yaku_hai = result.get("analysis_target") == "yaku_hai_hand"
            if use_yaku_hai:
                # 役牌：种类 0～5 与对副四桶分列导出（Excel 友好）
                hdr_k = "舍牌模式\t役牌种类(≥1枚)\t巡目范围\t0\t1\t2\t3\t4\t5+"
                hdr_u = "舍牌模式\t役牌对副数\t巡目范围\t零对\t一对\t两对\t三对及以上"
                rows = [hdr_k]
                if multi and pr_list:
                    for pr in pr_list:
                        pdim = pr.get("probability_distribution") or {}
                        sk = pdim.get("kinds", {})
                        rows.append(
                            pr["pattern_str"]
                            + "\t役牌种类\t"
                            + turn_str
                            + "\t"
                            + "\t".join(f"{sk.get(i, 0):.2f}" for i in range(6))
                        )
                    rows.append("")
                    rows.append(hdr_u)
                    for pr in pr_list:
                        pdim = pr.get("probability_distribution") or {}
                        su = pdim.get("pair_units", {})
                        rows.append(
                            pr["pattern_str"]
                            + "\t役牌对副\t"
                            + turn_str
                            + "\t"
                            + "\t".join(f"{su.get(i, 0):.2f}" for i in range(4))
                        )
                else:
                    pattern = result.get("query_pattern_str", "") or "-".join(
                        result.get("query_pattern", [])
                    )
                    pdim = result.get("probability_distribution") or {}
                    sk = pdim.get("kinds", {})
                    rows.append(
                        pattern
                        + "\t役牌种类\t"
                        + turn_str
                        + "\t"
                        + "\t".join(f"{sk.get(i, 0):.2f}" for i in range(6))
                    )
                    rows.append("")
                    rows.append(hdr_u)
                    su = pdim.get("pair_units", {})
                    rows.append(
                        pattern
                        + "\t役牌对副\t"
                        + turn_str
                        + "\t"
                        + "\t".join(f"{su.get(i, 0):.2f}" for i in range(4))
                    )
            else:
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
                        if pr.get("multi_target") and pr.get("target_tiles"):
                            for tk in pr["target_tiles"]:
                                pt = (pr.get("probability_distribution") or {}).get(tk, {})
                                rows.append(_row(pr["pattern_str"], tk, pt))
                        else:
                            prob = pr.get("probability_distribution", {})
                            target = "听牌" if use_tenpai else pr.get("target", "")
                            rows.append(_row(pr["pattern_str"], target, prob))
                else:
                    prob = result.get("probability_distribution", {})
                    pattern = result.get("query_pattern_str", "") or "-".join(
                        result.get("query_pattern", [])
                    )
                    target = "听牌" if use_tenpai else result.get("target_tile", "")
                    multi_target = result.get("multi_target", False)
                    if multi_target:
                        for tk in result.get("target_tiles", []):
                            pt = prob.get(tk, {})
                            rows.append(_row(pattern, tk, pt))
                    else:
                        rows.append(_row(pattern, target, prob))

        rows.append("")
        rows.append(f"总匹配样本数\t{result.get('total_matches', 0)}")
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
                if item.layout():
                    _clear_layout(item.layout())
                if item.widget():
                    item.widget().deleteLater()

        _clear_layout(self.multi_merge_layout)

        use_tenpai = self.last_query_result and self.last_query_result.get("analysis_target") == "tenpai"
        use_instant = self.last_query_result and self.last_query_result.get("analysis_target") == "deal_in_instant"
        header_row = QHBoxLayout()
        if use_instant:
            header_lbl = "勾选参与合并的模式（按目标牌合并）:"
        else:
            header_lbl = "勾选合并（目标牌相同）:" if not use_tenpai else "勾选合并:"
        header_row.addWidget(QLabel(header_lbl))
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
        self.merged_result_label.setMinimumHeight(60)
        merge_result_scroll = QScrollArea()
        merge_result_scroll.setWidget(self.merged_result_label)
        merge_result_scroll.setWidgetResizable(True)
        merge_result_scroll.setFrameShape(QFrame.NoFrame)
        merge_result_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        merge_result_scroll.setMinimumHeight(64)
        merge_result_scroll.setMaximumHeight(200)
        merge_display_row.addWidget(merge_result_scroll, 1)
        copy_merge_btn = QPushButton("复制合并")
        copy_merge_btn.setFixedWidth(70)
        copy_merge_btn.setToolTip("将合并结果复制到剪贴板（Tab 分隔，可粘贴到 Excel）")
        copy_merge_btn.clicked.connect(self._copy_merged_result)
        merge_display_row.addWidget(copy_merge_btn)
        copy_per_mode_btn = QPushButton("复制各模式")
        copy_per_mode_btn.setFixedWidth(78)
        copy_per_mode_btn.setToolTip("将各模式分别的即时铳率数据复制到剪贴板（Tab 分隔，可粘贴到 Excel）")
        copy_per_mode_btn.clicked.connect(self._copy_per_mode_result)
        merge_display_row.addWidget(copy_per_mode_btn)
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
        use_instant = result.get("analysis_target") == "deal_in_instant"
        if use_instant:
            # 按目标牌合并：对每个目标牌汇总勾选模式的 hits/points
            target_tiles = checked[0].get("target_tiles") or []
            lines = [f"按目标牌合并：总匹配 {_fmt_int(total_matches)} 次"]
            for tk in target_tiles:
                hits_sum = 0
                points_sum = 0
                for pr in checked:
                    mis = pr.get("multi_instant_stats") or {}
                    s = mis.get(tk, {})
                    hits_sum += s.get("hits", 0)
                    points_sum += s.get("points", 0)
                rate = hits_sum / total_matches if total_matches > 0 else 0.0
                p_avg = points_sum / hits_sum if hits_sum > 0 else 0.0
                intensity = rate * p_avg
                lines.append(
                    f"  {tk}: 铳率 {rate:.2%} ({_fmt_int(hits_sum)} 例) | 平均铳点 {p_avg:.1f} | 铳度 {intensity:.2f}"
                )
            self.merged_result_label.setText("\n".join(lines))
            tr = result.get("turn_range")
            turn_str = f"{tr[0]}-{tr[1]}巡" if tr else "不限"
            header = "舍牌模式\t目标牌\t巡目范围\t铳率\t可铳样本\t平均铳点\t铳度"
            excel_rows = [header]
            for tk in target_tiles:
                hits_sum = sum(
                    (pr.get("multi_instant_stats") or {}).get(tk, {}).get("hits", 0) for pr in checked
                )
                points_sum = sum(
                    (pr.get("multi_instant_stats") or {}).get(tk, {}).get("points", 0) for pr in checked
                )
                rate = hits_sum / total_matches if total_matches > 0 else 0.0
                p_avg = points_sum / hits_sum if hits_sum > 0 else 0.0
                intensity = rate * p_avg
                pattern_merged = "【合并】"
                excel_rows.append(
                    f"{pattern_merged}\t{tk}\t{turn_str}\t{rate:.2%}\t{hits_sum}\t{p_avg:.1f}\t{intensity:.2f}"
                )
            excel_rows.append("")
            excel_rows.append(f"总匹配样本数\t{total_matches}")
            excel_rows.append(f"分析耗时(秒)\t{result.get('elapsed_seconds', 0):.1f}")
            self._excel_clipboard_text = "\n".join(excel_rows)
            return
        use_tenpai = result.get("analysis_target") == "tenpai"
        use_related_tile = result.get("analysis_target") == "related_tile"
        use_yaku_hai = result.get("analysis_target") == "yaku_hai_hand"
        if use_yaku_hai:
            merge_lines = [f"合并结果（役牌手持）：总匹配 {_fmt_int(total_matches)} 次"]
            merged_k = {i: 0 for i in range(6)}
            for pr in checked:
                sub = (pr.get("target_count_distribution") or {}).get("kinds", {})
                for i in range(6):
                    merged_k[i] += sub.get(i, 0)
            seg_k = []
            for i in range(6):
                pct = (merged_k[i] / total_matches * 100) if total_matches > 0 else 0
                seg_k.append(f"{i}:{pct:.1f}%({_fmt_int(merged_k[i])}例)")
            merge_lines.append("  役牌种类(≥1枚): " + " ".join(seg_k))
            merged_u = {i: 0 for i in range(4)}
            for pr in checked:
                sub = (pr.get("target_count_distribution") or {}).get("pair_units", {})
                for i in range(4):
                    merged_u[i] += sub.get(i, 0)
            _pu_nm = ("零对", "一对", "两对", "三对及以上")
            seg_u = []
            for i in range(4):
                pct = (merged_u[i] / total_matches * 100) if total_matches > 0 else 0
                seg_u.append(f"{_pu_nm[i]}:{pct:.1f}%({_fmt_int(merged_u[i])}例)")
            merge_lines.append("  役牌对副数: " + " ".join(seg_u))
            merge_lines.append("")
            merge_lines.append(f"总匹配样本数（合计勾选）\t{total_matches}")
            self.merged_result_label.setText("\n".join(merge_lines))
            tr = result.get("turn_range")
            turn_str = f"{tr[0]}-{tr[1]}巡" if tr else "不限"
            excel_rows = ["舍牌模式\t役牌种类(≥1枚)\t巡目范围\t0\t1\t2\t3\t4\t5+"]
            for pr in checked:
                pdim = pr.get("probability_distribution") or {}
                sub_pr = pdim.get("kinds", {})
                cells = "\t".join(f"{sub_pr.get(i, 0):.2f}" for i in range(6))
                excel_rows.append(f"{pr['pattern_str']}\t役牌种类\t{turn_str}\t{cells}")
            excel_rows.append("舍牌模式\t役牌对副\t巡目范围\t零对\t一对\t两对\t三对及以上")
            for pr in checked:
                pdim = pr.get("probability_distribution") or {}
                sub_pr = pdim.get("pair_units", {})
                cells = "\t".join(f"{sub_pr.get(i, 0):.2f}" for i in range(4))
                excel_rows.append(f"{pr['pattern_str']}\t役牌对副\t{turn_str}\t{cells}")
            excel_rows.append("")
            excel_rows.append(f"总匹配样本数\t{total_matches}")
            excel_rows.append(f"分析耗时(秒)\t{result.get('elapsed_seconds', 0):.1f}")
            self._excel_clipboard_text = "\n".join(excel_rows)
            return
        # 存量多目标（如多个搭子）：分布按目标嵌套，不能与单目标一样用 d.get(0) 合并
        multi_pr_target = (
            not use_tenpai
            and not use_related_tile
            and any(pr.get("multi_target") for pr in checked)
        )
        if multi_pr_target:
            seen_tk = set()
            all_tiles = []
            for pr in checked:
                for tk in pr.get("target_tiles") or []:
                    if tk not in seen_tk:
                        seen_tk.add(tk)
                        all_tiles.append(tk)
                if not pr.get("multi_target") and pr.get("target"):
                    tk0 = pr["target"]
                    if tk0 not in seen_tk:
                        seen_tk.add(tk0)
                        all_tiles.append(tk0)
            tr = result.get("turn_range")
            turn_str = f"{tr[0]}-{tr[1]}巡" if tr else "不限"
            header = "舍牌模式\t目标牌\t巡目范围\t0张\t1张\t2张\t3张"
            merge_lines = [f"合并结果（多目标存量）：总匹配 {_fmt_int(total_matches)} 次"]
            base_rows = [header]
            for pr in checked:
                if pr.get("multi_target") and pr.get("target_tiles"):
                    for tk in pr["target_tiles"]:
                        pt = (pr.get("probability_distribution") or {}).get(tk, {})
                        base_rows.append(
                            f"{pr['pattern_str']}\t{tk}\t{turn_str}\t"
                            f"{pt.get(0, 0):.2f}\t{pt.get(1, 0):.2f}\t"
                            f"{pt.get(2, 0):.2f}\t{pt.get(3, 0):.2f}"
                        )
                else:
                    prob = pr.get("probability_distribution", {})
                    pt = pr.get("target", "")
                    base_rows.append(
                        f"{pr['pattern_str']}\t{pt}\t{turn_str}\t"
                        f"{prob.get(0, 0):.2f}\t{prob.get(1, 0):.2f}\t"
                        f"{prob.get(2, 0):.2f}\t{prob.get(3, 0):.2f}"
                    )
            for tk in all_tiles:
                merged_dist = {0: 0, 1: 0, 2: 0, 3: 0}
                for pr in checked:
                    d = pr.get("target_count_distribution") or {}
                    if pr.get("multi_target"):
                        sub = d.get(tk, {})
                    else:
                        sub = d if pr.get("target") == tk else {}
                    if isinstance(sub, dict):
                        for c in (0, 1, 2, 3):
                            merged_dist[c] += sub.get(c, 0)
                probs_tk = [
                    (merged_dist[k] / total_matches * 100) if total_matches > 0 else 0
                    for k in (0, 1, 2, 3)
                ]
                merge_lines.append(
                    f"  {tk}: 有0张 {probs_tk[0]:.1f}% 有1张 {probs_tk[1]:.1f}% "
                    f"有2张 {probs_tk[2]:.1f}% 有3张 {probs_tk[3]:.1f}%"
                )
                base_rows.append(
                    f"【合并】\t{tk}\t{turn_str}\t"
                    f"{probs_tk[0]:.2f}\t{probs_tk[1]:.2f}\t"
                    f"{probs_tk[2]:.2f}\t{probs_tk[3]:.2f}"
                )
            base_rows.append("")
            base_rows.append(f"总匹配样本数\t{total_matches}")
            base_rows.append(f"分析耗时(秒)\t{result.get('elapsed_seconds', 0):.1f}")
            self.merged_result_label.setText("\n".join(merge_lines))
            self._excel_clipboard_text = "\n".join(base_rows)
            return
        keys = [0, 1] if (use_tenpai or use_related_tile) else [0, 1, 2, 3]
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
        elif use_related_tile:
            target_label = "关联牌"
            txt = (
                f"合并结果（分析目标 {target_label}）：总匹配 {_fmt_int(total_matches)} 次\n"
                f"  非关联: {probs[0]:.1f}%  关联: {probs[1]:.1f}%"
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
        target = "听牌" if use_tenpai else ("关联牌" if use_related_tile else checked[0]['target'])
        for pr in checked:
            prob = pr.get('probability_distribution', {})
            pt = "听牌" if use_tenpai else ("关联牌" if use_related_tile else pr.get('target', ''))
            base_rows.append(f"{pr['pattern_str']}\t{pt}\t{turn_str}\t"
                f"{prob.get(0, 0):.2f}\t{prob.get(1, 0):.2f}\t{prob.get(2, 0):.2f}\t{prob.get(3, 0):.2f}")
        p0, p1 = probs[0], probs[1]
        p2 = probs[2] if len(probs) > 2 else 0
        p3 = probs[3] if len(probs) > 3 else 0
        base_rows.append(f"【合并】\t{target}\t{turn_str}\t{p0:.2f}\t{p1:.2f}\t{p2:.2f}\t{p3:.2f}")
        base_rows.append("")
        base_rows.append(f"总匹配样本数\t{total_matches}")
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
        """将场上可见枚数条目按 2 列重新排列（删除行后调用；takeAt 后勿再 removeWidget）"""
        layout = self.visible_constraint_rows_layout
        parent = self.visible_constraint_rows_widget
        if not layout or not parent:
            return
        while layout.count():
            layout.takeAt(0)
        for i, (_, _, row_widget) in enumerate(self.visible_constraint_row_refs):
            row_widget.setParent(parent)
            layout.addWidget(row_widget, i // 2, i % 2)
            row_widget.show()

    def _rebuild_hand_visible_constraint_grid(self):
        """将主分析页手牌可见枚数条目按 2 列重新排列（删除行后调用）"""
        layout = self.hand_visible_constraint_rows_layout
        parent = self.hand_visible_constraint_rows_widget
        if not layout or not parent:
            return
        while layout.count():
            layout.takeAt(0)
        for i, (_, _, row_widget) in enumerate(self.hand_visible_constraint_row_refs):
            row_widget.setParent(parent)
            layout.addWidget(row_widget, i // 2, i % 2)
            row_widget.show()

    def _add_visible_constraint_row(self):
        """添加一行场上可见枚数输入（牌 + 范围滑块），每行可并排两个条目，每个条目固定宽度"""
        # 父控件设为滚动区内容 widget，避免 takeAt/removeWidget 链导致子控件不可见
        row_widget = QWidget(self.visible_constraint_rows_widget)
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
        idx = len(self.visible_constraint_row_refs) - 1
        self.visible_constraint_rows_layout.addWidget(row_widget, idx // 2, idx % 2)
        row_widget.show()

        def do_remove():
            self.visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self.visible_constraint_row_refs.remove((tile_edit, range_slider, row_widget))
            self._rebuild_visible_constraint_grid()
        remove_btn.clicked.connect(do_remove)

    def _add_hand_visible_constraint_row(self):
        """添加一行手牌可见枚数输入（牌 + 范围滑块），对除目标玩家外的三名玩家分别判断"""
        row_widget = QWidget(self.hand_visible_constraint_rows_widget)
        row_widget.setFixedWidth(292)
        row_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 8, 2)
        row_layout.setSpacing(4)
        tile_edit = QLineEdit()
        tile_edit.setPlaceholderText("牌，如 8m")
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
        self.hand_visible_constraint_row_refs.append((tile_edit, range_slider, row_widget))
        idx = len(self.hand_visible_constraint_row_refs) - 1
        self.hand_visible_constraint_rows_layout.addWidget(row_widget, idx // 2, idx % 2)
        row_widget.show()

        def do_remove():
            if self.hand_visible_constraint_rows_layout:
                self.hand_visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self.hand_visible_constraint_row_refs = [
                r for r in self.hand_visible_constraint_row_refs if r[2] != row_widget
            ]
            self._rebuild_hand_visible_constraint_grid()
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

    def _get_hand_visible_constraints_from_ui(self) -> Dict[str, Tuple[int, int]]:
        """从手牌可见枚数行收集约束，空牌名跳过"""
        hand_visible_constraints = {}
        for tile_edit, range_slider, _ in self.hand_visible_constraint_row_refs:
            tile = tile_edit.text().strip()
            if not tile:
                continue
            min_count, max_count = range_slider.getRange()
            if min_count > max_count:
                continue
            hand_visible_constraints[tile] = (min_count, max_count)
        return hand_visible_constraints

    def _rebuild_player_visible_constraint_grid(self):
        layout = self.player_visible_constraint_rows_layout
        parent = self.player_visible_constraint_rows_widget
        if not layout or not parent:
            return
        while layout.count():
            layout.takeAt(0)
        for i, (_, _, row_widget) in enumerate(self.player_visible_constraint_row_refs):
            row_widget.setParent(parent)
            layout.addWidget(row_widget, i // 2, i % 2)
            row_widget.show()

    def _add_player_visible_constraint_row(self):
        row_widget = QWidget(self.player_visible_constraint_rows_widget)
        row_widget.setFixedWidth(292)
        row_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 2, 8, 2)
        row_layout.setSpacing(4)
        tile_edit = QLineEdit()
        tile_edit.setPlaceholderText("牌，如 5p")
        tile_edit.setFixedWidth(52)
        row_layout.addWidget(tile_edit)
        row_layout.addWidget(QLabel("合计"))
        range_slider = DiscreteRangeSlider()
        row_layout.addWidget(range_slider)
        row_layout.addWidget(QLabel("枚"))
        remove_btn = QPushButton("X")
        remove_btn.setFixedSize(32, 24)
        remove_btn.setToolTip("删除此行")
        row_layout.addWidget(remove_btn)
        self.player_visible_constraint_row_refs.append((tile_edit, range_slider, row_widget))
        idx = len(self.player_visible_constraint_row_refs) - 1
        self.player_visible_constraint_rows_layout.addWidget(row_widget, idx // 2, idx % 2)
        row_widget.show()

        def do_remove():
            self.player_visible_constraint_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
            self.player_visible_constraint_row_refs.remove((tile_edit, range_slider, row_widget))
            self._rebuild_player_visible_constraint_grid()

        remove_btn.clicked.connect(do_remove)

    def _get_player_visible_constraints_from_ui(self) -> Dict[str, Tuple[int, int]]:
        out = {}
        for tile_edit, range_slider, _ in self.player_visible_constraint_row_refs:
            tile = tile_edit.text().strip()
            if not tile:
                continue
            min_count, max_count = range_slider.getRange()
            if min_count > max_count:
                continue
            out[tile] = (min_count, max_count)
        return out

    def _show_player_visible_help(self):
        QMessageBox.information(
            self,
            "玩家可见枚数说明",
            "约束对象：达成舍牌模式的玩家（目标玩家）。\n\n"
            "合计枚数 = 该玩家在当前打出瞬间手牌中的该牌枚数（含本张打出牌，与延伸手牌对自家口径一致）"
            "+ 场上可见枚数（与「场上可见枚数」相同统计：visible_tiles，含舍牌/副露/宝牌指示等已暴露牌）。\n\n"
            "若某牌设「合计 3-3」，则手牌中该牌与场上该牌之和必须恰为 3。\n"
            "可与「场上可见」「手牌可见」同时使用；三者独立校验。",
        )

    def _show_hand_visible_help(self):
        """显示手牌可见枚数说明"""
        QMessageBox.information(
            self,
            "手牌可见枚数说明",
            "该约束针对除目标玩家（满足舍牌模式的玩家）以外的其他三名玩家。\n\n"
            "• 延伸手牌：包括该玩家当前的 13 张手牌加上牌山里的任意牌。\n"
            "• 算法逻辑：若某种牌在某玩家的延伸手牌中可能出现的枚数范围与设置的范围有交集，则视为满足。\n"
            "• 例：设置“8m 可见 2-3 枚”。若玩家 A 手里有 1 张，牌山里还有 3 张，则其延伸手牌可能含有 1-4 张 8m，范围 [1,4] 与 [2,3] 有交集，满足条件。\n"
            "• 若任一非目标玩家不满足该约束，则该样本被排除。"
        )

    # ---------- 分享串：生成可读字符串 / 从字符串恢复约束 ----------
    _SHARE_VERSION = "RHM1"

    def _build_share_string_from_main(self) -> str:
        """从主分析页当前输入生成分享串（可读的 key=value 多行）。"""
        lines = [self._SHARE_VERSION]
        for pattern_edit, target_edit, _, _, _, _ in self._pattern_row_widgets:
            pt = pattern_edit.text().strip()
            tg = target_edit.text().strip()
            if not pt:
                continue
            lines.append("模式=%s→%s" % (pt, tg or ""))
        analysis = self.analysis_target_combo.currentData() or "target_count"
        analysis_map = {
            "target_count": "目标牌存量",
            "tenpai": "是否听牌",
            "related_tile": "关联牌判断",
            "outcome": "和铳率",
            "yaku_hai_hand": "役牌手持统计",
        }
        lines.append("分析=%s" % analysis_map.get(analysis, "目标牌存量"))
        # 仅「目标牌存量」有意义；与 execute_query 中 independence_on 口径一致
        if analysis == "target_count" and getattr(self, "independence_filter_check", None):
            lines.append(
                "独立性筛选=%s" % ("是" if self.independence_filter_check.isChecked() else "否")
            )
        turn_ranges = self._get_turn_ranges_from_ui()
        if turn_ranges:
            for t_min, t_max in turn_ranges:
                lines.append("巡目范围=%d-%d" % (t_min, t_max))
        else:
            lo, hi = self.turn_range_slider.getRange()
            if lo != 1 or hi != 18:
                lines.append("巡目=%d-%d" % (lo, hi))
        if self.dora_any_radio.isChecked():
            lines.append("宝牌=不问")
        elif self.dora_irrelevant_radio.isChecked():
            lines.append("宝牌=无关目标牌")
        elif self.dora_matches_position_radio.isChecked():
            lines.append("宝牌=模式第")
            pos = self.dora_position_input.text().strip()
            if pos:
                lines.append("宝牌位置=%s" % pos)
        else:
            tile = self.dora_tile_input.text().strip()
            if tile:
                lines.append("宝牌=%s" % tile)
            else:
                lines.append("宝牌=无关目标牌")
        riichi_map = {"any": "任意", "has_riichi": "有人", "no_riichi": "无人"}
        rv = "any"
        if self.riichi_has_radio.isChecked():
            rv = "has_riichi"
        elif self.riichi_no_radio.isChecked():
            rv = "no_riichi"
        lines.append("立直=%s" % riichi_map.get(rv, "任意"))
        call_map = {"any": "任意", "has_call": "有人", "no_call": "无人"}
        cv = "any"
        if self.call_has_radio.isChecked():
            cv = "has_call"
        elif self.call_no_radio.isChecked():
            cv = "no_call"
        lines.append("副露=%s" % call_map.get(cv, "任意"))
        if self.target_no_call_check.isChecked():
            lines.append("目标无副露=是")
        lines.append("南三=%s" % ("是" if self.exclude_south3_check.isChecked() else "否"))
        lines.append("南四=%s" % ("是" if self.exclude_south4_check.isChecked() else "否"))
        prior_plain = self.prior_discard_exclusion_input.toPlainText().strip()
        if prior_plain:
            for pline in [x.strip() for x in prior_plain.splitlines() if x.strip()]:
                lines.append("前段禁打=%s" % pline)
        prior_req = self.prior_discard_required_input.text().strip()
        if prior_req:
            lines.append("前段有打=%s" % prior_req)
        for le in self.call_area_inputs:
            t = le.text().strip()
            if t:
                lines.append("副露区域=%s" % t)
        for tile, (lo, hi) in self._get_visible_constraints_from_ui().items():
            lines.append("可见=%s:%d-%d" % (tile, lo, hi))
        for tile, (lo, hi) in self._get_hand_visible_constraints_from_ui().items():
            lines.append("手牌可见=%s:%d-%d" % (tile, lo, hi))
        for tile, (lo, hi) in self._get_player_visible_constraints_from_ui().items():
            lines.append("玩家可见=%s:%d-%d" % (tile, lo, hi))
        sample_limit = self.sample_limit_input.value()
        if sample_limit != 10000:
            lines.append("样本上限=%d" % sample_limit)
        match_cap = self.matched_states_cap_spin.value()
        if match_cap != 200:
            lines.append("匹配保留=%d" % match_cap)
        return "\n".join(lines)

    def _build_share_string_from_instant(self) -> str:
        """从铳率分析页当前输入生成分享串。"""
        lines = [self._SHARE_VERSION]
        for pattern_edit, target_edit, _, _ in self._instant_pattern_row_widgets:
            pt = pattern_edit.text().strip()
            tg = target_edit.text().strip()
            if not pt or not tg:
                continue
            lines.append("模式=%s→%s" % (pt, tg))
        lines.append("分析=即时铳率")
        inst_trs = []
        seen_s = set()
        for edit in getattr(self, "_instant_turn_range_edits", []):
            line = edit.text().strip()
            if not line:
                continue
            tr = _parse_turn_range(line)
            if tr and tr not in seen_s:
                seen_s.add(tr)
                inst_trs.append(tr)
        if inst_trs:
            for t_min, t_max in inst_trs:
                lines.append("巡目范围=%d-%d" % (t_min, t_max))
        else:
            t_min = self.instant_turn_min.value()
            t_max = self.instant_turn_max.value()
            if t_min != 1 or t_max != 18:
                lines.append("巡目=%d-%d" % (t_min, t_max))
        if self.instant_dora_any_radio.isChecked():
            lines.append("宝牌=不问")
        elif self.instant_dora_irrelevant_radio.isChecked():
            lines.append("宝牌=无关目标牌")
        elif self.instant_dora_matches_position_radio.isChecked():
            lines.append("宝牌=模式第")
            pos = self.instant_dora_position_input.text().strip()
            if pos:
                lines.append("宝牌位置=%s" % pos)
        else:
            tile = self.instant_dora_tile_input.text().strip()
            if tile:
                lines.append("宝牌=%s" % tile)
            else:
                lines.append("宝牌=无关目标牌")
        riichi_map = {"any": "任意", "has_riichi": "有人", "no_riichi": "无人"}
        rv = "any"
        if self.instant_riichi_has_radio.isChecked():
            rv = "has_riichi"
        elif self.instant_riichi_no_radio.isChecked():
            rv = "no_riichi"
        lines.append("立直=%s" % riichi_map.get(rv, "任意"))
        call_map = {"any": "任意", "has_call": "有人", "no_call": "无人"}
        cv = "any"
        if self.instant_call_has_radio.isChecked():
            cv = "has_call"
        elif self.instant_call_no_radio.isChecked():
            cv = "no_call"
        lines.append("副露=%s" % call_map.get(cv, "任意"))
        if self.instant_target_no_call_check.isChecked():
            lines.append("目标无副露=是")
        lines.append("南三=%s" % ("是" if self.instant_exclude_south3_check.isChecked() else "否"))
        lines.append("南四=%s" % ("是" if self.instant_exclude_south4_check.isChecked() else "否"))
        prior_plain = self.instant_prior_discard_exclusion_input.toPlainText().strip()
        if prior_plain:
            for pline in [x.strip() for x in prior_plain.splitlines() if x.strip()]:
                lines.append("前段禁打=%s" % pline)
        prior_req = self.instant_prior_discard_required_input.text().strip()
        if prior_req:
            lines.append("前段有打=%s" % prior_req)
        for le in self.instant_call_area_inputs:
            t = le.text().strip()
            if t:
                lines.append("副露区域=%s" % t)
        for tile, (lo, hi) in self._instant_get_visible_constraints_from_ui().items():
            lines.append("可见=%s:%d-%d" % (tile, lo, hi))
        for tile, (lo, hi) in self._instant_get_hand_visible_constraints_from_ui().items():
            lines.append("手牌可见=%s:%d-%d" % (tile, lo, hi))
        for tile, (lo, hi) in self._instant_get_player_visible_constraints_from_ui().items():
            lines.append("玩家可见=%s:%d-%d" % (tile, lo, hi))
        furiten = self.instant_hypothetical_furiten_input.text().strip()
        if furiten:
            lines.append("假想振听=%s" % furiten)
        lines.append("仅理论点=%s" % ("是" if self.instant_use_theory_point_only_check.isChecked() else "否"))
        lines.append("亲家子家=%s" % ("是" if self.instant_normalize_oya_ron_to_ko_check.isChecked() else "否"))
        sample_limit = self.instant_sample_limit_input.value()
        if sample_limit != 10000:
            lines.append("样本上限=%d" % sample_limit)
        match_cap = self.instant_matched_states_cap_spin.value()
        if match_cap != 200:
            lines.append("匹配保留=%d" % match_cap)
        return "\n".join(lines)

    def _parse_share_string(self, text: str) -> Optional[Dict[str, Any]]:
        """解析分享串为字典。键为中文名，值为字符串或列表（模式/副露区域/可见可多条）。不支持版本则返回 None。"""
        text = (text or "").strip()
        if not text:
            return None
        lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
        if not lines:
            return None
        first = lines[0]
        if first == self._SHARE_VERSION:
            lines = lines[1:]
        elif "=" in first and first.split("=")[0].strip() != self._SHARE_VERSION:
            ver = first.split("=")[0].strip()
            if ver == self._SHARE_VERSION:
                pass
            else:
                return None
        data = {}
        for line in lines:
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k in ("模式", "副露区域", "可见", "手牌可见", "玩家可见", "巡目范围", "前段禁打"):
                data.setdefault(k, []).append(v)
            else:
                data[k] = v
        for k in ("模式", "副露区域", "可见", "手牌可见", "玩家可见", "巡目范围", "前段禁打"):
            if k in data and isinstance(data[k], str):
                data[k] = [data[k]]
        return data if data else None

    def _apply_share_string(self, data: Dict[str, Any]) -> bool:
        """将解析后的分享串应用到界面。若分析=即时铳率则填铳率分析页并切到该页，否则填主分析页。返回是否成功。"""
        if not data:
            return False
        is_instant = data.get("分析") == "即时铳率"
        patterns = data.get("模式") or []
        if not patterns:
            return False
        if is_instant:
            while len(self._instant_pattern_row_widgets) < len(patterns):
                self._instant_add_pattern_row()
            for idx, (pattern_edit, target_edit, _, _) in enumerate(self._instant_pattern_row_widgets):
                if idx < len(patterns):
                    s = patterns[idx]
                    if "→" in s:
                        pt, _, tg = s.partition("→")
                        pattern_edit.setText(pt.strip())
                        target_edit.setText(tg.strip())
                    else:
                        pattern_edit.setText(s.strip())
                        target_edit.clear()
                else:
                    pattern_edit.clear()
                    target_edit.clear()
            turn_range_strs = data.get("巡目范围") or []
            if isinstance(turn_range_strs, str):
                turn_range_strs = [turn_range_strs]
            turn_range_strs = [s for s in turn_range_strs if isinstance(s, str) and _parse_turn_range(s)]
            if turn_range_strs:
                row_layout = getattr(self, "_instant_turn_ranges_row_layout", None)
                add_btn = getattr(self, "_instant_add_turn_range_btn", None)
                while (
                    len(self._instant_turn_range_edits) < len(turn_range_strs)
                    and row_layout
                    and add_btn
                ):
                    self._instant_add_turn_range_pair(row_layout, add_btn)
                for i, s in enumerate(turn_range_strs):
                    if i < len(self._instant_turn_range_edits):
                        tr = _parse_turn_range(s)
                        self._instant_turn_range_edits[i].setText(
                            "%d-%d" % (tr[0], tr[1]) if tr else s
                        )
                for i in range(len(turn_range_strs), len(self._instant_turn_range_edits)):
                    self._instant_turn_range_edits[i].clear()
                all_ok = [x for x in (_parse_turn_range(s) for s in turn_range_strs) if x]
                if all_ok:
                    t_lo = min(r[0] for r in all_ok)
                    t_hi = max(r[1] for r in all_ok)
                    self.instant_turn_min.setValue(max(1, min(18, t_lo)))
                    self.instant_turn_max.setValue(max(1, min(18, t_hi)))
            else:
                t_min, t_max = 1, 18
                turn = data.get("巡目", "")
                if isinstance(turn, str) and re.match(r"^\d+-\d+$", turn):
                    a, b = turn.split("-")
                    t_min, t_max = int(a), int(b)
                self.instant_turn_min.setValue(max(1, min(18, t_min)))
                self.instant_turn_max.setValue(max(1, min(18, t_max)))
                for edit in getattr(self, "_instant_turn_range_edits", []):
                    edit.clear()
            self._apply_share_string_dora(data, instant=True)
            self._apply_share_string_riichi_call_south(data, instant=True)
            self._apply_share_string_prior_call_area(data, instant=True)
            self._apply_share_string_visible(data, instant=True)
            self.instant_hypothetical_furiten_input.setText((data.get("假想振听") or "").strip())
            self.instant_use_theory_point_only_check.setChecked((data.get("仅理论点") or "是") == "是")
            self.instant_normalize_oya_ron_to_ko_check.setChecked((data.get("亲家子家") or "否") == "是")
            if "样本上限" in data:
                try:
                    self.instant_sample_limit_input.setValue(max(100, min(10000000, int(data["样本上限"]))))
                except (ValueError, TypeError):
                    pass
            if "匹配保留" in data:
                try:
                    self.instant_matched_states_cap_spin.setValue(max(1, min(2000, int(data["匹配保留"]))))
                except (ValueError, TypeError):
                    pass
            self._sync_instant_constraints_to_main()
            self.main_tab.setCurrentIndex(1)
        else:
            while len(self._pattern_row_widgets) < len(patterns):
                self._add_pattern_row()
            for idx, (pattern_edit, target_edit, _, _, _, _) in enumerate(self._pattern_row_widgets):
                if idx < len(patterns):
                    s = patterns[idx]
                    if "→" in s:
                        pt, _, tg = s.partition("→")
                        pattern_edit.setText(pt.strip())
                        target_edit.setText((tg or "").strip())
                    else:
                        pattern_edit.setText(s.strip())
                        target_edit.clear()
                else:
                    pattern_edit.clear()
                    target_edit.clear()
            analysis_map = {
                "目标牌存量": "target_count",
                "是否听牌": "tenpai",
                "关联牌判断": "related_tile",
                "和铳率": "outcome",
                "役牌手持统计": "yaku_hai_hand",
            }
            target = analysis_map.get(data.get("分析", "目标牌存量"), "target_count")
            for i in range(self.analysis_target_combo.count()):
                if self.analysis_target_combo.itemData(i) == target:
                    self.analysis_target_combo.setCurrentIndex(i)
                    break
            # 分享串中的搭子独立性筛选（仅目标牌存量生效）
            if getattr(self, "independence_filter_check", None):
                raw_ind = (data.get("独立性筛选") or "否")
                if isinstance(raw_ind, str):
                    ind = raw_ind.strip()
                else:
                    ind = "否"
                want_on = ind == "是" or ind.lower() in ("1", "true", "yes", "开", "on")
                if target == "target_count":
                    self.independence_filter_check.setChecked(want_on)
                else:
                    self.independence_filter_check.setChecked(False)
            turn_range_strs = data.get("巡目范围") or []
            if isinstance(turn_range_strs, str):
                turn_range_strs = [turn_range_strs]
            turn_range_strs = [s for s in turn_range_strs if isinstance(s, str) and _parse_turn_range(s)]
            if turn_range_strs:
                n_needed = len(turn_range_strs)
                row_layout = getattr(self, "_turn_ranges_row_layout", None)
                add_btn = getattr(self, "_add_turn_range_btn", None)
                while len(self._turn_range_edits) < n_needed and row_layout and add_btn:
                    self._add_turn_range_pair(row_layout, add_btn)
                for i, s in enumerate(turn_range_strs):
                    if i < len(self._turn_range_edits):
                        tr = _parse_turn_range(s)
                        self._turn_range_edits[i].setText("%d-%d" % (tr[0], tr[1]) if tr else s)
                for i in range(n_needed, len(self._turn_range_edits)):
                    self._turn_range_edits[i].clear()
                all_ok = [_parse_turn_range(s) for s in turn_range_strs]
                all_ok = [x for x in all_ok if x]
                if all_ok:
                    t_min = min(r[0] for r in all_ok)
                    t_max = max(r[1] for r in all_ok)
                    self.turn_range_slider.setRange(t_min, t_max)
            else:
                turn = data.get("巡目", "")
                if isinstance(turn, str) and re.match(r"^\d+-\d+$", turn):
                    a, b = turn.split("-")
                    t_min, t_max = int(a), int(b)
                    self.turn_range_slider.setRange(t_min, t_max)
                    for edit in self._turn_range_edits:
                        edit.clear()
                    if self._turn_range_edits:
                        self._turn_range_edits[0].setText("%d-%d" % (t_min, t_max))
            self._apply_share_string_dora(data, instant=False)
            self._apply_share_string_riichi_call_south(data, instant=False)
            self._apply_share_string_prior_call_area(data, instant=False)
            self._apply_share_string_visible(data, instant=False)
            if "样本上限" in data:
                try:
                    self.sample_limit_input.setValue(max(100, min(10000000, int(data["样本上限"]))))
                except (ValueError, TypeError):
                    pass
            if "匹配保留" in data:
                try:
                    self.matched_states_cap_spin.setValue(max(1, min(2000, int(data["匹配保留"]))))
                except (ValueError, TypeError):
                    pass
            self.main_tab.setCurrentIndex(0)
        QTimer.singleShot(0, self._on_analysis_target_changed)
        return True

    def _apply_share_string_dora(self, data: Dict[str, Any], instant: bool):
        if instant:
            iany, ir, isp, ipos = self.instant_dora_any_radio, self.instant_dora_irrelevant_radio, self.instant_dora_specific_radio, self.instant_dora_position_input
            itile = self.instant_dora_tile_input
            imatch = self.instant_dora_matches_position_radio
        else:
            iany, ir, isp, ipos = self.dora_any_radio, self.dora_irrelevant_radio, self.dora_specific_radio, self.dora_position_input
            itile = self.dora_tile_input
            imatch = self.dora_matches_position_radio
        dora = data.get("宝牌", "无关")
        if dora == "不问":
            iany.setChecked(True)
            itile.clear()
            ipos.clear()
        elif dora in ("无关", "无关目标牌"):
            ir.setChecked(True)
            itile.clear()
            ipos.clear()
        elif dora == "模式第":
            imatch.setChecked(True)
            ipos.setText((data.get("宝牌位置") or "").strip())
            itile.clear()
        else:
            isp.setChecked(True)
            itile.setText(dora.strip())
            ipos.clear()

    def _apply_share_string_riichi_call_south(self, data: Dict[str, Any], instant: bool):
        rmap = {"任意": "any", "有人": "has_riichi", "无人": "no_riichi"}
        cmap = {"任意": "any", "有人": "has_call", "无人": "no_call"}
        riichi = rmap.get(data.get("立直", "任意"), "any")
        call = cmap.get(data.get("副露", "任意"), "any")
        if instant:
            ra, rh, rn = self.instant_riichi_any_radio, self.instant_riichi_has_radio, self.instant_riichi_no_radio
            ca, ch, cn = self.instant_call_any_radio, self.instant_call_has_radio, self.instant_call_no_radio
            tnc = self.instant_target_no_call_check
            s3, s4 = self.instant_exclude_south3_check, self.instant_exclude_south4_check
        else:
            ra, rh, rn = self.riichi_any_radio, self.riichi_has_radio, self.riichi_no_radio
            ca, ch, cn = self.call_any_radio, self.call_has_radio, self.call_no_radio
            tnc = self.target_no_call_check
            s3, s4 = self.exclude_south3_check, self.exclude_south4_check
        ra.setChecked(riichi == "any")
        rh.setChecked(riichi == "has_riichi")
        rn.setChecked(riichi == "no_riichi")
        ca.setChecked(call == "any")
        ch.setChecked(call == "has_call")
        cn.setChecked(call == "no_call")
        tnc.setChecked((data.get("目标无副露") or "否") == "是")
        s3.setChecked((data.get("南三") or "否") == "是")
        s4.setChecked((data.get("南四") or "否") == "是")

    def _apply_share_string_prior_call_area(self, data: Dict[str, Any], instant: bool):
        praw = data.get("前段禁打")
        if isinstance(praw, list):
            prior = "\n".join(str(x).strip() for x in praw if str(x).strip())
        else:
            prior = (str(praw).strip() if praw else "")
        prior_req = (data.get("前段有打") or "").strip()
        areas = data.get("副露区域")
        if not isinstance(areas, list):
            areas = [areas] if areas else []
        areas = [str(x).strip() for x in areas if x][:4]
        if instant:
            self.instant_prior_discard_exclusion_input.setPlainText(prior)
            self.instant_prior_discard_required_input.setText(prior_req)
            for i, le in enumerate(self.instant_call_area_inputs):
                le.setText(areas[i] if i < len(areas) else "")
        else:
            self.prior_discard_exclusion_input.setPlainText(prior)
            self.prior_discard_required_input.setText(prior_req)
            for i, le in enumerate(self.call_area_inputs):
                le.setText(areas[i] if i < len(areas) else "")

    def _apply_share_string_visible(self, data: Dict[str, Any], instant: bool):
        vis = data.get("可见") or []
        if not isinstance(vis, list):
            vis = [vis] if vis else []
        entries = []
        for v in vis:
            v = str(v).strip()
            if not v:
                continue
            if ":" in v:
                tile, _, rng = v.partition(":")
                tile = tile.strip()
                rng = rng.strip()
                if re.match(r"^\d+-\d+$", rng):
                    lo, hi = int(rng.split("-")[0]), int(rng.split("-")[1])
                    entries.append((tile, max(0, min(4, lo)), max(0, min(4, hi))))
            else:
                parts = v.split()
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    entries.append((parts[0], int(parts[1]), int(parts[2])))

        h_vis = data.get("手牌可见") or []
        if not isinstance(h_vis, list):
            h_vis = [h_vis] if h_vis else []
        h_entries = []
        for v in h_vis:
            v = str(v).strip()
            if not v:
                continue
            if ":" in v:
                tile, _, rng = v.partition(":")
                tile = tile.strip()
                rng = rng.strip()
                if re.match(r"^\d+-\d+$", rng):
                    lo, hi = int(rng.split("-")[0]), int(rng.split("-")[1])
                    h_entries.append((tile, max(0, min(4, lo)), max(0, min(4, hi))))
            else:
                parts = v.split()
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    h_entries.append((parts[0], int(parts[1]), int(parts[2])))

        p_vis = data.get("玩家可见") or []
        if not isinstance(p_vis, list):
            p_vis = [p_vis] if p_vis else []
        p_entries = []
        for v in p_vis:
            v = str(v).strip()
            if not v:
                continue
            if ":" in v:
                tile, _, rng = v.partition(":")
                tile = tile.strip()
                rng = rng.strip()
                if re.match(r"^\d+-\d+$", rng):
                    lo, hi = int(rng.split("-")[0]), int(rng.split("-")[1])
                    p_entries.append((tile, max(0, min(4, lo)), max(0, min(4, hi))))
            else:
                parts = v.split()
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    p_entries.append((parts[0], int(parts[1]), int(parts[2])))

        if instant:
            # 场上可见
            for _, _, row_widget in list(self.instant_visible_constraint_row_refs):
                self.instant_visible_constraint_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
            self.instant_visible_constraint_row_refs.clear()
            for tile, lo, hi in entries:
                self._instant_add_visible_constraint_row()
                tile_edit, range_slider, _ = self.instant_visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(lo, hi)
            if not entries:
                self._instant_add_visible_constraint_row()
            # 手牌可见
            for _, _, row_widget in list(self.instant_hand_visible_constraint_row_refs):
                self.instant_hand_visible_constraint_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
            self.instant_hand_visible_constraint_row_refs.clear()
            for tile, lo, hi in h_entries:
                self._instant_add_hand_visible_constraint_row()
                tile_edit, range_slider, _ = self.instant_hand_visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(lo, hi)
            if not h_entries:
                self._instant_add_hand_visible_constraint_row()
            for _, _, row_widget in list(self.instant_player_visible_constraint_row_refs):
                self.instant_player_visible_constraint_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
            self.instant_player_visible_constraint_row_refs.clear()
            for tile, lo, hi in p_entries:
                self._instant_add_player_visible_constraint_row()
                tile_edit, range_slider, _ = self.instant_player_visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(lo, hi)
            if not p_entries:
                self._instant_add_player_visible_constraint_row()
        else:
            # 场上可见
            for _, _, row_widget in list(self.visible_constraint_row_refs):
                self.visible_constraint_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
            self.visible_constraint_row_refs.clear()
            for tile, lo, hi in entries:
                self._add_visible_constraint_row()
                tile_edit, range_slider, _ = self.visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(lo, hi)
            if not entries:
                self._add_visible_constraint_row()
            # 手牌可见
            for _, _, row_widget in list(self.hand_visible_constraint_row_refs):
                self.hand_visible_constraint_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
            self.hand_visible_constraint_row_refs.clear()
            for tile, lo, hi in h_entries:
                self._add_hand_visible_constraint_row()
                tile_edit, range_slider, _ = self.hand_visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(lo, hi)
            if not h_entries:
                self._add_hand_visible_constraint_row()
            for _, _, row_widget in list(self.player_visible_constraint_row_refs):
                self.player_visible_constraint_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
            self.player_visible_constraint_row_refs.clear()
            for tile, lo, hi in p_entries:
                self._add_player_visible_constraint_row()
                tile_edit, range_slider, _ = self.player_visible_constraint_row_refs[-1]
                tile_edit.setText(tile)
                range_slider.setRange(lo, hi)
            if not p_entries:
                self._add_player_visible_constraint_row()

    def _show_generate_share_dialog(self, from_instant: bool):
        """弹出对话框展示分享串并复制到剪贴板。from_instant 为 True 时从铳率分析页生成。"""
        s = self._build_share_string_from_instant() if from_instant else self._build_share_string_from_main()
        QApplication.clipboard().setText(s)
        dlg = QDialog(self)
        dlg.setWindowTitle("分享串（已复制到剪贴板）")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("以下内容已复制到剪贴板，可粘贴到「从分享串导入」或分享给他人："))
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(s)
        te.setMinimumSize(400, 180)
        layout.addWidget(te)
        copy_btn = QPushButton("再次复制")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(s))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        btn_row = QHBoxLayout()
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(close_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        dlg.exec_()

    def _show_import_share_dialog(self):
        """弹出输入框供用户粘贴分享串，解析后应用到对应页并切换标签。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("从分享串导入")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("请粘贴之前生成的分享串（多行 key=value 格式）："))
        te = QTextEdit()
        te.setPlaceholderText("例：\nRHM1\n模式=7s-9s→6s\n分析=目标牌存量\n独立性筛选=是\n巡目=1-6\n或 巡目范围=1-3\n巡目范围=4-6\n巡目范围=7-9\n...")
        te.setMinimumSize(420, 200)
        layout.addWidget(te)
        bbox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bbox.accepted.connect(dlg.accept)
        bbox.rejected.connect(dlg.reject)
        layout.addWidget(bbox)
        if dlg.exec_() != QDialog.Accepted:
            return
        text = te.toPlainText().strip()
        data = self._parse_share_string(text)
        if not data:
            QMessageBox.warning(self, "导入失败", "无法识别分享串格式，请确认以 RHM1 开头且包含「模式=」行。")
            return
        if self._apply_share_string(data):
            QMessageBox.information(self, "导入成功", "已根据分享串填充舍牌模式与约束。")
        else:
            QMessageBox.warning(self, "导入失败", "分享串中未包含有效的舍牌模式。")

    def execute_query(self):
        """执行查询"""
        forced_target = self._forced_analysis_target
        self._forced_analysis_target = None
        analysis_target = forced_target or (self.analysis_target_combo.currentData() or "target_count")
        use_tenpai = (analysis_target == "tenpai")
        use_instant = (analysis_target == "deal_in_instant")
        use_outcome = (analysis_target == "outcome")
        use_related_tile = (analysis_target == "related_tile")
        use_yaku_hai = (analysis_target == "yaku_hai_hand")
        require_target = not (use_tenpai or use_outcome or use_yaku_hai)
        query_items = self._get_pattern_items(require_target=require_target)
        if not query_items:
            msg = "请至少输入一个舍牌模式"
            if require_target:
                msg += "和对应的目标牌"
            elif use_outcome or use_yaku_hai:
                msg += "（和铳率/役牌统计无需目标牌）"
            QMessageBox.warning(self, "输入错误", msg)
            return
        first_pattern, first_target = query_items[0]
        
        # 宝牌约束
        if self.dora_any_radio.isChecked():
            dora_constraint = "any"
            dora_position_spec = []
        elif self.dora_irrelevant_radio.isChecked():
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

        # 手牌可见枚数
        hand_visible_constraints = self._get_hand_visible_constraints_from_ui()

        # 玩家可见（手牌+场上合计）
        player_visible_constraints = self._get_player_visible_constraints_from_ui()
        
        # 样本上限
        sample_limit = self.sample_limit_input.value()

        # 巡目范围：优先从输入框读取，为空则用滑块
        turn_ranges = self._get_turn_ranges_from_ui()
        if not turn_ranges:
            turn_min, turn_max = self.turn_range_slider.getRange()
            if turn_min <= turn_max:
                turn_ranges = [(turn_min, turn_max)]
        # 多巡目矩阵：和铳率仍不支持；即时铳率已与主分析对齐（GridQueryThread + deal_in_instant）
        use_grid = len(turn_ranges) > 1 and not use_outcome and not use_yaku_hai
        if (use_outcome or use_instant or use_yaku_hai) and turn_ranges:
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

        hypothetical_furiten = None
        if use_instant and getattr(self, "instant_hypothetical_furiten_input", None):
            hypothetical_furiten = self.instant_hypothetical_furiten_input.text().strip() or None
        instant_use_theory_point_only = True
        if use_instant and getattr(self, "instant_use_theory_point_only_check", None):
            instant_use_theory_point_only = self.instant_use_theory_point_only_check.isChecked()
        instant_normalize_oya_ron_to_ko = False
        if use_instant and getattr(self, "instant_normalize_oya_ron_to_ko_check", None):
            instant_normalize_oya_ron_to_ko = self.instant_normalize_oya_ron_to_ko_check.isChecked()
        # 与本次实际 analysis_target 对齐（含铳率页强制即时铳率等），避免误传独立性开关
        independence_on = (
            bool(getattr(self, "independence_filter_check", None))
            and self.independence_filter_check.isChecked()
            and analysis_target == "target_count"
        )
        params = {
            "query_items": query_items,
            "analysis_target": analysis_target,
            "instant_use_theory_point_only": instant_use_theory_point_only if use_instant else None,
            "instant_normalize_oya_ron_to_ko": instant_normalize_oya_ron_to_ko if use_instant else False,
            "visible_constraints": visible_constraints if visible_constraints else None,
            "hand_visible_constraints": hand_visible_constraints if hand_visible_constraints else None,
            "player_visible_constraints": player_visible_constraints if player_visible_constraints else None,
            "dora_constraint": dora_constraint,
            "dora_position_spec": dora_position_spec,
            "riichi_constraint": riichi_constraint,
            "call_constraint": call_constraint,
            "target_no_call": self.target_no_call_check.isChecked(),
            "call_area_constraints": call_area_constraints if call_area_constraints else None,
            "turn_range": turn_range,
            "sample_limit": sample_limit,
            "total_logs_hint": total_logs_hint,
            "matched_states_cap": matched_states_cap,
            "analysis_batch_size": analysis_batch_size,
            "exclude_south4": self.exclude_south4_check.isChecked(),
            "exclude_south3": self.exclude_south3_check.isChecked(),
            "prior_discard_exclusion": (
                self.prior_discard_exclusion_input.toPlainText().strip() or None
            ),
            "prior_discard_required": self.prior_discard_required_input.text().strip() or None,
            "hypothetical_furiten_tiles": hypothetical_furiten,
            "max_workers": self.max_workers_spin.value(),
            "gc_interval_batches": self.gc_interval_batches_spin.value(),
            "independence_filter": independence_on,
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
        # 听牌模式：全部/未听牌/听牌；关联牌模式：全部/非关联/关联；搭子模式：全部/没有/有；单张模式：全部/有0张~有3张；和铳率模式：全部/和牌/放铳/两者皆没有
        use_tenpai = self.last_query_params.get("analysis_target") == "tenpai"
        use_related_tile = self.last_query_params.get("analysis_target") == "related_tile"
        use_instant = self.last_query_params.get("analysis_target") == "deal_in_instant"
        use_outcome = self.last_query_params.get("analysis_target") == "outcome"
        use_yaku_hai = self.last_query_params.get("analysis_target") == "yaku_hai_hand"
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
        elif use_related_tile:
            if self.sample_target_combo.count() != 3 or self.sample_target_combo.itemText(1) != "非关联":
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(["全部", "非关联", "关联"])
        elif use_yaku_hai:
            # 与主统计口径一致：零对/一对/两对/三对及以上（筛选用 pair_kinds；末项为 ≥3）
            _yaku_sample_items = ["全部", "零对", "一对", "两对", "三对及以上"]
            if (
                self.sample_target_combo.count() != len(_yaku_sample_items)
                or self.sample_target_combo.itemText(1) != "零对"
            ):
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(_yaku_sample_items)
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
        elif use_related_tile:
            deal_in_filter = None
            outcome_filter = None
            target_count_filter = None if idx == 0 else (1 if idx == 2 else 0)  # 0=非关联 1=关联
        elif use_yaku_hai:
            deal_in_filter = None
            outcome_filter = None
            # idx 1～3：pair_kinds 精确为 0/1/2；idx 4：pair_kinds ≥ 3（见 YAKU_HAI_PAIR_FILTER_GE3）
            target_count_filter = None if idx == 0 else (
                YAKU_HAI_PAIR_FILTER_GE3 if idx == 4 else (idx - 1)
            )
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

        multi = self.last_query_params.get("multi_pattern", False) and len(query_items) > 1
        # 构建用于本次采样的参数（单模式用选中的 pattern/target）
        params_for_sample = {
            **self.last_query_params,
            "query_pattern": selected_pattern,
            "query_pattern_str": self._last_sample_pattern_str,
            "target_tile": selected_target,
            "pattern_index_filter": pattern_index if multi else None,  # 多模式时只保留该模式的样本
        }

        # 若有预收集的 sample_pool 且为多模式，只保留当前选中模式的样本
        sample_pool = self.last_query_result.get("sample_pool") if self.last_query_result else None
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
            elif self.last_query_params.get("analysis_target") == "yaku_hai_hand":
                target_tile = "役牌手持"  # 样本展示用
            try:
                text = format_samples_for_display(
                    samples,
                    query_str,
                    target_tile,
                    analysis_target=self.last_query_params.get("analysis_target", "target_count"),
                )
                safe_qs = (query_str or "pattern").replace("-", "_")
                dlg = SampleDialog(self, text, safe_qs)
                dlg.exec_()
            except Exception as e:
                logger.exception("格式化或展示验证样本失败")
                QMessageBox.critical(self, "生成样本失败", str(e))
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
                _is_inst_grid = result.get("analysis_target") == "deal_in_instant"
                summary = (
                    f"矩阵分析完成：{n_pat} 模式 × {n_tr} 巡目，共 {n_pat * n_tr} 格\n"
                    f"分析半庄数: {_fmt_int(result.get('total_logs_analyzed', 0))}\n"
                    f"分析耗时: {result.get('elapsed_seconds', 0):.1f} 秒\n\n"
                    + (
                        "本结果为即时铳率矩阵：「展示矩阵」中按每个目标牌分别显示铳率(%)、平均铳点、铳度；"
                        "可勾选「模式·目标」列，行末为勾选列的算术平均。主窗口「复制 Excel」导出同上（默认全选列）。\n"
                        if _is_inst_grid
                        else "点击「展示矩阵」查看每格样本数，并可勾选 0/1/2/3 张后复制 Excel 格式。"
                    )
                )
                self.result_text.setText(summary)
                self.multi_merge_widget.setVisible(False)
                self.gen_sample_btn.setEnabled(False)
                self.matrix_display_btn.setEnabled(True)
                header_col = result.get("header_col", [])
                header_row = result.get("header_row", [])
                if _is_inst_grid and result.get("instant_matrix_cells"):
                    self._excel_clipboard_text = instant_matrix_export_tsv(result, None)
                else:
                    _merge_keys = (
                        [1]
                        if result.get("analysis_target")
                        in ("tenpai", "related_tile", "deal_in_instant")
                        else [1, 2]
                    )
                    _tbl = {}
                    for (tr_idx, pat_idx), dist in result.get("table_dist", {}).items():
                        _tbl[(tr_idx, pat_idx)] = round(sum(dist.get(k, 0) for k in _merge_keys), 2)
                    _counts = result.get("table_counts", {})
                    header_two = []
                    for h in header_col:
                        header_two.append(h)
                        header_two.append(f"{h}(n)")
                    _rows = ["巡目范围\t" + "\t".join(header_two)]
                    for tr_idx, lbl in enumerate(header_row):
                        _cells = [lbl]
                        for pat_idx in range(len(header_col)):
                            v = _tbl.get((tr_idx, pat_idx), "")
                            n = _counts.get((tr_idx, pat_idx))
                            if n is not None:
                                _cells.append(str(v) if v != "" and v is not None else "")
                                _cells.append(str(n))
                            else:
                                _cells.append(str(v) if v != "" else "")
                                _cells.append("")
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
            if self.dora_any_radio.isChecked():
                dora_constraint = "any"
            elif self.dora_irrelevant_radio.isChecked():
                dora_constraint = "dora_unrelated"
            elif self.dora_matches_position_radio.isChecked():
                dora_constraint = "dora_matches_position"
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
                "hand_visible_constraints": self._get_hand_visible_constraints_from_ui() or None,
                "player_visible_constraints": self._get_player_visible_constraints_from_ui() or None,
                "riichi_constraint": riichi_constraint,
                "call_constraint": call_constraint,
                "call_area_constraints": call_area_constraints,
                "turn_range": turn_range,
                "sample_limit": self.sample_limit_input.value(),
                "is_combo": result.get("is_combo", False),
                "total_logs_hint": total_logs_hint,
                "exclude_south4": self.exclude_south4_check.isChecked(),
            "exclude_south3": self.exclude_south3_check.isChecked(),
                "prior_discard_exclusion": (
                    self.prior_discard_exclusion_input.toPlainText().strip() or None
                ),
                "prior_discard_required": self.prior_discard_required_input.text().strip() or None,
                "analysis_batch_size": self.analysis_batch_size_spin.value(),
                "gc_interval_batches": self.gc_interval_batches_spin.value(),
                "independence_filter": (
                    bool(getattr(self, "independence_filter_check", None))
                    and self.independence_filter_check.isChecked()
                    and (self.analysis_target_combo.currentData() or "") == "target_count"
                ),
            }
            self.gen_sample_btn.setEnabled(True)
            # 舍牌模式选择：多模式时列出每个模式供生成样本时选择
            self.sample_pattern_combo.clear()
            pr_list = result.get("pattern_results", []) if result.get("multi_pattern") else []
            use_tenpai = (result.get("analysis_target") == "tenpai")
            use_related_tile = (result.get("analysis_target") == "related_tile")
            use_instant = (result.get("analysis_target") == "deal_in_instant")
            if result.get("multi_pattern") and pr_list:
                use_yaku_hai = result.get("analysis_target") == "yaku_hai_hand"
                for pr in pr_list:
                    lbl = (
                        f"{pr['pattern_str']} → 听牌" if use_tenpai
                        else (f"{pr['pattern_str']} → 关联牌" if use_related_tile
                              else (f"{pr['pattern_str']} → 役牌" if use_yaku_hai else f"{pr['pattern_str']} → {pr['target']}")
                              )
                    )
                    self.sample_pattern_combo.addItem(lbl)
            else:
                first_pattern, first_target = query_items[0]
                use_yaku_hai = result.get("analysis_target") == "yaku_hai_hand"
                lbl = "-".join(first_pattern) + (
                    " → 听牌" if use_tenpai
                    else (" → 关联牌" if use_related_tile
                          else (" → 役牌" if use_yaku_hai else f" → {first_target}"))
                )
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
            elif use_related_tile:
                self.sample_target_combo.addItems(["全部", "非关联", "关联"])
            elif result.get("analysis_target") == "yaku_hai_hand":
                self.sample_target_combo.clear()
                self.sample_target_combo.addItems(
                    ["全部", "零对", "一对", "两对", "三对及以上"]
                )
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
                use_related_tile = (result.get("analysis_target") == "related_tile")
                use_instant = (result.get("analysis_target") == "deal_in_instant")
                lines = ["查询完成！\n", f"总匹配数: {_fmt_int(result['total_matches'])}\n"]
                use_yaku_hai = (result.get("analysis_target") == "yaku_hai_hand")
                for pr in pr_list:
                    pr_label = (
                        f"{pr['pattern_str']} → 听牌" if use_tenpai
                        else (f"{pr['pattern_str']} → 关联牌" if use_related_tile
                              else (f"{pr['pattern_str']} → 役牌" if use_yaku_hai else f"{pr['pattern_str']} → {pr['target']}"))
                    )
                    tcd = pr.get('target_count_distribution', {})
                    lines.append(f"  {pr_label}: {_fmt_int(pr['matches'])} 次")
                    if use_instant:
                        mis = pr.get("multi_instant_stats") or {}
                        for tk in pr.get("target_tiles") or []:
                            s = mis.get(tk, {})
                            if s:
                                lines.append(
                                    f"    {tk}: 铳率 {s.get('rate', 0):.2%} ({_fmt_int(s.get('hits', 0))} 例) | "
                                    f"平均铳点 {s.get('point_avg', 0):.1f} | 铳度 {s.get('intensity', 0):.2f}"
                                )
                        continue
                    elif use_yaku_hai:
                        pdim = pr.get("probability_distribution") or {}
                        sub_tk = tcd.get("kinds", {})
                        sub_pk = pdim.get("kinds", {})
                        seg_k = " ".join(
                            f"{i}:{sub_pk.get(i, 0):.1f}%({_fmt_int(sub_tk.get(i, 0))}例)"
                            for i in range(6)
                        )
                        lines.append(f"    役牌种类(≥1枚): {seg_k}")
                        sub_tu = tcd.get("pair_units", {})
                        sub_pu = pdim.get("pair_units", {})
                        _yl = ("零对", "一对", "两对", "三对及以上")
                        seg_u = " ".join(
                            f"{_yl[i]}:{sub_pu.get(i, 0):.1f}%({_fmt_int(sub_tu.get(i, 0))}例)"
                            for i in range(4)
                        )
                        lines.append(f"    役牌对副数: {seg_u}")
                    elif use_tenpai:
                        lines.append(
                            f"    未听牌: {pr['probability_distribution'][0]:.1f}% ({_fmt_int(tcd.get(0, 0))} 例)  "
                            f"听牌: {pr['probability_distribution'][1]:.1f}% ({_fmt_int(tcd.get(1, 0))} 例)"
                        )
                    elif use_related_tile:
                        lines.append(
                            f"    非关联: {pr['probability_distribution'][0]:.1f}% ({_fmt_int(tcd.get(0, 0))} 例)  "
                            f"关联: {pr['probability_distribution'][1]:.1f}% ({_fmt_int(tcd.get(1, 0))} 例)"
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
                    pt_label = "平均铳点（仅理论点）" if result.get("instant_use_theory_point_only", True) else "平均铳点"
                    if result.get("instant_normalize_oya_ron_to_ko"):
                        pt_label += "（亲家已按子家换算）"
                    lines.extend([
                        "",
                        f"即时铳率: {result.get('deal_in_rate', 0.0):.2%}",
                        f"可铳样本: {_fmt_int(result.get('deal_in_hits', 0))}",
                        f"{pt_label}: {result.get('deal_in_point_avg', 0.0):.1f}",
                        f"铳度: {result.get('deal_in_intensity', 0.0):.2f}",
                    ])
                    exc = result.get("excluded_due_to_hypothetical_furiten", 0)
                    if exc > 0:
                        lines.append(f"因假想振听牌被排除的案列数: {_fmt_int(exc)}")
                        by_tile = result.get("excluded_due_to_hypothetical_furiten_by_tile") or {}
                        if by_tile:
                            lines.append("  （按牌: " + "、".join(f"{t}: {_fmt_int(c)} 例" for t, c in sorted(by_tile.items())) + "）")
                result_text = "\n".join(lines)

                # 多模式：勾选与合并（即时铳率也显示合并区，按目标牌合并；复制合并即复制按目标牌合并结果）
                self._setup_multi_pattern_merge(result, pr_list)
            else:
                use_tenpai = (result.get("analysis_target") == "tenpai")
                use_related_tile = (result.get("analysis_target") == "related_tile")
                use_instant = (result.get("analysis_target") == "deal_in_instant")
                use_yaku_hai = (result.get("analysis_target") == "yaku_hai_hand")
                multi_target = result.get("multi_target", False)
                is_combo = result.get("is_combo", False)
                if use_instant and multi_target and result.get("multi_instant_stats"):
                    stats = result["multi_instant_stats"]
                    lines = [
                        f"  总样本: {_fmt_int(result.get('total_matches', 0))}\n",
                        "  各目标牌即时铳率:"
                    ]
                    for tk, s in stats.items():
                        lines.append(
                            f"    {tk}: 铳率 {s['rate']:.2%} ({_fmt_int(s['hits'])} 例) | 平均铳点 {s['point_avg']:.1f} | 铳度 {s['intensity']:.2f}"
                        )
                    exc = result.get("excluded_due_to_hypothetical_furiten", 0)
                    total_hits = sum(s.get("hits", 0) for s in stats.values())
                    if exc > 0:
                        lines.append(f"  因假想振听牌被排除的案列数: {_fmt_int(exc)}")
                        by_tile = result.get("excluded_due_to_hypothetical_furiten_by_tile") or {}
                        if by_tile:
                            lines.append("  （按牌: " + "、".join(f"{t}: {_fmt_int(c)} 例" for t, c in sorted(by_tile.items())) + "）")
                        if total_hits == 0:
                            lines.append("  （被排除的案列均为可铳匹配，排除后各目标铳率为 0%。）")
                    dist_text = "\n".join(lines)
                    target_label = f"目标: {result['target_tile']} (多目标即时铳率)"
                elif use_instant:
                    exc = result.get("excluded_due_to_hypothetical_furiten", 0)
                    pt_label = "平均铳点（仅理论点）" if result.get("instant_use_theory_point_only", True) else "平均铳点"
                    if result.get("instant_normalize_oya_ron_to_ko"):
                        pt_label += "（亲家已按子家换算）"
                    dist_text = (
                        f"  总样本: {_fmt_int(result.get('total_matches', 0))}\n"
                        f"  可铳样本: {_fmt_int(result.get('deal_in_hits', 0))}\n"
                        f"  即时铳率: {result.get('deal_in_rate', 0.0):.2%}\n"
                        f"  {pt_label}: {result.get('deal_in_point_avg', 0.0):.1f}\n"
                        f"  铳度: {result.get('deal_in_intensity', 0.0):.2f}"
                    )
                    if exc > 0:
                        dist_text += f"\n  因假想振听牌被排除的案列数: {_fmt_int(exc)}"
                        by_tile = result.get("excluded_due_to_hypothetical_furiten_by_tile") or {}
                        if by_tile:
                            dist_text += "\n  （按牌: " + "、".join(f"{t}: {_fmt_int(c)} 例" for t, c in sorted(by_tile.items())) + "）"
                    target_label = f"目标: {result['target_tile']} (即时铳率)"
                elif use_tenpai:
                    dist_text = (
                        f"  未听牌: {result['probability_distribution'][0]:.2f}% ({_fmt_int(result['target_count_distribution'][0])} 例)\n"
                        f"  听牌: {result['probability_distribution'][1]:.2f}% ({_fmt_int(result['target_count_distribution'][1])} 例)"
                    )
                    target_label = "分析目标: 听牌"
                elif use_related_tile:
                    dist_text = (
                        f"  非关联: {result['probability_distribution'][0]:.2f}% ({_fmt_int(result['target_count_distribution'][0])} 例)\n"
                        f"  关联: {result['probability_distribution'][1]:.2f}% ({_fmt_int(result['target_count_distribution'][1])} 例)"
                    )
                    target_label = "分析目标: 关联牌"
                elif use_yaku_hai:
                    tcd = result.get("target_count_distribution", {})
                    pdim = result.get("probability_distribution", {})
                    sub_tk = tcd.get("kinds", {})
                    sub_pk = pdim.get("kinds", {})
                    line_k = "  役牌种类(≥1枚，自风/场风/三元按座): " + " ".join(
                        f"{i}:{sub_pk.get(i, 0):.2f}% ({_fmt_int(sub_tk.get(i, 0))} 例)"
                        for i in range(6)
                    )
                    sub_tu = tcd.get("pair_units", {})
                    sub_pu = pdim.get("pair_units", {})
                    _yl = ("零对", "一对", "两对", "三对及以上")
                    line_u = "  役牌对副数(每种≥2枚计1副，刻子仍计1副): " + " ".join(
                        f"{_yl[i]}:{sub_pu.get(i, 0):.2f}% ({_fmt_int(sub_tu.get(i, 0))} 例)"
                        for i in range(4)
                    )
                    dist_text = line_k + "\n" + line_u
                    target_label = "分析目标: 役牌手持统计（无需填目标牌；种类 0–5 为桶上限）"
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
