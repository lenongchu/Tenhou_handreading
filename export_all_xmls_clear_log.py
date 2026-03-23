"""
无筛选全量导出 logs.log（GZIP 压缩的 mjlog XML），并可选择在导出成功后清空 log 列以缩小库体积。

安全流程（建议按顺序执行）:
  1. 先备份数据库（copy tenhou.db），并确认已有 log_json 时分析不依赖 XML（主分析优先读 log_json）。
  2. py export_all_xmls_clear_log.py --output D:\\目标盘\\xmls --dry-run
  3. 试导出少量: --limit 10（不写库）
  4. 全量仅导出: py export_all_xmls_clear_log.py --output D:\\...
  5. 抽查输出目录中 .xml 可正常打开后，再执行清空:
     py export_all_xmls_clear_log.py --output D:\\... --clear-log --confirm-clear
     （若目标文件已存在会跳过写入且不会清空该行 log，除非配合 --overwrite）

注意:
  - 仅清空 log（BLOB），不删 log_json；无 log_json 的记录清空 log 后将无法从库内恢复 XML。
  - SQLite 清空后需 VACUUM 才能真正缩小文件；VACUUM 耗时长且会锁库，请空闲时执行。

用法:
  py export_all_xmls_clear_log.py [--db PATH] --output DIR [选项]
"""

from __future__ import annotations

import argparse
import configparser
import gzip
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 默认输出目录（可改为你的大容量盘路径）
DEFAULT_OUTPUT_DIR = r"E:\Cursor\Tenhou data\exported_xmls_all"


def _get_db_path() -> str:
    """从 config.ini 读取数据库路径，若无则使用项目内默认。"""
    root = Path(__file__).resolve().parent
    config_file = root / "config.ini"
    if config_file.exists():
        try:
            cfg = configparser.ConfigParser()
            cfg.read(config_file, encoding="utf-8")
            path = cfg.get("Database", "path", fallback=None)
            if path and path.strip():
                return path.strip()
        except Exception as e:
            logger.debug("读取 config.ini 失败: %s", e)
    return str(root / "data" / "tenhou.db")


