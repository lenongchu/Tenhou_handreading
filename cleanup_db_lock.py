"""
清理数据库锁定状态
"""
import sqlite3
import os

db_path = 'data/tenhou.db'

print("尝试清理数据库锁定...")

# 1. 强制关闭所有连接并VACUUM
try:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")
    conn.commit()
    conn.close()
    print("✓ 数据库已清理")
except Exception as e:
    print(f"✗ 清理失败: {e}")

# 2. 删除journal文件（如果存在）
journal_file = db_path + '-journal'
if os.path.exists(journal_file):
    try:
        os.remove(journal_file)
        print(f"✓ 已删除 {journal_file}")
    except Exception as e:
        print(f"✗ 无法删除journal文件: {e}")

# 3. 删除WAL文件（如果存在）
wal_file = db_path + '-wal'
if os.path.exists(wal_file):
    try:
        os.remove(wal_file)
        print(f"✓ 已删除 {wal_file}")
    except Exception as e:
        print(f"✗ 无法删除WAL文件: {e}")

shm_file = db_path + '-shm'
if os.path.exists(shm_file):
    try:
        os.remove(shm_file)
        print(f"✓ 已删除 {shm_file}")
    except Exception as e:
        print(f"✗ 无法删除SHM文件: {e}")

print("\n清理完成，可以重新启动处理脚本")
