"""检查 mjlog 中是否包含本场（honba）信息"""
import sqlite3
import gzip
import xml.etree.ElementTree as ET
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 获取一个对局
cur.execute("SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 1")
log_id, xml_compressed = cur.fetchone()

xml_content = gzip.decompress(xml_compressed).decode('utf-8')
root = ET.fromstring(xml_content)

print(f"对局ID: {log_id}\n")
print("="*70)
print("INIT 标签中的 seed 属性分析（前15个小局）：\n")
print(f"{'小局号':<6} | {'seed 完整值':<25} | {'round':<6} | {'本场':<6} | {'说明':<20}")
print("-"*70)

for i, init_tag in enumerate(root.findall('.//INIT')[:15]):
    seed = init_tag.get("seed", "0,0,0,0,0,0")
    parts = seed.split(',')
    
    round_num = int(parts[0])
    honba = int(parts[1])  # 本场数
    riichi_sticks = int(parts[2])  # 立直棒
    
    # 解释小局名称
    if round_num < 4:
        round_name = f"东{round_num + 1}局"
    elif round_num < 8:
        round_name = f"南{round_num - 3}局"
    else:
        round_name = f"第{round_num}局"
    
    if honba > 0:
        round_name += f" {honba}本场"
    
    print(f"{i:<6} | {seed:<25} | {round_num:<6} | {honba:<6} | {round_name:<20}")

print("\n" + "="*70)
print("结论：")
print("  - seed 格式: round_num, honba, riichi_sticks, dice1, dice2, dora")
print("  - honba = 本场数（连庄次数）")
print("  - 当前数据库 **没有记录** honba 信息！")
print("="*70)

# 统计这场游戏中有多少连庄
honba_count = 0
for init_tag in root.findall('.//INIT'):
    seed = init_tag.get("seed", "0,0,0,0,0,0")
    honba = int(seed.split(',')[1])
    if honba > 0:
        honba_count += 1

print(f"\n本场游戏中连庄小局数: {honba_count}")

conn.close()
