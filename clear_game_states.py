"""
快速清空 game_states 表（保留表结构）
"""
import sqlite3
import sys
import io

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db_path = 'data/tenhou.db'

print("开始清空 game_states 和 visible_tile_stats 表...")

try:
    conn = sqlite3.connect(db_path, timeout=120)
    cur = conn.cursor()
    
    # 获取清空前的记录数
    cur.execute("SELECT COUNT(*) FROM game_states")
    old_count = cur.fetchone()[0]
    print(f"当前 game_states 表记录数: {old_count:,}")
    
    cur.execute("SELECT COUNT(*) FROM visible_tile_stats")
    old_stats_count = cur.fetchone()[0]
    print(f"当前 visible_tile_stats 表记录数: {old_stats_count:,}")
    
    # 清空表
    print("\n正在清空表...")
    cur.execute("DELETE FROM visible_tile_stats")
    cur.execute("DELETE FROM game_states")
    
    conn.commit()
    
    # 验证
    cur.execute("SELECT COUNT(*) FROM game_states")
    new_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM visible_tile_stats")
    new_stats_count = cur.fetchone()[0]
    
    print(f"\n✓ 清空完成!")
    print(f"  game_states: {old_count:,} -> {new_count:,}")
    print(f"  visible_tile_stats: {old_stats_count:,} -> {new_stats_count:,}")
    
    # 可选：VACUUM 释放空间（较慢，可跳过）
    # print("\n正在优化数据库（VACUUM）...")
    # conn.execute("VACUUM")
    # print("✓ 优化完成")
    
    conn.close()
    print("\n✓ 数据库已准备就绪，可以重新处理对局数据")
    
except Exception as e:
    print(f"✗ 错误: {e}")
    sys.exit(1)
