import sqlite3
import sys
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("=== Database Structure Check ===\n")

# Check logs table structure
cur.execute('PRAGMA table_info(logs)')
columns = cur.fetchall()
print("logs table columns:")
for col in columns:
    pk = 'PRIMARY KEY' if col[5] else ''
    notnull = 'NOT NULL' if col[3] else ''
    print(f"  {col[1]} ({col[2]}) {pk} {notnull}")

# Check indexes
cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='logs'")
indexes = cur.fetchall()
print("\nlogs table indexes:")
for idx in indexes:
    if idx[0]:
        print(f"  {idx[0]}")

# Check table creation statement
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='logs'")
create_sql = cur.fetchone()
print("\nlogs table creation SQL:")
print(create_sql[0])

# Check for duplicate IDs
cur.execute('SELECT id, COUNT(*) as cnt FROM logs GROUP BY id HAVING cnt > 1')
duplicates = cur.fetchall()
print(f"\nDuplicate log IDs in database: {len(duplicates)}")
if duplicates:
    print("Sample duplicates (first 5):")
    for dup_id, count in duplicates[:5]:
        print(f"  ID: {dup_id}, Count: {count}")

# Statistics
cur.execute('SELECT COUNT(*) FROM logs')
total = cur.fetchone()[0]
cur.execute('SELECT COUNT(DISTINCT id) FROM logs')
unique = cur.fetchone()[0]
print(f"\nTotal records: {total:,}")
print(f"Unique IDs: {unique:,}")
print(f"Has duplicates: {'YES' if total != unique else 'NO'}")

conn.close()
