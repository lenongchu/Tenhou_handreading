"""
提取包含 BYE 标签的完整牌谱
保存为格式化的 XML 文件供人工观察
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET
import xml.dom.minidom as minidom

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 找到包含 BYE 标签的对局
cur.execute("""
    SELECT id, log 
    FROM logs 
    WHERE log IS NOT NULL 
    LIMIT 2000
""")

found_logs = []
for log_id, log_data in cur.fetchall():
    xml = gzip.decompress(log_data).decode('utf-8')
    if '<BYE' in xml:
        found_logs.append((log_id, xml))
        if len(found_logs) >= 3:  # 提取3个样本
            break

conn.close()

print(f"找到 {len(found_logs)} 个包含 BYE 标签的对局")
print("="*80)

# 保存每个牌谱
for idx, (log_id, xml) in enumerate(found_logs):
    # 格式化 XML
    try:
        dom = minidom.parseString(xml)
        pretty_xml = dom.toprettyxml(indent="  ", encoding="utf-8").decode('utf-8')
    except:
        # 如果格式化失败，使用原始 XML
        pretty_xml = xml
    
    # 保存到文件
    filename = f"sample_bye_log_{idx+1}_{log_id.replace('-', '_')}.xml"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(pretty_xml)
    
    print(f"\n牌谱 #{idx+1}")
    print(f"  对局 ID: {log_id}")
    print(f"  文件: {filename}")
    
    # 统计 BYE 标签信息
    root = ET.fromstring(xml)
    bye_tags = root.findall('.//BYE')
    print(f"  BYE 标签数量: {len(bye_tags)}")
    for bye in bye_tags:
        who = bye.get('who', '?')
        print(f"    - 玩家 {who} 掉线")

print("\n" + "="*80)
print("文件已保存，您可以用文本编辑器打开查看完整牌谱")
print("建议使用支持 XML 语法高亮的编辑器（如 VS Code、Notepad++）")
