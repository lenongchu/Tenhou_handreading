"""
识别并迁移三麻（Sanma / 3人麻将）牌谱

识别 main DB 中的三麻牌谱：XML 格式的复制到 tenhou_sanma.db 保存；
仅 tenhou6 格式的直接删除不保存。可选从主库执行删除（需 --execute）。

天凤 mjlog GO 元素的 type 为 8 位位域，其中 bit 0x10 (16) = 三麻。
详见：https://blog.kobalab.net/entry/20170312/1489315432

用法:
  py extract_sanma_logs.py [--db PATH] [--output PATH] [--limit N] [--dry-run] [--execute]

选项:
  --db PATH      主数据库路径，默认从 config.ini 读取或 data/tenhou.db
  --output PATH  三麻牌谱输出库路径，默认与主库同目录的 tenhou_sanma.db
  --limit N      最多扫描 N 局（默认全部）
  --dry-run      仅统计，不复制不删除
  --execute      复制后从主库删除三麻牌谱（默认仅复制）
"""

import argparse
import configparser
import gzip
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 天凤 GO type 位域：bit 0x10 = 三麻 (Sanma)
SANMA_TYPE_BIT = 0x10


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


def _is_sanma_from_json(json_str_or_bytes) -> bool:
    """
    从 tenhou6 JSON 根级 type 判定是否为三麻。
    tenhou-paifu-to-json 输出的根级 type 来自 mjlog GO 的 type 属性。
    """
    try:
        raw = json_str_or_bytes
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        t = data.get("type")
        if t is None:
            return False
        return (int(t) & SANMA_TYPE_BIT) != 0
    except Exception:
        return False


def _is_sanma_from_xml(xml_content: str) -> bool:
    """
    从 mjlog XML 的 GO 元素 type 属性判定是否为三麻。
    GO 位于文件前部，用正则提取 type 值。
    """
    m = re.search(r'<GO\s[^>]*type\s*=\s*["\'](\d+)["\']', xml_content)
    if not m:
        return False
    try:
        t = int(m.group(1))
        return (t & SANMA_TYPE_BIT) != 0
    except ValueError:
        return False


