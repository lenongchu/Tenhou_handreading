"""调试 3m-1m 查询问题"""
import sqlite3
import sys
import io
sys.path.insert(0, 'src')
from simple_normalizer import normalize_discard_pattern

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 测试标准化
print("="*70)
print("测试 3m-1m 的标准化")
print("="*70)

test_pattern = ["3m", "1m"]
normalized = normalize_discard_pattern(test_pattern)
print(f"\n输入: {test_pattern}")
print(f"标准化结果: {normalized}\n")

# 检查所有可能的等价形式
equivalents = [
    ["3m", "1m"],
    ["3s", "1s"],
    ["3p", "1p"],
    ["7m", "9m"],  # 镜像
    ["7s", "9s"],
    ["7p", "9p"],
]

print("所有等价形式的标准化结果：")
print("-"*70)
for pattern in equivalents:
    norm = normalize_discard_pattern(pattern)
    print(f"{str(pattern):20} -> {norm}")

# 查询数据库
conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("\n" + "="*70)
print(f"数据库查询: 包含 '{normalized}' 的模式")
print("="*70)

cur.execute("""
    SELECT COUNT(*) 
    FROM game_states 
    WHERE normalized_pattern LIKE ?
""", (f"%{normalized}%",))
count = cur.fetchone()[0]
print(f"\n找到 {count} 个匹配\n")

if count == 0:
    # 如果没找到，让我们看看有哪些类似的模式
    print("没有找到匹配！让我们看看数据库中有哪些模式：\n")
    
    # 查看包含 "3s" 和 "1s" 的模式（不要求连续）
    cur.execute("""
        SELECT DISTINCT normalized_pattern, COUNT(*) as cnt
        FROM game_states 
        WHERE normalized_pattern LIKE '%3s%' 
          AND normalized_pattern LIKE '%1s%'
        GROUP BY normalized_pattern
        ORDER BY cnt DESC
        LIMIT 20
    """)
    
    print("包含 3s 和 1s 的模式（前20个）：")
    print("-"*70)
    for row in cur.fetchall():
        pattern, cnt = row
        if pattern:
            print(f"{pattern[:50]:50} | {cnt:6} 次")
    
    # 查看以 "3s" 开头的模式
    print("\n以 3s 开头的模式（前20个）：")
    print("-"*70)
    cur.execute("""
        SELECT DISTINCT normalized_pattern, COUNT(*) as cnt
        FROM game_states 
        WHERE normalized_pattern LIKE '3s-%'
        GROUP BY normalized_pattern
        ORDER BY cnt DESC
        LIMIT 20
    """)
    
    for row in cur.fetchall():
        pattern, cnt = row
        if pattern:
            print(f"{pattern[:50]:50} | {cnt:6} 次")
    
    # 看看最常见的2张牌模式
    print("\n最常见的2张牌模式（前30个）：")
    print("-"*70)
    cur.execute("""
        SELECT normalized_pattern, COUNT(*) as cnt
        FROM game_states 
        WHERE normalized_pattern NOT LIKE '%-%-%'  -- 只包含2张牌
          AND normalized_pattern LIKE '%-%'         -- 至少包含1个连字符
          AND LENGTH(normalized_pattern) <= 10       -- 长度限制
        GROUP BY normalized_pattern
        ORDER BY cnt DESC
        LIMIT 30
    """)
    
    for row in cur.fetchall():
        pattern, cnt = row
        print(f"{pattern:20} | {cnt:6} 次")

else:
    # 显示一些示例
    print("找到匹配！显示前10个示例：\n")
    cur.execute("""
        SELECT log_id, round_num, honba, player_id, turn, normalized_pattern
        FROM game_states 
        WHERE normalized_pattern LIKE ?
        LIMIT 10
    """, (f"%{normalized}%",))
    
    print(f"{'对局ID':<32} | {'小局':<6} | {'本场':<6} | {'玩家':<6} | {'巡目':<6} | {'模式':<30}")
    print("-"*100)
    for row in cur.fetchall():
        log_id, round_num, honba, player_id, turn, pattern = row
        print(f"{log_id:<32} | {round_num:<6} | {honba:<6} | {player_id:<6} | {turn:<6} | {pattern[:30]:<30}")

conn.close()

print("\n" + "="*70)
print("分析完成")
print("="*70)
