"""分析多个对局的小局数统计"""
import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("="*70)
print("麻将对局小局数统计分析")
print("="*70)

# 获取所有对局的小局统计
cur.execute("""
    SELECT 
        log_id, 
        COUNT(DISTINCT round_num) as num_rounds,
        MIN(round_num) as min_round,
        MAX(round_num) as max_round,
        COUNT(*) as total_states
    FROM game_states 
    GROUP BY log_id 
    ORDER BY log_id
    LIMIT 30
""")

results = cur.fetchall()

print(f"\n共分析 {len(results)} 场对局：\n")
print(f"{'对局ID':<32} | {'小局数':<6} | {'小局范围':<12} | {'总状态数':<8}")
print("-"*70)

for row in results:
    log_id, num_rounds, min_r, max_r, states = row
    round_range = f"{min_r}-{max_r}"
    print(f"{log_id:<32} | {num_rounds:<6} | {round_range:<12} | {states:<8}")

# 统计小局数分布
print("\n" + "="*70)
print("小局数分布统计")
print("="*70)

cur.execute("""
    SELECT 
        num_rounds,
        COUNT(*) as game_count,
        ROUND(AVG(total_states), 1) as avg_states
    FROM (
        SELECT 
            log_id,
            COUNT(DISTINCT round_num) as num_rounds,
            COUNT(*) as total_states
        FROM game_states 
        GROUP BY log_id
    )
    GROUP BY num_rounds
    ORDER BY num_rounds
""")

print(f"\n{'小局数':<8} | {'对局数':<10} | {'平均状态数':<12}")
print("-"*35)
for row in cur.fetchall():
    rounds, count, avg_states = row
    print(f"{rounds:<8} | {count:<10} | {avg_states:<12}")

# 详细分析几个典型对局
print("\n" + "="*70)
print("典型对局详细分析（前3场）")
print("="*70)

for i, (log_id, _, _, _, _) in enumerate(results[:3]):
    print(f"\n[对局 {i+1}] {log_id}")
    print("-"*50)
    
    cur.execute("""
        SELECT 
            round_num,
            COUNT(*) as states,
            COUNT(DISTINCT player_id) as players,
            MAX(turn) as max_turn
        FROM game_states 
        WHERE log_id = ?
        GROUP BY round_num
        ORDER BY round_num
    """, (log_id,))
    
    print(f"{'小局号':<8} | {'状态数':<8} | {'玩家数':<8} | {'最大巡目':<10}")
    print("-"*50)
    for row in cur.fetchall():
        round_num, states, players, max_turn = row
        print(f"{round_num:<8} | {states:<8} | {players:<8} | {max_turn:<10}")
    
    # 显示这场游戏的总结
    cur.execute("""
        SELECT COUNT(DISTINCT round_num), COUNT(*), SUM(CASE WHEN round_num >= 4 THEN 1 ELSE 0 END)
        FROM game_states 
        WHERE log_id = ?
    """, (log_id,))
    total_rounds, total_states, south_rounds = cur.fetchone()
    
    game_type = "半庄（东南战）" if south_rounds > 0 else "东风战"
    print(f"\n  类型: {game_type}")
    print(f"  总小局数: {total_rounds}, 总状态数: {total_states}")

print("\n" + "="*70)
print("分析说明：")
print("  - 小局号 0-3: 东1局-东4局")
print("  - 小局号 4-7: 南1局-南4局")
print("  - 小局号 8+:  可能是西、北场或连庄")
print("  - 每个小局的状态数 = 4个玩家的所有出牌动作")
print("="*70)

conn.close()
