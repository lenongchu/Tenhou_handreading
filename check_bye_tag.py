"""
查找掉线/BYE 标签
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 查找 20 个对局
cur.execute('SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 20')
logs = cur.fetchall()

bye_count = 0
no_bye_count = 0

for log_id, xml_compressed in logs:
    xml = gzip.decompress(xml_compressed).decode('utf-8')
    
    # 查找 BYE 标签
    if '<BYE' in xml:
        bye_count += 1
        print(f"对局 {log_id} 包含 BYE 标签")
        
        # 提取 BYE 标签
        root = ET.fromstring(xml)
        bye_elements = root.findall('.//BYE')
        for elem in bye_elements:
            print(f"  BYE 属性: {elem.attrib}")
    else:
        no_bye_count += 1

print(f"\n统计:")
print(f"包含 BYE 标签: {bye_count}/{len(logs)}")
print(f"不包含 BYE 标签: {no_bye_count}/{len(logs)}")

conn.close()
