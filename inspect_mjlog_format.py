"""
检查 mjlog XML 格式，查找立直和掉线信息
"""
import sqlite3
import gzip
import re
from xml.etree import ElementTree as ET

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 获取几个对局样本
cur.execute('SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 5')
logs = cur.fetchall()

for log_id, xml_compressed in logs:
    print(f"\n{'='*70}")
    print(f"对局 ID: {log_id}")
    print('='*70)
    
    xml = gzip.decompress(xml_compressed).decode('utf-8')
    
    # 查找所有标签
    tags = re.findall(r'<(\w+)', xml)
    unique_tags = set(tags)
    
    print(f"XML 标签: {sorted(unique_tags)}")
    
    # 查找立直相关标签
    reach_tags = [t for t in unique_tags if 'REACH' in t.upper() or 'RIICHI' in t.upper()]
    if reach_tags:
        print(f"\n立直相关标签: {reach_tags}")
        # 显示立直标签内容
        for tag in reach_tags:
            matches = re.findall(f'<{tag}[^>]*>', xml)
            if matches:
                print(f"  {tag} 示例: {matches[:3]}")
    
    # 查找掉线相关标签或属性
    # 可能的关键词: drop, disconnect, afk, leave, quit
    disconnect_keywords = ['DROP', 'DISCONNECT', 'AFK', 'LEAVE', 'QUIT', 'BYE', 'LOST']
    disconnect_tags = [t for t in unique_tags if any(kw in t.upper() for kw in disconnect_keywords)]
    if disconnect_tags:
        print(f"\n掉线相关标签: {disconnect_tags}")
        for tag in disconnect_tags:
            matches = re.findall(f'<{tag}[^>]*>', xml)
            if matches:
                print(f"  {tag} 示例: {matches[:3]}")
    
    # 解析 XML 查看结构
    try:
        root = ET.fromstring(xml)
        print(f"\n根元素: {root.tag}")
        print(f"根元素属性: {root.attrib}")
        
        # 查找 REACH 标签
        reach_elements = root.findall('.//REACH')
        if reach_elements:
            print(f"\n找到 {len(reach_elements)} 个 REACH 标签:")
            for elem in reach_elements[:3]:
                print(f"  属性: {elem.attrib}")
        
        # 查找所有子元素类型
        all_children = []
        for child in root:
            all_children.append(child.tag)
        print(f"\n子元素类型: {set(all_children)}")
        
    except Exception as e:
        print(f"\nXML 解析错误: {e}")
    
    print()

conn.close()

print("\n" + "="*70)
print("总结")
print("="*70)
print("请查看以上输出，确认是否包含立直和掉线信息")
