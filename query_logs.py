"""
查询数据库中最新日志信息
"""

import sqlite3
from datetime import datetime

db_path = 'data/tenhou.db'

try:
    # 连接数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("="*60)
    print("数据库日志统计")
    print("="*60 + "\n")
    
    # 查看所有表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"数据库中的表: {[t[0] for t in tables]}\n")
    
    # 查询 logs 表结构
    cursor.execute("PRAGMA table_info(logs);")
    columns = cursor.fetchall()
    print(f"logs 表结构:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    print()
    
    # 统计总日志数
    cursor.execute("SELECT COUNT(*) FROM logs;")
    total_logs = cursor.fetchone()[0]
    print(f"总日志数: {total_logs:,} 条\n")
    
    # 查询最新的日志
    # 尝试不同的可能字段名
    try:
        cursor.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 1;")
        latest = cursor.fetchone()
        
        if latest:
            print("最新日志信息:")
            for i, col in enumerate(columns):
                print(f"  {col[1]}: {latest[i]}")
            print()
    except Exception as e:
        print(f"查询最新日志时出错: {e}\n")
    
    # 尝试查询日期范围
    try:
        # 检查是否有时间戳字段
        cursor.execute("SELECT * FROM logs LIMIT 1;")
        sample = cursor.fetchone()
        
        # 尝试从 log_id 中提取日期信息（天凤的 log_id 通常包含日期）
        cursor.execute("SELECT MIN(id), MAX(id) FROM logs;")
        min_id, max_id = cursor.fetchone()
        print(f"日志ID范围: {min_id} - {max_id}")
        
    except Exception as e:
        print(f"查询日期范围时出错: {e}")
    
    conn.close()
    
    print("\n" + "="*60)
    
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
