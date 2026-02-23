import sqlite3
import os
import sys
import io

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 连接数据库
conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 获取所有表
cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = [row[0] for row in cur.fetchall()]
print('数据库表:', tables)
print()

# 统计已下载的对局内容
cur.execute('SELECT COUNT(*) FROM logs')
logs_count = cur.fetchone()[0]
print(f'已下载对局内容（mjlog）总数: {logs_count:,}')

# 查看logs表结构
cur.execute('PRAGMA table_info(logs)')
logs_columns = [row[1] for row in cur.fetchall()]
print(f'  - logs表字段: {logs_columns}')

# 检查file_index表
cur.execute('SELECT COUNT(*) FROM file_index')
file_index_count = cur.fetchone()[0]
print(f'\n对局索引（file_index）总数: {file_index_count:,}')

# 查看file_index表结构
cur.execute('PRAGMA table_info(file_index)')
file_index_columns = [row[1] for row in cur.fetchall()]
print(f'  - file_index表字段: {file_index_columns}')

# 检查下载状态
if 'log' in logs_columns:
    cur.execute('SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ""')
    with_content = cur.fetchone()[0]
    print(f'\n[OK] 包含实际对局内容的记录: {with_content:,}')
    
    cur.execute('SELECT COUNT(*) FROM logs WHERE log IS NULL OR log = ""')
    without_content = cur.fetchone()[0]
    print(f'[EMPTY] 仅有ID没有内容的记录: {without_content:,}')
    
    progress = (with_content / logs_count * 100) if logs_count > 0 else 0
    print(f'\n[进度] 下载完成度: {progress:.2f}%')
    
    # 随机查看一条记录的大小
    cur.execute('SELECT LENGTH(log) FROM logs WHERE log IS NOT NULL AND log != "" LIMIT 1')
    result = cur.fetchone()
    if result:
        avg_size_kb = result[0] / 1024
        print(f'[大小] 单条对局平均大小: ~{avg_size_kb:.2f} KB')
        total_size_mb = (with_content * result[0]) / (1024**2)
        print(f'[估计] 对局数据总大小: ~{total_size_mb:.2f} MB')
print()

# 数据库文件大小
db_size = os.path.getsize('data/tenhou.db') / (1024**3)
print(f'数据库文件大小: {db_size:.2f} GB')

conn.close()
