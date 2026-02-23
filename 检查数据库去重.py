import sqlite3
import sys
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("=== 数据库表结构检查 ===\n")

# 查看 logs 表的完整结构
cur.execute('PRAGMA table_info(logs)')
columns = cur.fetchall()
print("logs 表字段:")
for col in columns:
    print(f"  {col[1]} ({col[2]}) - {'主键' if col[5] else ''} {'NOT NULL' if col[3] else ''}")

# 查看索引
cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='logs'")
indexes = cur.fetchall()
print("\nlogs 表索引:")
for idx in indexes:
    if idx[0]:
        print(f"  {idx[0]}")

# 查看表的创建语句
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='logs'")
create_sql = cur.fetchone()
print("\nlogs 表创建语句:")
print(create_sql[0])

# 检查是否有重复的ID
cur.execute('SELECT id, COUNT(*) as cnt FROM logs GROUP BY id HAVING cnt > 1')
duplicates = cur.fetchall()
print(f"\n当前数据库中重复的对局ID数: {len(duplicates)}")
if duplicates:
    print("重复的ID示例（前5个）:")
    for dup_id, count in duplicates[:5]:
        print(f"  ID: {dup_id}, 出现次数: {count}")

# 统计总记录数
cur.execute('SELECT COUNT(*) FROM logs')
total = cur.fetchone()[0]
cur.execute('SELECT COUNT(DISTINCT id) FROM logs')
unique = cur.fetchone()[0]
print(f"\n总记录数: {total:,}")
print(f"唯一ID数: {unique:,}")
print(f"是否有重复: {'是' if total != unique else '否'}")

conn.close()
