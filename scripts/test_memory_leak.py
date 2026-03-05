import sys
import os
import time
import psutil
import logging

# 确保能导入 src
sys.path.append(os.getcwd())

from src.live_analyzer import LiveAnalyzer

logging.basicConfig(level=logging.INFO)

def monitor_memory(analyzer, query_pattern, target_tile):
    process = psutil.Process(os.getpid())
    print(f"Starting memory monitoring. Initial RSS: {process.memory_info().rss / 1024 / 1024:.2f} MB")
    
    start_time = time.time()
    
    # 模拟即时铳率分析
    # 设置一个较大的 batch_size 和总对局数限制，观察内存变化
    result = analyzer.analyze_discard_pattern(
        query_pattern=query_pattern,
        target_tile=target_tile,
        analysis_target="deal_in_instant",
        sample_limit=1000,
        analysis_batch_size=200,
        max_workers=2  # 并行模式
    )
    
    end_time = time.time()
    print(f"Analysis finished in {end_time - start_time:.2f} seconds.")
    print(f"Final RSS: {process.memory_info().rss / 1024 / 1024:.2f} MB")
    print(f"Matches: {result.get('total_matches')}")

if __name__ == "__main__":
    db_path = r"E:\Cursor\Tenhou data\data\tenhou.db"
    if not os.path.exists(db_path):
        # 尝试默认路径
        db_path = os.path.join(os.getcwd(), "data", "tenhou.db")
    
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        sys.exit(1)

    analyzer = LiveAnalyzer(db_path)
    # 使用一个非常容易匹配的模式
    # "z" 代表任意字牌
    result = analyzer.analyze_discard_pattern(
        query_pattern=["z"],
        target_tile="1z",
        analysis_target="deal_in_instant",
        sample_limit=2000,
        analysis_batch_size=200,
        max_workers=2
    )
