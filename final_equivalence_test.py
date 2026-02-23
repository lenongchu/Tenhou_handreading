"""
完整的等价性验证测试
"""
import sqlite3
import sys
import os
sys.path.insert(0, 'src')
from simple_normalizer import normalize_discard_pattern

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("="*60)
print("麻将读牌 - 等价性匹配验证")
print("="*60)

# 测试组1: 1s-2s 的所有等价形式
print("\n[测试组1] 1s-2s 的等价形式")
print("-"*60)
group1 = [
    ["1s", "2s"],
    ["1m", "2m"],
    ["1p", "2p"],
    ["9s", "8s"],  # 镜像
    ["9m", "8m"],  # 镜像
    ["9p", "8p"],  # 镜像
]

for pattern_tiles in group1:
    normalized = normalize_discard_pattern(pattern_tiles)
    cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{normalized}%",))
    count = cur.fetchone()[0]
    print(f"  {str(pattern_tiles):20} → {normalized:10} → {count:,} matches")

# 测试组2: 2s-3s 的等价形式（应该和组1不同）
print("\n[测试组2] 2s-3s 的等价形式（应不同于组1）")
print("-"*60)
group2 = [
    ["2s", "3s"],
    ["2m", "3m"],
    ["8s", "7s"],  # 镜像
    ["8m", "7m"],  # 镜像
]

for pattern_tiles in group2:
    normalized = normalize_discard_pattern(pattern_tiles)
    cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{normalized}%",))
    count = cur.fetchone()[0]
    print(f"  {str(pattern_tiles):20} → {normalized:10} → {count:,} matches")

# 测试组3: 不应等价的模式
print("\n[测试组3] 不等价的其他模式")
print("-"*60)
group3 = [
    ["3s", "4s"],
    ["7s", "6s"],  # 镜像
    ["1s", "3s"],  # 间隔2
    ["9s", "7s"],  # 间隔2的镜像
]

for pattern_tiles in group3:
    normalized = normalize_discard_pattern(pattern_tiles)
    cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{normalized}%",))
    count = cur.fetchone()[0]
    print(f"  {str(pattern_tiles):20} → {normalized:10} → {count:,} matches")

# 测试组4: 复杂序列
print("\n[测试组4] 复杂序列的等价性")
print("-"*60)
group4 = [
    ["1m", "2m", "3m"],
    ["9m", "8m", "7m"],  # 镜像
    ["7m", "8m", "9m"],  # reverse+mirror
    ["3m", "2m", "1m"],  # reverse
]

for pattern_tiles in group4:
    normalized = normalize_discard_pattern(pattern_tiles)
    cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{normalized}%",))
    count = cur.fetchone()[0]
    print(f"  {str(pattern_tiles):20} → {normalized:10} → {count:,} matches")

print("\n" + "="*60)
print("验证完成！")
print("="*60)

conn.close()
