import sqlite3
import time
import sys
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("监控下载进度（每10秒刷新一次，Ctrl+C 退出）\n")

last_count = 0
conn = sqlite3.connect('data/tenhou.db')

try:
    while True:
        cur = conn.cursor()
        
        # 统计已下载的4人麻将
        cur.execute('SELECT COUNT(*) FROM logs WHERE num_players = 4 AND log IS NOT NULL AND log != ""')
        current_count = cur.fetchone()[0]
        
        # 统计总的4人麻将
        cur.execute('SELECT COUNT(*) FROM logs WHERE num_players = 4')
        total_count = cur.fetchone()[0]
        
        # 计算增量和进度
        increment = current_count - last_count
        progress = (current_count / total_count * 100) if total_count > 0 else 0
        
        print(f"\r已下载: {current_count:,} / {total_count:,} ({progress:.2f}%) | 本次增加: +{increment}", end='', flush=True)
        
        last_count = current_count
        time.sleep(10)
        
except KeyboardInterrupt:
    print("\n\n监控已停止")
finally:
    conn.close()
