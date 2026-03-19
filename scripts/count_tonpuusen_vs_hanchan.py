"""
统计 tenhou6 牌谱中 東風戦（东风战）与 半荘（半庄）的数量。

判断规则：
- 東風戦：所有小局仅含东场 (bakaze E/東)，无南场
- 半荘：至少含一局南场 (bakaze S/南)

用法：python scripts/count_tonpuusen_vs_hanchan.py [--db PATH]
"""

import argparse
import configparser
import json
import sqlite3
import sys
from pathlib import Path


def _get_db_path(override: str = None) -> str:
    """从 config.ini 或默认路径获取数据库路径"""
    root = Path(__file__).resolve().parent.parent
    if override:
        return override
    config_file = root / "config.ini"
    if config_file.exists():
        try:
            cfg = configparser.ConfigParser()
            cfg.read(config_file, encoding="utf-8")
            path = cfg.get("Database", "path", fallback=None)
            if path and path.strip():
                return path.strip()
        except Exception:
            pass
    return str(root / "data" / "tenhou.db")


def _is_hanchan(log_json_blob) -> bool:
    """
    根据 games[].data.bakaze 判断是否为半荘。
    若任一小局为南场 (S/南) 或西场 (W/西)，则为半庄及以上；否则为东风战。
    """
    if not log_json_blob:
        return None  # 无法判定
    raw = log_json_blob
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    if not raw or not raw.startswith("{") or "games" not in raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    games = data.get("games") or []
    for g in games:
        gd = g.get("data") or {}
        bakaze = (gd.get("bakaze") or "E").upper()
        # 东场：E/東；南场：S/南；西场：W/西
        if bakaze in ("S", "南") or bakaze in ("W", "西"):
            return True  # 半庄或更长
    return False  # 仅东场 = 东风战


def main():
    parser = argparse.ArgumentParser(description="统计东风战与半庄牌谱数量")
    parser.add_argument("--db", help="数据库路径（默认从 config.ini 读取）")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 条（0=全部，用于测试）")
    parser.add_argument("--progress", type=int, default=0, help="每 N 条打印进度（0=不打印）")
    args = parser.parse_args()
    db_path = _get_db_path(args.db)
    if not Path(db_path).exists():
        print(f"错误：数据库不存在: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 只统计有 log_json 的记录（tenhou6 格式）
    sql = (
        "SELECT id, log_json FROM logs WHERE log_json IS NOT NULL AND log_json != ''"
    )
    if args.limit:
        sql += f" LIMIT {args.limit}"
    cur.execute(sql)
    total = 0
    tonpuusen = 0  # 东风战
    hanchan = 0    # 半庄
    unknown = 0    # 无法判定

    for row in cur:
        total += 1
        result = _is_hanchan(row["log_json"])
        if result is None:
            unknown += 1
        elif result:
            hanchan += 1
        else:
            tonpuusen += 1
        if args.progress and total % args.progress == 0:
            print(f"  已处理 {total:,} 条...", flush=True)

    conn.close()

    pct_t = 100 * tonpuusen / total if total else 0
    pct_h = 100 * hanchan / total if total else 0

    print("=" * 50)
    print("tenhou6 牌谱 東風戦 / 半荘 统计")
    print("=" * 50)
    print(f"数据库: {db_path}")
    print(f"总牌谱数（有 log_json）: {total:,}")
    print(f"  東風戦（东风战，仅东场）: {tonpuusen:,} ({pct_t:.1f}%)")
    print(f"  半荘（半庄，含南场）:     {hanchan:,} ({pct_h:.1f}%)")
    if unknown:
        print(f"  无法判定:                 {unknown:,}")
    print("=" * 50)


if __name__ == "__main__":
    main()
