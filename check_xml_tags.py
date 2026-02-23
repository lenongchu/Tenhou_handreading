import sqlite3
import gzip
import re

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# Get one log
cur.execute("SELECT id, log FROM logs WHERE log IS NOT NULL AND log != '' LIMIT 1")
log_id, xml_compressed = cur.fetchone()

# Decompress
xml_content = gzip.decompress(xml_compressed).decode('utf-8')

print(f"Log ID: {log_id}\n")

# Find D, E, F, G tags (discard tags)
discard_tags = re.findall(r'<[DEFG][^>]*>', xml_content)

print(f"Found {len(discard_tags)} discard tags\n")
print("First 20 discard tags:")
for i, tag in enumerate(discard_tags[:20]):
    print(f"  {i+1}. {tag}")

# Find T, U, V, W tags (draw tags)
print("\n\nFirst 20 draw tags:")
draw_tags = re.findall(r'<[TUVW][^>]*>', xml_content)
print(f"Found {len(draw_tags)} draw tags\n")
for i, tag in enumerate(draw_tags[:20]):
    print(f"  {i+1}. {tag}")

conn.close()
