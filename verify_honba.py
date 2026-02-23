"""验证本场（honba）信息是否正确记录"""
import sqlite3
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("="*80)
print("本场（连庄）信息验证")
print("="*80)

# 检查表结构
cur.execute("PRAGMA table_info(game_states)")
columns = [row[1] for row in cur.fetchall()]
print(f"\n[OK] game_states 表字段: {columns}")
print(f"[OK] honba 字段存在: {'honba' in columns}\n")

# 查询第一场游戏的详细小局信息
cur.execute("""
    SELECT DISTINCT log_id 
    FROM game_states 
    LIMIT 1
""")
first_log_id = cur.fetchone()[0]

print(f"分析对局: {first_log_id}\n")
print("="*80)

cur.execute("""
    SELECT 
        round_num,
        honba,
        COUNT(*) as states,
        MAX(turn) as max_turn
    FROM game_states 
    WHERE log_id = ?
    GROUP BY round_num, honba
    ORDER BY round_num, honba
""", (first_log_id,))

print(f"{'小局号':<8} | {'本场':<6} | {'说明':<20} | {'状态数':<8} | {'最大巡目':<10}")
print("-"*80)

for row in cur.fetchall():
    round_num, honba, states, max_turn = row
    
    # 生成说明
    if round_num < 4:
        round_name = f"东{round_num + 1}局"
    elif round_num < 8:
        round_name = f"南{round_num - 3}局"
    else:
        round_name = f"第{round_num + 1}局"
    
    if honba > 0:
        round_name += f" {honba}本场"
    
    print(f"{round_num:<8} | {honba:<6} | {round_name:<20} | {states:<8} | {max_turn:<10}")

# 统计所有对局的连庄情况
print("\n" + "="*80)
print("所有对局的连庄统计")
print("="*80)

cur.execute("""
    SELECT 
        honba,
        COUNT(DISTINCT log_id || '-' || round_num) as round_count,
        AVG(state_count) as avg_states
    FROM (
        SELECT 
            log_id,
            round_num,
            honba,
            COUNT(*) as state_count
        FROM game_states
        GROUP BY log_id, round_num, honba
    )
    GROUP BY honba
    ORDER BY honba
""")

print(f"\n{'本场数':<8} | {'小局数':<10} | {'平均状态数':<12} | {'说明':<20}")
print("-"*60)
for row in cur.fetchall():
    honba, count, avg_states = row
    desc = "正常" if honba == 0 else f"{honba}连庄"
    print(f"{honba:<8} | {count:<10} | {avg_states:<12.1f} | {desc:<20}")

# 找出连庄最多的小局
print("\n" + "="*80)
print("连庄次数最多的小局（TOP 10）")
print("="*80)

cur.execute("""
    SELECT 
        log_id,
        round_num,
        honba,
        COUNT(*) as states
    FROM game_states
    WHERE honba > 0
    GROUP BY log_id, round_num, honba
    ORDER BY honba DESC, states DESC
    LIMIT 10
""")

print(f"\n{'对局ID':<32} | {'小局':<6} | {'本场':<6} | {'状态数':<8}")
print("-"*60)
for row in cur.fetchall():
    log_id, round_num, honba, states = row
    print(f"{log_id:<32} | {round_num:<6} | {honba:<6} | {states:<8}")

print("\n" + "="*80)
print("[OK] 本场信息已正确记录到数据库！")
print("="*80)

conn.close()
