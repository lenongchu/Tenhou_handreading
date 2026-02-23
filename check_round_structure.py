"""检查数据库中小局的记录方式"""
import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 检查表结构
cur.execute("PRAGMA table_info(game_states)")
columns = [row[1] for row in cur.fetchall()]
print(f"game_states 表字段: {columns}\n")

# 检查是否有 round_num 字段
if 'round_num' in columns:
    print("✅ 数据库有 round_num 字段，支持小局区分！\n")
    print("=== 按小局分组统计 ===")
    cur.execute("""
        SELECT log_id, round_num, COUNT(*) as states 
        FROM game_states 
        GROUP BY log_id, round_num 
        ORDER BY log_id, round_num 
        LIMIT 20
    """)
    print(f"{'对局ID':<30} | {'小局号':<6} | {'状态数':<8}")
    print("-"*55)
    for row in cur.fetchall():
        print(f"{row[0][:30]:<30} | {row[1]:<6} | {row[2]:<8}")
    
    # 检查一场游戏有多少小局
    print("\n=== 每场游戏的小局数统计 ===")
    cur.execute("""
        SELECT log_id, COUNT(DISTINCT round_num) as num_rounds, COUNT(*) as total_states
        FROM game_states 
        GROUP BY log_id 
        LIMIT 10
    """)
    print(f"{'对局ID':<30} | {'小局数':<8} | {'总状态数':<10}")
    print("-"*58)
    for row in cur.fetchall():
        print(f"{row[0][:30]:<30} | {row[1]:<8} | {row[2]:<10}")
else:
    print("[WARNING] 数据库中没有 round_num 字段！")
    print("当前记录可能没有区分小局。\n")

conn.close()
