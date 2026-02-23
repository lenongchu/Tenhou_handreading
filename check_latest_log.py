"""
查询最新日志日期 - 简化版
"""

import sqlite3
from datetime import datetime

db_path = 'data/tenhou.db'

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("="*60)
    print("数据库统计信息")
    print("="*60 + "\n")
    
    # 总日志数
    cursor.execute("SELECT COUNT(*) FROM logs;")
    total = cursor.fetchone()[0]
    print(f"总日志数: {total:,} 条")
    
    # 数据库大小
    import os
    size_mb = os.path.getsize(db_path) / (1024 * 1024)
    print(f"数据库大小: {size_mb:.2f} MB\n")
    
    # 最早和最新的日志
    cursor.execute("SELECT MIN(date), MAX(date) FROM logs;")
    min_date, max_date = cursor.fetchone()
    
    print(f"日期范围:")
    print(f"  最早: {min_date}")
    print(f"  最新: {max_date}\n")
    
    # 解析最新日期
    if max_date:
        # 格式: 2026-02-05T23:05
        dt = datetime.fromisoformat(max_date)
        print(f"最新日志详情:")
        print(f"  日期: {dt.strftime('%Y年%m月%d日')}")
        print(f"  时间: {dt.strftime('%H:%M')}")
        print(f"  星期: {dt.strftime('%A')}")
    
    # 按年份统计
    print(f"\n按年份统计:")
    cursor.execute("""
        SELECT substr(date, 1, 4) as year, COUNT(*) as count 
        FROM logs 
        GROUP BY year 
        ORDER BY year
    """)
    
    for year, count in cursor.fetchall():
        print(f"  {year}年: {count:,} 局")
    
    conn.close()
    
    print("\n" + "="*60)
    
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()