def export_all_xmls(
    db_path: str,
    output_dir: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    clear_log: bool = False,
    confirm_clear: bool = False,
    overwrite: bool = False,
    vacuum_after: bool = False,
    batch_fetch: int = 500,
) -> dict:
    """
    全量导出 XML（无质量筛选）；可选在每条成功写入后清空该行 log。

    Returns:
        统计字典：exported, skipped_bad_blob, skipped_exists, db_cleared, errors
    """
    # 清空 log 必须显式二次确认，避免误删
    if clear_log and not dry_run and not confirm_clear:
        raise ValueError(
            "使用 --clear-log 时必须同时传入 --confirm-clear（请先备份数据库并确认导出无误）"
        )

    out_path = Path(output_dir)
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)
        logger.info("输出目录: %s", out_path.resolve())

    conn = sqlite3.connect(db_path, timeout=300)
    cur = conn.cursor()

    # 统计待处理条数（仅含非空 log）
    cur.execute(
        "SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''"
    )
    total = cur.fetchone()[0]
    logger.info("待处理（log 非空）: %s 条", f"{total:,}")

    stats = {
        "exported": 0,
        "skipped_bad_blob": 0,
        "skipped_exists": 0,
        "db_cleared": 0,
        "errors": 0,
    }

    offset = 0
    processed = 0

    while True:
        cur.execute(
            """
            SELECT id, log FROM logs
            WHERE log IS NOT NULL AND log != ''
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (batch_fetch, offset),
        )
        rows = cur.fetchall()
        if not rows:
            break

        for log_id, log_blob in rows:
            if limit is not None and stats["exported"] >= limit:
                break

            processed += 1
            if processed % 2000 == 0:
                logger.info(
                    "进度: 已扫 %s | 已导出 %s | 跳过(坏数据/已存在) %s | 已清空 log %s",
                    f"{processed:,}",
                    f"{stats['exported']:,}",
                    f"{stats['skipped_bad_blob'] + stats['skipped_exists']:,}",
                    f"{stats['db_cleared']:,}",
                )

            out_file = out_path / f"{log_id}.xml"

            # 正式写入时：目标已存在则默认跳过，且不清空 DB（避免误删未校验的旧文件）
            if not dry_run and out_file.exists() and not overwrite:
                stats["skipped_exists"] += 1
                continue

            try:
                if not isinstance(log_blob, (bytes, memoryview)):
                    stats["skipped_bad_blob"] += 1
                    continue
                raw = bytes(log_blob)
                if not raw:
                    stats["skipped_bad_blob"] += 1
                    continue
                try:
                    xml_content = gzip.decompress(raw).decode("utf-8")
                except Exception:
                    stats["skipped_bad_blob"] += 1
                    continue
            except Exception as e:
                logger.warning("读取/解压失败 id=%s: %s", log_id, e)
                stats["errors"] += 1
                continue

            if not dry_run:
                try:
                    out_file.write_text(xml_content, encoding="utf-8")
                except Exception as e:
                    logger.warning("写入文件失败 id=%s: %s", log_id, e)
                    stats["errors"] += 1
                    continue

                # 仅在写入成功后更新数据库，避免「库已空、盘无文件」
                if clear_log:
                    cur.execute("UPDATE logs SET log = NULL WHERE id = ?", (log_id,))
                    stats["db_cleared"] += 1

            stats["exported"] += 1

        if limit is not None and stats["exported"] >= limit:
            break
        offset += batch_fetch

        # 分批提交（batch commit），降低长事务与回滚段压力
        if clear_log and not dry_run:
            conn.commit()

    if clear_log and not dry_run:
        conn.commit()

    # VACUUM 回收空闲页（file shrink）；仅建议在清空后、业务低峰执行
    if vacuum_after and clear_log and not dry_run and stats["db_cleared"] > 0:
        logger.info("开始 VACUUM（可能很慢，请勿中断）…")
        conn.execute("VACUUM")
        conn.commit()
        logger.info("VACUUM 完成")

    conn.close()

    logger.info("=" * 60)
    logger.info(
        "结束 | 导出(计数)=%s | 跳过坏数据=%s | 跳过已存在文件=%s | 清空 log 行数=%s | 异常=%s",
        f"{stats['exported']:,}",
        f"{stats['skipped_bad_blob']:,}",
        f"{stats['skipped_exists']:,}",
        f"{stats['db_cleared']:,}",
        f"{stats['errors']:,}",
    )
    if dry_run:
        logger.info("（dry-run：未写盘、未改库）")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="无筛选全量导出 XML；可选导出成功后清空 logs.log"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="数据库路径（默认 config.ini 或 data/tenhou.db）",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录（默认: {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最多处理 N 条（用于试跑）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计与遍历解压校验，不写文件、不改数据库",
    )
    parser.add_argument(
        "--clear-log",
        action="store_true",
        help="每条成功写入文件后，将该行 log 置为 NULL（须配合 --confirm-clear）",
    )
    parser.add_argument(
        "--confirm-clear",
        action="store_true",
        help="确认已备份且导出无误，允许执行清空 log",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="若目标 .xml 已存在则覆盖（否则跳过且不清空该行 log）",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="清空 log 后执行 VACUUM 以收缩库文件（很慢）",
    )

    args = parser.parse_args()
    db_path = args.db or _get_db_path()
    if not Path(db_path).exists():
        logger.error("数据库不存在: %s", db_path)
        return 1

    if args.clear_log and args.dry_run:
        logger.error("--clear-log 与 --dry-run 不能同时使用")
        return 1

    logger.info("数据库: %s", db_path)
    logger.info(
        "模式: %s",
        "dry-run（不写盘、不改库）"
        if args.dry_run
        else ("导出并清空 log" if args.clear_log else "仅全量导出"),
    )
    logger.info("=" * 60)

    try:
        export_all_xmls(
            db_path,
            args.output,
            limit=args.limit,
            dry_run=args.dry_run,
            clear_log=args.clear_log,
            confirm_clear=args.confirm_clear,
            overwrite=args.overwrite,
            vacuum_after=args.vacuum,
        )
    except ValueError as e:
        logger.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
