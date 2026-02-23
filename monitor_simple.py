"""
实时监控处理进度 - 精简版
每30秒刷新一次
"""
import sqlite3
import time
from datetime import datetime, timedelta
import sys
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db_path = 'data/tenhou.db'

print("="*70)
print("对局处理进度监控")
print("每30秒刷新 | 按 Ctrl+C 退出")
print("="*70)
print()

# 获取总数
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
total = cur.fetchone()[0]
conn.close()

print(f"待处理总数: {total:,} 场对局\n")

prev_count = 0
prev_time = time.time()
start_time = prev_time

try:
    while True:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
            cur = conn.cursor()
            
            cur.execute("SELECT COUNT(DISTINCT log_id) FROM game_states")
            current = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM game_states")
            states = cur.fetchone()[0]
            
            conn.close()
            
            # 计算进度
            progress = (current / total * 100) if total > 0 else 0
            
            # 速度
            now = time.time()
            elapsed = now - prev_time
            delta = current - prev_count
            speed = delta / (elapsed / 60) if elapsed > 0 else 0
            
            # ETA
            remaining = total - current
            eta_min = remaining / speed if speed > 0 else 0
            eta_str = str(timedelta(minutes=int(eta_min)))
            
            # 运行时间
            runtime = str(timedelta(seconds=int(now - start_time)))
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                  f"{current:,}/{total:,} ({progress:.1f}%) | "
                  f"状态数: {states:,} | "
                  f"速度: {speed:.0f}场/分 | "
                  f"已运行: {runtime} | "
                  f"剩余: {eta_str}")
            
            if current >= total:
                print("\n✓ 处理完成!")
                break
            
            prev_count = current
            prev_time = now
            
        except Exception as e:
            print(f"[警告] 查询失败: {e}")
        
        time.sleep(30)
        
except KeyboardInterrupt:
    print("\n\n监控已停止")
