"""
分析 tenhou6 转换失败的案例

从 logs 表中抽取尚未转换成功的记录，逐条尝试转换并输出详细错误信息。

用法：
  py analyze_convert_failures.py [--db PATH] [--sample N] [--out FILE]
"""
import argparse
import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _find_paifu_path() -> str:
    root = Path(__file__).parent
    for p in [
        root / "tenhou-paifu-to-json-main" / "src" / "main.py",
        root / "tenhou-paifu-to-json" / "src" / "main.py",
    ]:
        if p.exists():
            return str(p)
    return ""


def convert_with_full_error(xml_content: str, paifu_path: str) -> tuple:
    """
    转换并返回完整错误信息。
    Returns: (json_str or None, error_msg or None)
    """
    xml_path = json_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        ) as fx:
            fx.write(xml_content)
            xml_path = fx.name
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fj:
            json_path = fj.name

        result = subprocess.run(
            [sys.executable, paifu_path, xml_path, json_path],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip() or f"exit code {result.returncode}"
            return (None, err)

        with open(json_path, "r", encoding="utf-8") as f:
            return (f.read(), None)
    except subprocess.TimeoutExpired:
        return (None, "timeout (60s)")
    except UnicodeDecodeError as e:
        return (None, f"encoding error: {e}")
    except Exception as e:
        return (None, str(e))
    finally:
        import os
        for p in [xml_path, json_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def main():
    ap = argparse.ArgumentParser(description="分析 tenhou6 转换失败案例")
    ap.add_argument("--db", default="data/tenhou.db", help="数据库路径")
    ap.add_argument("--sample", type=int, default=50, help="抽样数量")
    ap.add_argument("--out", default="convert_failures_report.txt", help="输出报告路径")
    args = ap.parse_args()

    paifu_path = _find_paifu_path()
    if not paifu_path:
        print("未找到 tenhou-paifu-to-json")
        return 1

    sys.path.insert(0, str(Path(__file__).parent))
    import sqlite3
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(logs)")
    cols = [r[1] for r in cur.fetchall()]
    if "log_json" not in cols:
        print("logs 表无 log_json 列")
        conn.close()
        return 1

    cur.execute("""
        SELECT id, log FROM logs
        WHERE log IS NOT NULL AND log != ''
        AND (log_json IS NULL OR log_json = '')
        ORDER BY id
        LIMIT ?
    """, (args.sample,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("没有待转换的记录（可能已全部转换完成）")
        return 0

    print(f"抽样 {len(rows)} 条待转换记录，开始逐条测试...")
    failures = []
    ok = 0
    for log_id, log_blob in rows:
        if isinstance(log_blob, bytes):
            try:
                xml_str = gzip.decompress(log_blob).decode("utf-8")
            except Exception:
                xml_str = log_blob.decode("utf-8", errors="replace")
        else:
            xml_str = str(log_blob)

        json_str, err = convert_with_full_error(xml_str, paifu_path)
        if json_str:
            ok += 1
        else:
            failures.append((log_id, err, xml_str[:500]))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("tenhou6 转换失败案例分析\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"抽样: {len(rows)}, 成功: {ok}, 失败: {len(failures)}\n\n")
        if failures:
            f.write("失败详情:\n")
            f.write("-" * 80 + "\n")
            for log_id, err, xml_preview in failures:
                f.write(f"\nlog_id: {log_id}\n")
                f.write(f"错误: {err}\n")
                f.write(f"XML 前 500 字符:\n{xml_preview}\n")
                f.write("-" * 80 + "\n")

    print(f"完成: 成功 {ok}, 失败 {len(failures)}")
    print(f"详细报告已写入: {args.out}")

    if failures:
        print("\n失败原因统计:")
        from collections import Counter
        err_types = Counter()
        for _, err, _ in failures:
            if "timeout" in err.lower():
                err_types["timeout"] += 1
            elif "encoding" in err.lower() or "utf" in err.lower():
                err_types["encoding"] += 1
            elif "traceback" in err.lower() or "error" in err.lower():
                err_types["parse_error"] += 1
            else:
                err_types[err[:80]] += 1
        for k, v in err_types.most_common(10):
            print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
