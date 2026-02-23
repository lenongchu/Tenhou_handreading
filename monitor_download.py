"""
下载进度监控脚本

实时监控数据库文件大小变化
"""

import time
import os
from datetime import datetime

def format_size(bytes):
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes < 1024.0:
            return f"{bytes:.2f} {unit}"
        bytes /= 1024.0
    return f"{bytes:.2f} TB"

def monitor_download(db_path="data/tenhou.db", interval=10):
    """
    监控下载进度
    
    Args:
        db_path: 数据库文件路径
        interval: 检查间隔（秒）
    """
    print("="*60)
    print("数据下载进度监控")
    print("="*60)
    print(f"监控文件: {db_path}")
    print(f"检查间隔: {interval} 秒")
    print(f"按 Ctrl+C 停止监控\n")
    
    if not os.path.exists(db_path):
        print(f"错误：文件不存在 {db_path}")
        return
    
    last_size = 0
    start_time = time.time()
    
    try:
        while True:
            # 获取当前文件大小
            current_size = os.path.getsize(db_path)
            current_time = datetime.now().strftime("%H:%M:%S")
            
            # 计算增长
            if last_size > 0:
                growth = current_size - last_size
                speed = growth / interval  # 每秒增长
                
                print(f"[{current_time}] "
                      f"大小: {format_size(current_size)} | "
                      f"增长: {format_size(growth)} | "
                      f"速度: {format_size(speed)}/s")
            else:
                print(f"[{current_time}] "
                      f"初始大小: {format_size(current_size)}")
            
            last_size = current_size
            time.sleep(interval)
            
    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        print(f"\n监控停止")
        print(f"总耗时: {elapsed/60:.1f} 分钟")
        print(f"最终大小: {format_size(current_size)}")

if __name__ == "__main__":
    monitor_download()
