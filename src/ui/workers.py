"""
GUI 后台工作线程

抽离自 gui_app.py 的 QThread 子类，负责耗时计算与 I/O。
"""

import logging
from typing import List, Tuple

from PyQt5.QtCore import QThread, pyqtSignal

from ..data_downloader import DataDownloader
from ..live_analyzer import LiveAnalyzer, get_database_stats

logger = logging.getLogger(__name__)


def _fmt_int(n) -> str:
    """整数格式化，不用千分位符避免中文环境 Qt 渲染乱码"""
    return str(int(n))


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
                    self.progress.emit(
                        f"正在分析: {_fmt_int(current)}/{_fmt_int(total)} ({percent:.1f}%)"
                    )
                else:
                    self.progress.emit(f"正在分析: 已扫描 {_fmt_int(current)} 场")

            def should_cancel():
                return self._should_cancel

            result = self.analyzer.analyze_discard_pattern(
                **self.params,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
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
                    self.progress.emit(
                        f"正在分析: {_fmt_int(current)}/{_fmt_int(total)} ({percent:.1f}%)"
                    )
                else:
                    self.progress.emit(f"正在分析: 已扫描 {_fmt_int(current)} 场")

            def should_cancel():
                return self._should_cancel

            outcome_params = {
                k: v
                for k, v in self.params.items()
                if k
                in (
                    "query_pattern",
                    "target_tile",
                    "query_items",
                    "dora_constraint",
                    "dora_position_spec",
                    "visible_constraints",
                    "hand_visible_constraints",
                    "riichi_constraint",
                    "call_constraint",
                    "target_no_call",
                    "call_area_constraints",
                    "turn_range",
                    "sample_limit",
                    "exclude_south4",
                    "exclude_south3",
                    "prior_discard_exclusion",
                    "prior_discard_required",
                    "max_workers",
                )
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

    def __init__(
        self,
        analyzer: LiveAnalyzer,
        params: dict,
        sample_count: int,
        target_count_filter,
        sample_pool=None,
        target_tile_filter=None,
        outcome_filter=None,
        deal_in_filter=None,
    ):
        super().__init__()
        self.analyzer = analyzer
        self.params = params
        self.sample_count = sample_count
        self.target_count_filter = target_count_filter
        self.sample_pool = sample_pool
        self.target_tile_filter = target_tile_filter
        self.outcome_filter = outcome_filter
        self.deal_in_filter = deal_in_filter
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
                self.progress.emit(
                    f"已扫描: {_fmt_int(cur)} 场"
                    + (f"/{_fmt_int(total)}" if total > 0 else "")
                )

            # 仅传入 collect_verification_samples 声明的参数，避免 last_query_params 里多余键导致 TypeError 闪退
            _allowed_collect = {
                "query_pattern",
                "target_tile",
                "dora_constraint",
                "dora_position_spec",
                "visible_constraints",
                "hand_visible_constraints",
                "riichi_constraint",
                "call_constraint",
                "target_no_call",
                "call_area_constraints",
                "turn_range",
                "sample_limit",
                "total_logs_hint",
                "analysis_batch_size",
                "exclude_south4",
                "exclude_south3",
                "prior_discard_exclusion",
                "prior_discard_required",
                "gc_interval_batches",
                "analysis_target",
                "pattern_index_filter",
                "independence_filter",
            }
            params = {k: v for k, v in self.params.items() if k in _allowed_collect}
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


class GridQueryThread(QThread):
    """主界面矩阵分析线程：M>1 巡目时调用 analyze_discard_pattern_grid"""

    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)
    finished = pyqtSignal(bool, object)

    def __init__(
        self,
        analyzer,
        params: dict,
        patterns: List[Tuple[List[str], str]],
        turn_ranges: List[Tuple[int, int]],
        analysis_target: str = "target_count",
    ):
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
            self.progress.emit(
                f"矩阵分析中：{len(self.patterns)} 模式 × {len(self.turn_ranges)} 巡目..."
            )
            shared = {
                k: v
                for k, v in self.params.items()
                if k
                in (
                    "dora_constraint",
                    "dora_position_spec",
                    "riichi_constraint",
                    "call_constraint",
                    "target_no_call",
                    "call_area_constraints",
                    "visible_constraints",
                    "hand_visible_constraints",
                    "sample_limit",
                    "total_logs_hint",
                    "analysis_batch_size",
                    "exclude_south4",
                    "exclude_south3",
                    "prior_discard_exclusion",
                    "prior_discard_required",
                    "max_workers",
                    "gc_interval_batches",
                    "independence_filter",
                )
            }

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
            result["header_row"] = [
                f"{tmin}-{tmax}巡" for tmin, tmax in self.turn_ranges
            ]

            def _target_display(pat, tgt):
                if self.analysis_target == "tenpai":
                    return "听牌"
                if self.analysis_target == "related_tile" and tgt == "5z":
                    return "关联牌"
                return tgt

            result["header_col"] = [
                f"{'-'.join(p)}→{_target_display(p, t)}" for p, t in self.patterns
            ]
            self.finished.emit(True, result)
        except Exception as e:
            logger.exception("矩阵分析失败")
            self.finished.emit(False, str(e))


class BatchChartThread(QThread):
    """批量分析线程：依次运行 舍牌模式×巡目范围 组合"""

    progress = pyqtSignal(str)
    progress_num = pyqtSignal(int, int)
    finished = pyqtSignal(bool, object)

    def __init__(
        self,
        analyzer,
        constraint_params: dict,
        patterns: List[Tuple[List[str], str]],
        turn_ranges: List[Tuple[int, int]],
        merge_keys: List[int],
        analysis_target: str = "target_count",
    ):
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
            shared_filtered = {
                k: v
                for k, v in self.constraint_params.items()
                if k
                in (
                    "dora_constraint",
                    "dora_position_spec",
                    "riichi_constraint",
                    "call_constraint",
                    "target_no_call",
                    "call_area_constraints",
                    "visible_constraints",
                    "hand_visible_constraints",
                    "sample_limit",
                    "total_logs_hint",
                    "analysis_batch_size",
                    "exclude_south4",
                    "exclude_south3",
                    "prior_discard_exclusion",
                    "prior_discard_required",
                    "max_workers",
                    "gc_interval_batches",
                    "independence_filter",
                )
            }
            self.progress.emit(
                f"单次扫描分析 {len(self.patterns)} 模式 × {len(self.turn_ranges)} 巡目..."
            )

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
            header_row = [
                f"{tmin}-{tmax}巡" for tmin, tmax in self.turn_ranges
            ]

            def _hd_tgt(p, t):
                if self.analysis_target == "tenpai":
                    return "听牌"
                if self.analysis_target == "related_tile" and t == "5z":
                    return "关联牌"
                return t

            header_col = [
                f"{'-'.join(p)}→{_hd_tgt(p, t)}" for p, t in self.patterns
            ]
            elapsed = result.get("elapsed_seconds", 0)
            self.finished.emit(True, (table, header_row, header_col, elapsed))
        except Exception as e:
            logger.exception("批量分析失败")
            self.finished.emit(False, str(e))


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
