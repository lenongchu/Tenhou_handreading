"""查看 tenhou6 转换进度"""
import sqlite3
import sys
from pathlib import Path

db_path = Path(__file__).parent / "data" / "tenhou.db"
if not db_path.exists():
    print("数据库不存在")
    sys.exit(1)

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM logs WHERE log_json IS NOT NULL AND log_json != ''")
converted = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
total = cur.fetchone()[0]
conn.close()

if total == 0:
    print("无对局数据")
else:
    pct = 100 * converted / total
    print(f"已转换: {converted:,} / {total:,} ({pct:.2f}%)")
