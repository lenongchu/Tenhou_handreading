"""检查 mjlog XML 中的 INIT 标签格式"""
import sqlite3
import gzip
import xml.etree.ElementTree as ET

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 获取一个有内容的对局
cur.execute("SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 1")
log_id, xml_compressed = cur.fetchone()

xml_content = gzip.decompress(xml_compressed).decode('utf-8')
root = ET.fromstring(xml_content)

print(f"对局ID: {log_id}\n")
print("="*60)
print("所有 INIT 标签及其属性：\n")

for i, init_tag in enumerate(root.findall('.//INIT')):
    print(f"[小局 {i}]")
    print(f"  属性:")
    for attr, value in init_tag.attrib.items():
        print(f"    {attr}: {value}")
    print()

conn.close()
