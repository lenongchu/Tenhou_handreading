"""
在更多对局中查找掉线相关标签
"""
import sqlite3
import gzip
import re

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# 查找 1000 个对局
cur.execute('SELECT id, log FROM logs WHERE log IS NOT NULL LIMIT 1000')
logs = cur.fetchall()

keywords = ['BYE', 'DISCONNECT', 'DROP', 'AFK', 'LEAVE', 'QUIT', 'LOST', 'TIMEOUT']

findings = {kw: 0 for kw in keywords}
sample_logs = {kw: [] for kw in keywords}

for log_id, xml_compressed in logs:
    xml = gzip.decompress(xml_compressed).decode('utf-8')
    
    for keyword in keywords:
        if f'<{keyword}' in xml or f'{keyword}=' in xml:
            findings[keyword] += 1
            if len(sample_logs[keyword]) < 3:
                sample_logs[keyword].append(log_id)

print("在 1000 场对局中查找掉线相关标签:")
print("="*70)
for kw, count in findings.items():
    if count > 0:
        print(f"{kw}: {count} 场对局")
        if sample_logs[kw]:
            print(f"  示例: {sample_logs[kw]}")
    else:
        print(f"{kw}: 未发现")

conn.close()
