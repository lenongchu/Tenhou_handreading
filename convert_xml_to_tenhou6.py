"""
将 logs 表中的 XML 牌谱批量转换为 tenhou6 JSON 格式

前置条件：
  1. tenhou-paifu-to-json 或 tenhou-paifu-to-json-main 已置于项目目录
  2. 数据库 data/tenhou.db 存在且 logs 表有 log 列

用法：
  py convert_xml_to_tenhou6.py [--db PATH] [--limit N] [--batch B] [--workers W] [--skip-existing]

选项：
  --db PATH      数据库路径，默认 data/tenhou.db
  --limit N      最多转换 N 局（默认全部）
  --batch B      每批处理 B 局，默认 200
  --workers W    并行进程数，默认 CPU 核心数（建议 4-8）
  --skip-existing  跳过已有 log_json 的记录（支持断点续传）
"""

import argparse
import gzip
import logging
import multiprocessing
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _convert_one(args: Tuple[str, str, str]) -> Tuple[str, Optional[str]]:
    """
    单条转换（供多进程调用）。
    args: (log_id, xml_str, paifu_path)
    Returns: (log_id, json_str or None)
    """
    log_id, xml_str, paifu_path = args
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from src.tenhou6_adapter import xml_to_tenhou6_json
        out = xml_to_tenhou6_json(xml_str, paifu_cli_path=paifu_path)
        return (log_id, out if out else None)
    except Exception:
        return (log_id, None)


def _ensure_log_json_column(conn) -> None:
    """确保 logs 表有 log_json 列"""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(logs)")
    columns = [row[1] for row in cur.fetchall()]
    if "log_json" not in columns:
        cur.execute("ALTER TABLE logs ADD COLUMN log_json BLOB")
        conn.commit()
        logger.info("已添加 log_json 列")


def convert_new_logs_to_tenhou6(
    db_path: str,
    *,
    limit: Optional[int] = None,
    batch: int = 200,
    skip_existing: bool = True,
    workers: int = 1,
) -> tuple:
    """
    将 logs 表中未转换的 XML 转为 tenhou6 JSON。
    可由 data_downloader 在下载后调用。

    Args:
        workers: 并行进程数，1=单进程，>1=多进程并行
    Returns:
        (成功数, 失败数)
    """
    try:
        from src.tenhou6_adapter import xml_to_tenhou6_json
    except ImportError:
        logger.warning("无法导入 tenhou6_adapter，跳过转换")
        return (0, 0)

    paifu_path = _find_paifu_path()
    if not paifu_path:
        logger.warning("未找到 tenhou-paifu-to-json，跳过 tenhou6 转换")
        return (0, 0)

    import sqlite3
    conn = sqlite3.connect(str(db_path), timeout=120)
    _ensure_log_json_column(conn)
    cur = conn.cursor()

    where = "log IS NOT NULL AND log != ''"
    if skip_existing:
        where += " AND (log_json IS NULL OR log_json = '')"
    cur.execute(f"SELECT COUNT(*) FROM logs WHERE {where}")
    total = cur.fetchone()[0]
    if limit:
        total = min(total, limit)
    if total == 0:
        conn.close()
        return (0, 0)

    ok, fail, processed, offset = 0, 0, 0, 0
    use_parallel = workers > 1

    while True:
        fetch_limit = min(batch, total - processed) if limit else batch
        if limit and fetch_limit <= 0:
            break
        if skip_existing:
            cur.execute(f"SELECT id, log FROM logs WHERE {where} ORDER BY id LIMIT ?", (fetch_limit,))
        else:
            cur.execute(
                f"SELECT id, log FROM logs WHERE {where} ORDER BY id LIMIT ? OFFSET ?",
                (fetch_limit, offset),
            )
        rows = cur.fetchall()
        if not rows:
            break

        if use_parallel:
            tasks = []
            for log_id, log_blob in rows:
                if isinstance(log_blob, bytes):
                    try:
                        xml_str = gzip.decompress(log_blob).decode("utf-8")
                    except Exception:
                        xml_str = log_blob.decode("utf-8", errors="replace")
                else:
                    xml_str = str(log_blob)
                tasks.append((log_id, xml_str, paifu_path))
            with multiprocessing.Pool(workers) as pool:
                for log_id, json_str in pool.imap_unordered(_convert_one, tasks, chunksize=max(1, len(tasks) // (workers * 4))):
                    if json_str:
                        # 存 gzip 压缩以减小体积（约 3–5 倍）
                        blob = gzip.compress(json_str.encode("utf-8"))
                        cur.execute("UPDATE logs SET log_json = ? WHERE id = ?", (blob, log_id))
                        ok += 1
                    else:
                        fail += 1
        else:
            for log_id, log_blob in rows:
                try:
                    if isinstance(log_blob, bytes):
                        try:
                            xml_str = gzip.decompress(log_blob).decode("utf-8")
                        except Exception:
                            xml_str = log_blob.decode("utf-8", errors="replace")
                    else:
                        xml_str = str(log_blob)
                    json_str = xml_to_tenhou6_json(xml_str, paifu_cli_path=paifu_path)
                    if not json_str:
                        fail += 1
                        continue
                    blob = gzip.compress(json_str.encode("utf-8"))
                    cur.execute("UPDATE logs SET log_json = ? WHERE id = ?", (blob, log_id))
                    ok += 1
                except Exception:
                    fail += 1

        conn.commit()
        processed += len(rows)
        offset += len(rows)
        pct = 100 * processed / total if total else 0
        logger.info(f"已转换 {processed:,} / {total:,} ({pct:.2f}%) | 成功 {ok:,}, 失败 {fail:,}")
        sys.stdout.flush()
        if limit and processed >= limit:
            break
        if len(rows) < batch:
            break
    conn.close()
    if ok or fail:
        logger.info(f"tenhou6 转换完成: 成功 {ok:,}, 失败 {fail:,}")
    return (ok, fail)


def _find_paifu_path() -> str:
    """查找 tenhou-paifu-to-json 的 main.py 路径"""
    root = Path(__file__).parent
    for p in [
        root / "tenhou-paifu-to-json-main" / "src" / "main.py",
        root / "tenhou-paifu-to-json" / "src" / "main.py",
        root / "tenhou-paifu-to-json" / "main.py",
    ]:
        if p.exists():
            return str(p)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="将 XML 牌谱批量转换为 tenhou6 JSON")
    ap.add_argument("--db", default="data/tenhou.db", help="数据库路径")
    ap.add_argument("--limit", type=int, default=None, help="最多转换 N 局")
    ap.add_argument("--batch", type=int, default=200, help="每批处理数量")
    ap.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 4),
        help="并行进程数（默认 CPU 核心数，建议 4-8）",
    )
    ap.add_argument("--skip-existing", action="store_true", help="跳过已有 log_json 的记录")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error(f"数据库不存在: {db_path}")
        return 1

    sys.path.insert(0, str(Path(__file__).parent))
    paifu_path = _find_paifu_path()
    if not paifu_path:
        logger.error(
            "未找到 tenhou-paifu-to-json，请克隆到项目目录：\n"
            "  git clone https://github.com/Riichi-Mahjong-Statistics-Seminar/tenhou-paifu-to-json tenhou-paifu-to-json-main"
        )
        return 1
    logger.info(f"使用 tenhou-paifu-to-json: {paifu_path}, 并行进程: {args.workers}")

    ok, fail = convert_new_logs_to_tenhou6(
        str(db_path),
        limit=args.limit,
        batch=args.batch,
        skip_existing=args.skip_existing,
        workers=args.workers,
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
