"""
深入分析 BYE 标签的机制
检查是否有"重连"标签，以及掉线期间的打牌是否有特殊标记
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 找到包含 BYE 标签的对局
cur.execute("""
    SELECT id, log 
    FROM logs 
    WHERE log IS NOT NULL 
    LIMIT 1000
""")

found_bye_logs = []
for log_id, log_data in cur.fetchall():
    xml = gzip.decompress(log_data).decode('utf-8')
    if '<BYE' in xml:
        found_bye_logs.append((log_id, xml))
        if len(found_bye_logs) >= 5:  # 只分析前5个
            break

print(f"找到 {len(found_bye_logs)} 个包含 BYE 标签的对局")
print("="*80)

for idx, (log_id, xml) in enumerate(found_bye_logs[:2]):  # 详细分析前2个
    print(f"\n对局 #{idx+1} (ID: {log_id})")
    print("-"*80)
    
    root = ET.fromstring(xml)
    all_elements = list(root)
    
    # 找到所有 BYE 标签的位置
    for elem_idx, elem in enumerate(all_elements):
        if elem.tag == 'BYE':
            who = int(elem.get('who', -1))
            print(f"\n找到 BYE 标签: who={who}")
            print(f"  BYE 标签的所有属性: {elem.attrib}")
            
            # 玩家对应的动作标签
            draw_tag = ['T', 'U', 'V', 'W'][who]
            discard_tag = ['D', 'E', 'F', 'G'][who]
            
            print(f"\n  该玩家的动作标签: 摸牌={draw_tag}, 打牌={discard_tag}")
            print(f"\n  BYE 前 5 个标签:")
            for i in range(max(0, elem_idx-5), elem_idx):
                prev_elem = all_elements[i]
                print(f"    {prev_elem.tag:<15} {prev_elem.attrib}")
            
            print(f"\n  BYE 后 20 个标签:")
            for i in range(elem_idx+1, min(elem_idx+21, len(all_elements))):
                next_elem = all_elements[i]
                marker = ""
                
                # 标记该玩家的动作
                if next_elem.tag.startswith(draw_tag):
                    marker = " <-- 该玩家摸牌"
                elif next_elem.tag.startswith(discard_tag):
                    marker = " <-- 该玩家打牌"
                
                # 检查是否有新的状态标签
                if 'BYE' in next_elem.tag or 'RECONNECT' in next_elem.tag or 'UN' in next_elem.tag:
                    marker += " *** 可能是状态变化 ***"
                
                print(f"    {next_elem.tag:<15} {next_elem.attrib}{marker}")
                
                if next_elem.tag in ['INIT', 'AGARI', 'RYUUKYOKU']:
                    print(f"    ... (小局结束)")
                    break

print("\n" + "="*80)
print("关键问题:")
print("="*80)
print("1. BYE 后是否有表示'重连'的标签？")
print("2. 自动摸切的动作是否有特殊属性标记？")
print("3. 如何区分'玩家真实打牌'和'系统自动摸切'？")

conn.close()
