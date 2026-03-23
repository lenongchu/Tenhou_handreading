"""
GUI 对话框

抽离自 gui_app.py 的 QDialog 子类：样本、存档、帮助、示意图、矩阵、批量折线图。
"""

import re
import html
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Callable, TYPE_CHECKING

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QTextBrowser, QGroupBox, QComboBox, QCheckBox,
    QRadioButton, QProgressBar, QMessageBox, QDialogButtonBox, QApplication,
)
from PyQt5.QtCore import Qt

from .styles import PATTERN_HELP_HTML, TARGET_HELP_HTML
from .components import TileIllustrationWidget
from .workers import BatchChartThread

if TYPE_CHECKING:
    pass  # MainWindow 仅作类型提示，不导入避免循环依赖


class SampleDialog(QDialog):
    """样本展示对话框"""

    def __init__(self, parent, text: str, query_str: str):
        super().__init__(parent)
        self.setWindowTitle("验证样本")
        self.setMinimumSize(650, 500)
        layout = QVBoxLayout()
        self.text_edit = QTextBrowser()
        self.text_edit.setReadOnly(True)
        self.text_edit.setOpenExternalLinks(True)
        html_content = html.escape(text).replace("\n", "<br>")
        html_content = re.sub(
            r'(https://tenhou\.net/4/[^\s<]+)',
            r'<a href="\1">\1</a>',
            html_content
        )
        self.text_edit.setHtml(html_content)
        layout.addWidget(self.text_edit)
        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
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


