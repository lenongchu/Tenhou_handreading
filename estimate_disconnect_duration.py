"""
推断掉线时长：通过分析 BYE 后的动作数量
思路：
1. 如果 BYE 后立即有该玩家的动作 → 短暂掉线（几秒）
2. 如果 BYE 后很久没有该玩家动作 → 长时间掉线
3. 如果 BYE 后小局直接结束 → 掉线到小局结束
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET
import re

def analyze_disconnect_duration(xml_content: str):
    """分析掉线时长"""
    root = ET.fromstring(xml_content)
    
    # 找出所有元素及其标签
    all_elements = list(root)
    
    results = []
    
    # 玩家编号对应的标签字母（摸牌和打牌）
    player_draw_tags = ['T', 'U', 'V', 'W']  # 0, 1, 2, 3
    player_discard_tags = ['D', 'E', 'F', 'G']  # 0, 1, 2, 3
    
    for idx, elem in enumerate(all_elements):
        if elem.tag == 'BYE':
            who = int(elem.get('who', -1))
            
            # 该玩家的动作标签前缀
            draw_prefix = player_draw_tags[who]
            discard_prefix = player_discard_tags[who]
            
            # 统计 BYE 后多少个动作才有该玩家的下一个动作
            actions_until_next = 0
            found_next_action = False
            small_round_ended = False
            
            for j in range(idx + 1, min(idx + 100, len(all_elements))):
                next_elem = all_elements[j]
                
                # 检查是否是该玩家的动作（摸牌或打牌）
                if next_elem.tag.startswith(draw_prefix) or next_elem.tag.startswith(discard_prefix):
                    found_next_action = True
                    break
                
                # 检查是否小局结束
                if next_elem.tag in ['AGARI', 'RYUUKYOKU', 'INIT']:
                    small_round_ended = True
                    break
                
                actions_until_next += 1
            
            results.append({
                'who': who,
                'actions_until_next': actions_until_next,
                'found_next_action': found_next_action,
                'small_round_ended': small_round_ended
            })
    
    return results

# 测试
conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

cur.execute('SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 1000')
logs = cur.fetchall()

disconnect_stats = []

for log_id, xml_compressed in logs:
    xml = gzip.decompress(xml_compressed).decode('utf-8')
    
    if '<BYE' in xml:
        results = analyze_disconnect_duration(xml)
        for r in results:
            disconnect_stats.append(r)

print("掉线时长分析（基于后续动作数）:")
print("="*70)
print(f"总 BYE 标签数: {len(disconnect_stats)}")
print()

# 分类统计
short_disconnect = [s for s in disconnect_stats if s['found_next_action'] and s['actions_until_next'] <= 5]
medium_disconnect = [s for s in disconnect_stats if s['found_next_action'] and 5 < s['actions_until_next'] <= 20]
long_disconnect = [s for s in disconnect_stats if s['found_next_action'] and s['actions_until_next'] > 20]
round_ended = [s for s in disconnect_stats if s['small_round_ended']]

print(f"短暂掉线（≤5 个动作后重连）: {len(short_disconnect)} ({len(short_disconnect)/len(disconnect_stats)*100:.1f}%)")
print(f"中等掉线（6-20 个动作）: {len(medium_disconnect)} ({len(medium_disconnect)/len(disconnect_stats)*100:.1f}%)")
print(f"长时间掉线（>20 个动作）: {len(long_disconnect)} ({len(long_disconnect)/len(disconnect_stats)*100:.1f}%)")
print(f"掉线到小局结束: {len(round_ended)} ({len(round_ended)/len(disconnect_stats)*100:.1f}%)")

print()
print("短暂掉线示例（前5个）:")
for s in short_disconnect[:5]:
    print(f"  玩家 {s['who']}, {s['actions_until_next']} 个动作后重连")

print()
print("建议:")
print("- 短暂掉线（≤5 个动作）可能是网络波动，可考虑保留")
print("- 长时间掉线或到小局结束的应该过滤")

conn.close()
