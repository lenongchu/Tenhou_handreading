import sqlite3
import sys
import io

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 连接数据库
conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("=== 数据库详细分析 ===\n")

# 总记录数
cur.execute('SELECT COUNT(*) FROM logs')
total = cur.fetchone()[0]
print(f"总对局记录数: {total:,}")

# 按人数分类
cur.execute('SELECT num_players, COUNT(*) FROM logs GROUP BY num_players')
by_players = cur.fetchall()
print("\n按人数分类:")
for players, count in by_players:
    print(f"  {players}人麻将: {count:,} 场")

# 按是否已下载分类
cur.execute('SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ""')
downloaded = cur.fetchone()[0]
print(f"\n已下载完整内容: {downloaded:,}")

cur.execute('SELECT COUNT(*) FROM logs WHERE log IS NULL OR log = ""')
not_downloaded = cur.fetchone()[0]
print(f"未下载（仅ID）: {not_downloaded:,}")

# 4人麻将的下载情况
cur.execute('SELECT COUNT(*) FROM logs WHERE num_players = 4 AND (log IS NULL OR log = "")')
four_player_pending = cur.fetchone()[0]
print(f"\n4人麻将待下载: {four_player_pending:,}")

cur.execute('SELECT COUNT(*) FROM logs WHERE num_players = 4 AND log IS NOT NULL AND log != ""')
four_player_downloaded = cur.fetchone()[0]
print(f"4人麻将已下载: {four_player_downloaded:,}")

# 3人麻将的情况
cur.execute('SELECT COUNT(*) FROM logs WHERE num_players = 3')
three_player = cur.fetchone()[0]
print(f"\n3人麻将总数: {three_player:,}")

# 检查错误和已处理标记
cur.execute('SELECT COUNT(*) FROM logs WHERE was_error = 1')
error_count = cur.fetchone()[0]
print(f"\n标记为错误的记录: {error_count:,}")

cur.execute('SELECT COUNT(*) FROM logs WHERE is_processed = 1')
processed_count = cur.fetchone()[0]
print(f"已处理的记录: {processed_count:,}")

conn.close()