class ArchiveViewDialog(QDialog):
    """存档结果查看对话框，点击展开显示具体结果"""

    def __init__(self, parent, entry: Dict[str, Any]):
        super().__init__(parent)
        self.setWindowTitle("存档结果")
        self.setMinimumSize(700, 500)
        layout = QVBoxLayout()
        summary = entry.get("pattern_summary", "")
        time_str = entry.get("created_at", "")[:19].replace("T", " ")
        title = QLabel(f"舍牌模式: {summary}\n存档时间: {time_str}")
        title.setStyleSheet("font-weight: bold; font-size: 12pt; color: #00d4ff;")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(entry.get("result_display_text", ""))
        layout.addWidget(self.text_edit)
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
    """麻将示意图生成对话框（弹窗形式，兼容旧入口）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("麻将示意图")
        self.setMinimumSize(520, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(TileIllustrationWidget(self))


class MatrixDisplayDialog(QDialog):
    """矩阵展示窗口：勾选 0/1/2/3 张，动态预览并复制 Excel 格式；每格显示样本数"""

    def __init__(self, parent, grid_result: dict):
        super().__init__(parent)
        self.setWindowTitle("矩阵数据展示")
        self.setMinimumSize(560, 420)
        self._result = grid_result
        self._table_dist = grid_result.get("table_dist", {})
        self._table_counts = grid_result.get("table_counts", {})
        self._header_row = grid_result.get("header_row", [])
        self._header_col = grid_result.get("header_col", [])
        self._analysis_target = grid_result.get("analysis_target", "target_count")
        self._use_tenpai = (self._analysis_target == "tenpai")
        self._use_related_tile = (self._analysis_target == "related_tile")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        use_tenpai = self._use_tenpai
        use_related_tile = self._use_related_tile
        if use_tenpai:
            labels, keys = ["未听牌", "听牌"], [0, 1]
        elif use_related_tile:
            labels, keys = ["非关联", "关联"], [0, 1]
        else:
            labels, keys = ["0张", "1张", "2张", "3张"], [0, 1, 2, 3]
        cb_row = QHBoxLayout()
        cb_row.addWidget(QLabel("勾选展示（可多选合并）:"))
        self._checkboxes = []
        for k, lbl in zip(keys, labels):
            cb = QCheckBox(lbl)
            cb.setChecked(k in (1, 2) if not (use_tenpai or use_related_tile) else True)
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
        header_two = []
        for h in header_col:
            header_two.append(h)
            header_two.append(f"{h}(n)")
        rows.append("巡目范围\t" + "\t".join(header_two))
        for tr_idx, tr_label in enumerate(header_row):
            cells = [tr_label]
            for pat_idx in range(len(header_col)):
                v = tbl.get((tr_idx, pat_idx), "")
                n = self._table_counts.get((tr_idx, pat_idx))
                if isinstance(v, (int, float)) and n is not None:
                    cells.append(str(v))
                    cells.append(str(n))
                else:
                    cells.append(str(v) if v != "" else "")
                    cells.append(str(n) if n is not None else "")
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
        header_two = []
        for h in header_col:
            header_two.append(h)
            header_two.append(f"{h}(n)")
        rows.append("巡目范围\t" + "\t".join(header_two))
        for tr_idx, tr_label in enumerate(header_row):
            cells = [tr_label]
            for pat_idx in range(len(header_col)):
                v = tbl.get((tr_idx, pat_idx), "")
                n = self._table_counts.get((tr_idx, pat_idx))
                if isinstance(v, (int, float)) and n is not None:
                    cells.append(str(v))
                    cells.append(str(n))
                else:
                    cells.append(str(v) if v != "" else "")
                    cells.append(str(n) if n is not None else "")
            rows.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(rows))
        QMessageBox.information(self, "已复制", "表格已复制到剪贴板（含每格样本数），可粘贴到 Excel 中绘制折线图。")


class BatchChartDialog(QDialog):
    """批量生成折线图数据对话框；依赖注入避免循环导入"""

    def __init__(
        self,
        parent: "MainWindow",
        *,
        load_db_status_fn: Optional[Callable[[str], Optional[dict]]] = None,
        parse_pattern_fn: Optional[Callable[[str], Optional[Tuple[List[str], str]]]] = None,
        parse_turn_range_fn: Optional[Callable[[str], Optional[Tuple[int, int]]]] = None,
    ):
        super().__init__(parent)
        self.main_window = parent
        self._load_db_status_fn = load_db_status_fn
        self._parse_pattern_fn = parse_pattern_fn
        self._parse_turn_range_fn = parse_turn_range_fn
        self.setWindowTitle("生成折线图数据")
        self.setMinimumSize(620, 520)
        self._table_data = None
        self._header_row = None
        self._header_col = None
        self.batch_thread = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        hint = QLabel("批量分析 舍牌模式×巡目范围，生成表格：行=巡目范围，列=舍牌模式，单元格=合并概率")
        hint.setStyleSheet("color: #8b949e;")
        layout.addWidget(hint)
        layout.addWidget(QLabel("舍牌模式列表（每行 模式+目标，可用空格/→/逗号分隔）:"))
        self.patterns_edit = QTextEdit()
        self.patterns_edit.setPlaceholderText("7s-9s 6s\nc0p6p-$ 6s\n3m-5m→4m\n7s 9s 6s")
        self.patterns_edit.setMaximumHeight(90)
        layout.addWidget(self.patterns_edit)
        layout.addWidget(QLabel("巡目范围列表（每行 最小-最大，支持 1-3、1~3、1 3）:"))
        self.turn_ranges_edit = QTextEdit()
        self.turn_ranges_edit.setPlaceholderText("1-3\n4-6\n7-9\n1 6\n10~12")
        self.turn_ranges_edit.setMaximumHeight(70)
        layout.addWidget(self.turn_ranges_edit)
        self.constraint_group = QGroupBox("约束条件（勾选后使用下方设置，否则用主界面）")
        self.constraint_group.setCheckable(True)
        self.constraint_group.setChecked(False)
        cg_layout = QVBoxLayout()
        dora_row = QHBoxLayout()
        self.batch_dora_any = QRadioButton("宝牌不问")
        self.batch_dora_irrelevant = QRadioButton("宝牌无关目标牌")
        self.batch_dora_irrelevant.setChecked(True)
        self.batch_dora_specific = QRadioButton("宝牌为")
        self.batch_dora_input = QLineEdit()
        self.batch_dora_input.setPlaceholderText("例: 6s")
        self.batch_dora_input.setMaximumWidth(55)
        dora_row.addWidget(self.batch_dora_any)
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
        prior_req_row = QHBoxLayout()
        prior_req_row.addWidget(QLabel("前段有打:"))
        self.batch_prior_discard_required_input = QLineEdit()
        self.batch_prior_discard_required_input.setPlaceholderText("例: [29]m-3pf")
        self.batch_prior_discard_required_input.setToolTip(
            "巡目范围开始前须出现过该舍牌模式（语法同舍牌模式）。与主模式串联：前段有打 … 舍牌模式，中间不要求"
        )
        self.batch_prior_discard_required_input.setMinimumWidth(140)
        self.batch_prior_discard_required_input.setMaximumWidth(200)
        prior_req_row.addWidget(self.batch_prior_discard_required_input)
        prior_req_row.addStretch()
        cg_layout.addLayout(prior_req_row)
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
        at_row = QHBoxLayout()
        at_row.addWidget(QLabel("分析目标:"))
        self.analysis_target_combo = QComboBox()
        self.analysis_target_combo.addItem("目标牌存量", "target_count")
        self.analysis_target_combo.addItem("是否听牌", "tenpai")
        at_row.addWidget(self.analysis_target_combo)
        at_row.addStretch()
        layout.addLayout(at_row)
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
        layout.addWidget(QLabel("结果预览（可复制）:"))
        self.result_preview = QTextEdit()
        self.result_preview.setReadOnly(True)
        self.result_preview.setMinimumHeight(120)
        layout.addWidget(self.result_preview)
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
        mw = self.main_window
        self.batch_dora_any.setChecked(getattr(mw, "dora_any_radio", None) and mw.dora_any_radio.isChecked())
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
        self.batch_prior_discard_required_input.setText(mw.prior_discard_required_input.text())
        QMessageBox.information(self, "已同步", "约束条件已从主界面同步")

    def _build_constraint_params(self) -> dict:
        mw = self.main_window
        cached = (self._load_db_status_fn(mw.db_path) if self._load_db_status_fn else None)
        if self.constraint_group.isChecked():
            prior_excl = self.batch_prior_discard_exclusion_input.text().strip() or None
            prior_req = self.batch_prior_discard_required_input.text().strip() or None
        else:
            prior_excl = mw.prior_discard_exclusion_input.text().strip() or None
            prior_req = mw.prior_discard_required_input.text().strip() or None
        base = {
            "sample_limit": mw.sample_limit_input.value(),
            "total_logs_hint": cached.get("total_logs") if cached else None,
            "matched_states_cap": mw.matched_states_cap_spin.value(),
            "analysis_batch_size": mw.analysis_batch_size_spin.value(),
            "exclude_south4": mw.exclude_south4_check.isChecked(),
            "exclude_south3": mw.exclude_south3_check.isChecked(),
            "prior_discard_exclusion": prior_excl,
            "prior_discard_required": prior_req,
            "max_workers": mw.max_workers_spin.value(),
            "gc_interval_batches": mw.gc_interval_batches_spin.value(),
            "independence_filter": (
                bool(getattr(mw, "independence_filter_check", None))
                and mw.independence_filter_check.isChecked()
                and (self.analysis_target_combo.currentData() or "") == "target_count"
            ),
        }
        if self.constraint_group.isChecked():
            if self.batch_dora_any.isChecked():
                dora = "any"
            elif self.batch_dora_irrelevant.isChecked():
                dora = "dora_unrelated"
            else:
                dora = self.batch_dora_input.text().strip() or "any"
            dora_pos = []
            riichi = "any" if self.batch_riichi_any.isChecked() else ("has_riichi" if self.batch_riichi_has.isChecked() else "no_riichi")
            call = "any" if self.batch_call_any.isChecked() else ("has_call" if self.batch_call_has.isChecked() else "no_call")
            call_area = [le.text().strip() for le in self.batch_call_area_inputs if le.text().strip()][:4]
            visible = {}
            if self.batch_use_main_constraints.isChecked():
                visible = mw._get_visible_constraints_from_ui() or {}
            base.update(dora_constraint=dora, dora_position_spec=dora_pos, riichi_constraint=riichi, call_constraint=call,
                call_area_constraints=call_area if call_area else None, visible_constraints=visible if visible else None)
        else:
            if getattr(mw, "dora_any_radio", None) and mw.dora_any_radio.isChecked():
                dora = "any"
                dora_pos = []
            elif mw.dora_irrelevant_radio.isChecked():
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
        parse_fn = self._parse_pattern_fn
        turn_fn = self._parse_turn_range_fn
        if not parse_fn or not turn_fn:
            QMessageBox.critical(self, "错误", "缺少解析函数（load_db_status_fn、parse_pattern_fn、parse_turn_range_fn）")
            return
        patterns = []
        for line in self.patterns_edit.toPlainText().strip().split("\n"):
            pt = parse_fn(line)
            if pt:
                patterns.append(pt)
        if not patterns:
            QMessageBox.warning(self, "输入错误", "请至少输入一个有效的舍牌模式（例: 7s-9s 6s 或 7s-9s→6s）")
            return
        turn_ranges = []
        for line in self.turn_ranges_edit.toPlainText().strip().split("\n"):
            tr = turn_fn(line)
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
