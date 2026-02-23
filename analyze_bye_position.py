"""
分析 BYE 标签与小局的关系
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET
import re

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 获取包含 BYE 的对局
cur.execute('SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 500')
logs = cur.fetchall()

bye_samples = []

for log_id, xml_compressed in logs:
    xml = gzip.decompress(xml_compressed).decode('utf-8')
    
    if '<BYE' in xml:
        bye_samples.append((log_id, xml))
        if len(bye_samples) >= 5:
            break

print("分析 BYE 标签在 XML 中的位置:\n")

for i, (log_id, xml) in enumerate(bye_samples, 1):
    print(f"示例 {i}: {log_id}")
    print("="*70)
    
    # 查找 INIT 标签（小局开始）
    init_matches = list(re.finditer(r'<INIT[^>]*>', xml))
    print(f"小局数: {len(init_matches)}")
    
    # 查找 BYE 标签
    bye_matches = list(re.finditer(r'<BYE[^>]*>', xml))
    print(f"BYE 标签数: {len(bye_matches)}")
    
    # 分析每个 BYE 标签所在的小局
    for bye_match in bye_matches:
        bye_pos = bye_match.start()
        bye_tag = bye_match.group()
        
        # 找到最近的 INIT 标签（该 BYE 属于哪个小局）
        round_num = 0
        for j, init_match in enumerate(init_matches):
            if init_match.start() < bye_pos:
                round_num = j
            else:
                break
        
        print(f"  BYE 标签: {bye_tag}")
        print(f"    位置: {bye_pos}")
        print(f"    所在小局: {round_num}")
        
        # 查看 BYE 标签前后的内容
        context_start = max(0, bye_pos - 200)
        context_end = min(len(xml), bye_pos + 200)
        context = xml[context_start:context_end]
        
        # 显示前后的标签
        nearby_tags = re.findall(r'<(\w+)[^>]*>', context)
        print(f"    附近标签: {nearby_tags[-5:]}")
    
    print()

conn.close()

print("\n结论:")
print("BYE 标签出现在特定小局中，可以在分析时按小局过滤，")
print("而不是删除整场对局。")
