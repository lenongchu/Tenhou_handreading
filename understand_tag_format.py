"""
研究 mjlog XML 标签的含义
查看打牌动作的实际格式
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET
import re

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 获取一个对局
cur.execute('SELECT log FROM logs WHERE log IS NOT NULL LIMIT 1')
xml = gzip.decompress(cur.fetchone()[0]).decode('utf-8')

root = ET.fromstring(xml)

print("分析一个完整对局的标签结构:")
print("="*70)

# 找到第一个 INIT 标签（小局开始）
init_tag = root.find('.//INIT')
if init_tag:
    print(f"\nINIT 标签（小局开始）:")
    print(f"  属性: {init_tag.attrib}")
    
    # 获取 INIT 后的所有兄弟节点（该小局的所有动作）
    all_elements = list(root)
    init_index = all_elements.index(init_tag)
    
    print(f"\n该小局的动作序列（前 30 个标签）:")
    print(f"{'序号':<6} | {'标签':<20} | {'属性':<50}")
    print("-"*80)
    
    count = 0
    for elem in all_elements[init_index+1:init_index+31]:
        print(f"{count:<6} | {elem.tag:<20} | {str(elem.attrib):<50}")
        count += 1
        
        # 如果遇到下一个 INIT，说明新小局开始
        if elem.tag == 'INIT' and count > 0:
            break

print("\n" + "="*70)
print("标签含义说明:")
print("="*70)
print("T/D/E/F/G/U/V/W + 数字 = 打牌动作")
print("  - T = 玩家0 摸牌, D = 玩家1 摸牌, E = 玩家2 摸牌, F = 玩家3 摸牌")
print("  - G = 玩家0 打牌, U = 玩家1 打牌, V = 玩家2 打牌, W = 玩家3 打牌")
print("  - 数字 = 牌的编码（0-135）")
print()
print("N = 鸣牌（吃/碰/杠）")
print("REACH = 立直")
print("AGARI = 和牌")
print("RYUUKYOKU = 流局")

conn.close()
