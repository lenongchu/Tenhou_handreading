"""
监控 process_all_logs.py 的处理进度
实时显示已处理对局数、进度百分比、预计剩余时间
"""
import sqlite3
import time
import sys
import io
from datetime import datetime, timedelta

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def monitor_progress(interval=10):
    """
    监控处理进度
    
    Args:
        interval: 刷新间隔（秒）
    """
    db_path = 'data/tenhou.db'
    
    print("="*70)
    print("对局数据处理进度监控")
    print("="*70)
    print()
    
    # 获取总对局数（只读模式）
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
    total_logs = cur.fetchone()[0]
    conn.close()
    
    print(f"待处理总对局数: {total_logs:,}")
    print(f"刷新间隔: {interval} 秒")
    print()
    print("-"*70)
    
    prev_count = 0
    prev_time = time.time()
    start_time = prev_time
    
    try:
        while True:
            # 重新连接（只读模式，避免锁定）
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cur = conn.cursor()
                
                # 查询当前已处理的对局数
                cur.execute("SELECT COUNT(DISTINCT log_id) FROM game_states")
                current_count = cur.fetchone()[0]
                
                # 查询总状态数
                cur.execute("SELECT COUNT(*) FROM game_states")
                total_states = cur.fetchone()[0]
                
                conn.close()
            except sqlite3.OperationalError as e:
                print(f"[警告] 数据库暂时锁定，稍后重试...")
                time.sleep(2)
                continue
            
            # 计算进度
            progress = (current_count / total_logs * 100) if total_logs > 0 else 0
            
            # 计算处理速度
            current_time = time.time()
            elapsed = current_time - prev_time
            processed_since_last = current_count - prev_count
            
            if elapsed > 0:
                speed = processed_since_last / elapsed
            else:
                speed = 0
            
            # 估算剩余时间
            remaining_logs = total_logs - current_count
            if speed > 0:
                eta_seconds = remaining_logs / speed
                eta = timedelta(seconds=int(eta_seconds))
            else:
                eta = "未知"
            
            # 总运行时间
            total_elapsed = current_time - start_time
            total_elapsed_str = str(timedelta(seconds=int(total_elapsed)))
            
            # 显示进度
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] 已处理: {current_count:,}/{total_logs:,} ({progress:.2f}%)")
            print(f"  总状态数: {total_states:,}")
            print(f"  处理速度: {speed:.2f} 场/秒")
            print(f"  已运行: {total_elapsed_str}")
            print(f"  预计剩余: {eta}")
            print("-"*70)
            
            # 检查是否完成
            if current_count >= total_logs:
                print("\n✓ 处理完成！")
                break
            
            # 更新记录
            prev_count = current_count
            prev_time = current_time
            
            # 等待下一次刷新
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print("\n\n监控已停止（Ctrl+C）")

if __name__ == "__main__":
    # 默认10秒刷新，可通过命令行参数调整
    import sys
    interval = 10
    if len(sys.argv) > 1:
        try:
            interval = int(sys.argv[1])
        except ValueError:
            print(f"无效参数，使用默认间隔 {interval} 秒")
    
    monitor_progress(interval)
