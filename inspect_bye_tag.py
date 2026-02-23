"""
查看 BYE 标签的详细信息
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 获取包含 BYE 标签的对局示例
cur.execute('SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 500')
logs = cur.fetchall()

bye_samples = []

for log_id, xml_compressed in logs:
    xml = gzip.decompress(xml_compressed).decode('utf-8')
    
    if '<BYE' in xml:
        bye_samples.append((log_id, xml))
        if len(bye_samples) >= 5:
            break

print(f"找到 {len(bye_samples)} 个包含 BYE 标签的对局示例:\n")

for i, (log_id, xml) in enumerate(bye_samples, 1):
    print(f"示例 {i}: {log_id}")
    print("="*70)
    
    try:
        root = ET.fromstring(xml)
        bye_elements = root.findall('.//BYE')
        
        print(f"找到 {len(bye_elements)} 个 BYE 标签:")
        for elem in bye_elements:
            print(f"  BYE 属性: {elem.attrib}")
        
        # 查看 BYE 标签的上下文
        import re
        bye_matches = re.findall(r'<BYE[^>]*>', xml)
        for match in bye_matches:
            print(f"  完整标签: {match}")
        
        print()
    except Exception as e:
        print(f"  解析错误: {e}\n")

conn.close()