def extract_sanma_logs(
    db_path: str,
    output_path: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    execute_remove: bool = False,
) -> tuple[int, int, int, int]:
    """
    扫描主库，识别三麻牌谱。XML 格式复制到 output_path，仅 tenhou6 的不保存。

    Returns:
        (扫描数, 三麻总数, XML 已保存数, 已删除数或已复制数)
    """
    import sqlite3

    main_db = Path(db_path)
    out_db_path = Path(output_path)
    out_db_path.parent.mkdir(parents=True, exist_ok=True)

    print("正在连接数据库...", flush=True)
    conn = sqlite3.connect(str(main_db), timeout=120)
    cur = conn.cursor()

    # 确保有 log_json 列
    cur.execute("PRAGMA table_info(logs)")
    columns = [row[1] for row in cur.fetchall()]
    if "log_json" not in columns:
        cur.execute("ALTER TABLE logs ADD COLUMN log_json BLOB")
        conn.commit()

    print("正在统计对局总数（大表可能需数十秒）...", flush=True)
    cur.execute(
        "SELECT COUNT(*) FROM logs WHERE (log IS NOT NULL AND log != '') OR (log_json IS NOT NULL AND log_json != '')"
    )
    total = cur.fetchone()[0]
    if limit:
        total = min(total, limit)
    print(f"待扫描对局数: {total:,}，开始扫描（每 100/1000 条输出进度）...", flush=True)

    # (log_id, has_xml)：has_xml 表示 log 列有内容，可保存到 sanma 库
    sanma_list: list[tuple[str, bool]] = []
    batch_size = 500
    offset = 0
    scanned = 0

    while True:
        cur.execute(
            """
            SELECT id, log, log_json
            FROM logs
            WHERE (log IS NOT NULL AND log != '') OR (log_json IS NOT NULL AND log_json != '')
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (batch_size, offset),
        )
        rows = cur.fetchall()
        if not rows:
            break

        for log_id, log_blob, log_json_blob in rows:
            if limit and scanned >= limit:
                break
            scanned += 1

            is_sanma = False
            has_xml = log_blob and (
                (isinstance(log_blob, bytes) and len(log_blob) > 0)
                or (isinstance(log_blob, str) and len(log_blob) > 0)
            )

            # 优先从 log_json 判定（tenhou6 JSON 根级 type）
            if log_json_blob and (isinstance(log_json_blob, bytes) and len(log_json_blob) > 0 or
                                  isinstance(log_json_blob, str) and len(log_json_blob) > 0):
                is_sanma = _is_sanma_from_json(log_json_blob)
            else:
                # 回退到 XML：需要解压 log 并解析
                if log_blob:
                    try:
                        if isinstance(log_blob, bytes):
                            xml_content = gzip.decompress(log_blob).decode("utf-8")
                        else:
                            xml_content = str(log_blob)
                        is_sanma = _is_sanma_from_xml(xml_content)
                    except Exception:
                        pass

            if is_sanma:
                sanma_list.append((log_id, bool(has_xml)))

            # 每 1000 条或前 500 条输出进度，避免长时间无输出像卡住
            if (scanned <= 500 and scanned % 100 == 0 and scanned > 0) or (scanned > 500 and scanned % 1000 == 0):
                n_xml = sum(1 for _, hx in sanma_list if hx)
                print(f"  已扫描: {scanned:,}/{total:,}（{100.0 * scanned / total:.1f}%），发现三麻: {len(sanma_list):,}（含 XML 可保存: {n_xml:,}）", flush=True)

        offset += batch_size
        if limit and scanned >= limit:
            break

    sanma_to_save = [lid for lid, hx in sanma_list if hx]
    sanma_all = [lid for lid, _ in sanma_list]
    logger.info("扫描完成。三麻牌谱数: %s（含 XML 可保存: %s，仅 tenhou6 直接删除: %s）",
                f"{len(sanma_all):,}", f"{len(sanma_to_save):,}", f"{len(sanma_all) - len(sanma_to_save):,}")

    if dry_run:
        if sanma_all:
            logger.info("示例三麻 log_id（前 5 个）: %s", sanma_all[:5])
        return (scanned, len(sanma_all), 0, len(sanma_to_save))

    if not sanma_all:
        conn.close()
        return (scanned, 0, 0, 0)

    # 获取 logs 表所有列
    cur.execute("PRAGMA table_info(logs)")
    col_infos = cur.fetchall()
    col_names = [c[1] for c in col_infos]
    placeholders = ",".join(["?"] * len(col_names))
    col_list = ",".join(col_names)

    # 创建输出库及 logs 表（结构与主库一致）
    conn_out = sqlite3.connect(str(out_db_path), timeout=120)
    cur_out = conn_out.cursor()
    cur_out.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='logs'"
    )
    if not cur_out.fetchone():
        # 从主库复制表结构
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='logs'")
        create_sql = cur.fetchone()
        if create_sql:
            cur_out.execute(create_sql[0])
        else:
            # 简单位置：仅 id, log, log_json
            cur_out.execute("""
                CREATE TABLE logs (
                    id TEXT PRIMARY KEY,
                    log BLOB,
                    log_json BLOB
                )
            """)
    conn_out.commit()

    # 批量复制含 XML 的三麻牌谱到输出库（仅 tenhou6 的不保存）
    copied = 0
    for i in range(0, len(sanma_to_save), batch_size):
        batch = sanma_to_save[i : i + batch_size]
        for lid in batch:
            cur.execute(
                f"SELECT {col_list} FROM logs WHERE id = ?",
                (lid,),
            )
            row = cur.fetchone()
            if row:
                cur_out.execute(
                    f"INSERT OR REPLACE INTO logs ({col_list}) VALUES ({placeholders})",
                    row,
                )
                copied += 1
        conn_out.commit()
        logger.info("已复制 XML 格式: %s/%s", f"{copied:,}", f"{len(sanma_to_save):,}")

    conn_out.close()

    if execute_remove:
        logger.info("从主库删除三麻牌谱及关联 game_states、visible_tile_stats...")
        # 按依赖顺序删除：visible_tile_stats -> game_states -> logs
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='visible_tile_stats'")
        has_vts = cur.fetchone() is not None
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='game_states'")
        has_gs = cur.fetchone() is not None

        for j in range(0, len(sanma_all), batch_size):
            batch = sanma_all[j : j + batch_size]
            ph = ",".join(["?"] * len(batch))
            if has_vts and has_gs:
                cur.execute(
                    "DELETE FROM visible_tile_stats WHERE state_id IN "
                    "(SELECT id FROM game_states WHERE log_id IN (" + ph + "))",
                    batch,
                )
            if has_gs:
                cur.execute(
                    "DELETE FROM game_states WHERE log_id IN (" + ph + ")",
                    batch,
                )
        for j in range(0, len(sanma_all), batch_size):
            batch = sanma_all[j : j + batch_size]
            ph = ",".join(["?"] * len(batch))
            cur.execute("DELETE FROM logs WHERE id IN (" + ph + ")", batch)
        conn.commit()
        logger.info("已从主库删除 %s 条三麻牌谱", f"{len(sanma_all):,}")

    conn.close()
    if execute_remove:
        return (scanned, len(sanma_all), len(sanma_to_save), len(sanma_all))
    return (scanned, len(sanma_all), len(sanma_to_save), copied)


def main():
    # 确保进度即时输出到终端（避免被缓冲）
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="识别并将三麻牌谱迁移到独立数据库"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="主数据库路径，默认从 config.ini 或 data/tenhou.db",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="三麻输出库路径，默认与主库同目录的 tenhou_sanma.db",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="最多扫描 N 局",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计，不复制不删除",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="复制后从主库删除三麻牌谱（默认仅复制）",
    )
    args = parser.parse_args()

    db_path = args.db or _get_db_path()
    output_path = args.output
    if not output_path:
        output_path = str(Path(db_path).parent / "tenhou_sanma.db")

    print("=" * 60)
    print("三麻牌谱识别与迁移工具")
    print("=" * 60)
    print(f"主库: {db_path}")
    print(f"三麻输出库: {output_path}")
    if args.dry_run:
        print("[DRY RUN] 仅统计，不写入")
    if args.execute:
        print("[EXECUTE] 将从主库删除三麻牌谱")
    print("开始执行...", flush=True)
    print()

    result = extract_sanma_logs(
        db_path,
        output_path,
        limit=args.limit,
        dry_run=args.dry_run,
        execute_remove=args.execute,
    )
    scanned, sanma_count, saved_xml, modified = result

    print()
    print("=" * 60)
    print(f"扫描: {scanned:,} 局 | 三麻: {sanma_count:,} 局")
    if args.dry_run:
        print(f"  XML 可保存: {saved_xml:,} | 仅 tenhou6 直接删除: {sanma_count - saved_xml:,}")
    elif args.execute:
        print(f"  已保存 XML: {saved_xml:,} | 已从主库删除: {modified:,}")
    else:
        print(f"  已复制 XML 到三麻库: {saved_xml:,}")
    print("=" * 60)


if __name__ == "__main__":
    main()
