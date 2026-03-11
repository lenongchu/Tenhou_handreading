"""
将筛选过掉线对局的 XML 牌谱导出到指定目录

从数据库读取 log 列（gzip 压缩的 mjlog XML），应用与 clean_bye_logs 相同的质量筛选
（排除「非最后一局掉线且未重连」的对局），将合格对局以独立 .xml 文件输出。

用法:
  py export_filtered_xmls.py [--db PATH] [--output DIR] [--limit N] [--dry-run]

选项:
  --db PATH    数据库路径，默认从 config.ini 读取或 data/tenhou.db
  --output DIR 输出目录，默认 E:\\Cursor\\Tenhou data\\filtered_xmls
  --limit N    最多导出 N 局（默认全部）
  --dry-run    仅统计，不实际写入文件
"""

import argparse
import configparser
import gzip
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 默认输出目录
DEFAULT_OUTPUT_DIR = r"E:\Cursor\Tenhou data\filtered_xmls"


def _get_db_path() -> str:
    """从 config.ini 读取数据库路径，若无则使用默认值"""
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


def export_filtered_xmls(
    db_path: str,
    output_dir: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """
    导出筛选后的 XML 牌谱到指定目录。

    Returns:
        (导出数量, 跳过数量)
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from src.log_quality import check_log_quality_compressed

    import sqlite3

    out_path = Path(output_dir)
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)
        logger.info("输出目录: %s", out_path.resolve())

    conn = sqlite3.connect(db_path, timeout=120)
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''"
    )
    total = cur.fetchone()[0]
    logger.info("数据库中共有 %s 条含 XML 的对局", f"{total:,}")

    batch_size = 500
    offset = 0
    exported = 0
    skipped = 0
    processed = 0

    while True:
        cur.execute(
            """
            SELECT id, log
            FROM logs
            WHERE log IS NOT NULL AND log != ''
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (batch_size, offset),
        )
        rows = cur.fetchall()
        if not rows:
            break

        for log_id, log_blob in rows:
            if limit is not None and exported >= limit:
                break

            processed += 1
            if processed % 500 == 0:
                logger.info(
                    "已处理 %s/%s | 已导出 %s | 已跳过 %s",
                    f"{processed:,}",
                    f"{total:,}",
                    f"{exported:,}",
                    f"{skipped:,}",
                )

            try:
                if not isinstance(log_blob, bytes):
                    skipped += 1
                    continue
                try:
                    xml_content = gzip.decompress(log_blob).decode("utf-8")
                except Exception:
                    skipped += 1
                    continue

                is_valid, _reason = check_log_quality_compressed(log_blob)
                if not is_valid:
                    skipped += 1
                    continue

                if not dry_run:
                    out_file = out_path / f"{log_id}.xml"
                    out_file.write_text(xml_content, encoding="utf-8")

                exported += 1

            except Exception as e:
                logger.warning("处理 %s 失败: %s", log_id, e)
                skipped += 1

        if limit is not None and exported >= limit:
            break
        offset += batch_size

    conn.close()

    logger.info("=" * 60)
    logger.info("完成！导出 %s 条，跳过 %s 条", f"{exported:,}", f"{skipped:,}")
    return exported, skipped


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="导出筛选过掉线的 XML 牌谱到指定目录"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="数据库路径（默认从 config.ini 或 data/tenhou.db）",
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
        help="最多导出 N 局",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计，不实际写入文件",
    )
    args = parser.parse_args()

    db_path = args.db or _get_db_path()
    if not Path(db_path).exists():
        logger.error("数据库不存在: %s", db_path)
        sys.exit(1)

    logger.info("数据库: %s", db_path)
    logger.info("模式: %s", "试运行（不写入）" if args.dry_run else "正式导出")
    logger.info("=" * 60)

    export_filtered_xmls(
        db_path,
        args.output,
        limit=args.limit,
        dry_run=args.dry_run,
    )
