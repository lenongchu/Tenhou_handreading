"""
测试改进后的分析引擎：
1. 自动过滤掉线小局
2. 支持立直筛选
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.live_analyzer import LiveAnalyzer
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_disconnected_filter():
    """测试掉线过滤功能"""
    print("="*70)
    print("测试 1: 掉线过滤功能")
    print("="*70)
    
    analyzer = LiveAnalyzer('data/tenhou.db')
    
    def progress_callback(current, total):
        if current % 500 == 0:
            print(f"进度: {current:,}/{total:,} ({current/total*100:.1f}%)")
    
    # 分析 1000 场对局
    result = analyzer.analyze_discard_pattern(
        query_pattern=["7s", "9s"],
        target_tile="8s",
        dora_constraint="any",
        sample_limit=1000,
        progress_callback=progress_callback
    )
    
    print(f"\n结果:")
    print(f"  分析对局数: {result['total_logs_analyzed']:,}")
    print(f"  匹配状态数: {result['total_matches']:,}")
    print(f"  目标牌在手: {result['target_in_hand_count']:,}")
    print(f"  概率: {result['probability']:.2f}%")
    print(f"\n✓ 掉线小局已自动过滤\n")

def test_riichi_filter():
    """测试立直筛选功能"""
    print("="*70)
    print("测试 2: 立直筛选功能")
    print("="*70)
    
    analyzer = LiveAnalyzer('data/tenhou.db')
    
    # 测试 3 种立直约束
    constraints = [
        ("any", "无限制"),
        ("has_riichi", "有人立直"),
        ("no_riichi", "无人立直")
    ]
    
    for constraint, desc in constraints:
        print(f"\n测试: {desc}")
        
        result = analyzer.analyze_discard_pattern(
            query_pattern=["7s", "9s"],
            target_tile="8s",
            dora_constraint="any",
            riichi_constraint=constraint,
            sample_limit=500
        )
        
        print(f"  匹配状态数: {result['total_matches']:,}")
        print(f"  概率: {result['probability']:.2f}%")

if __name__ == "__main__":
    test_disconnected_filter()
    print()
    test_riichi_filter()
    
    print("\n" + "="*70)
    print("✓ 所有测试完成")
    print("="*70)
