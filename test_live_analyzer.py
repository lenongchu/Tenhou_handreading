"""
测试实时分析引擎
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.live_analyzer import LiveAnalyzer, get_database_stats
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    db_path = 'data/tenhou.db'
    
    # 显示数据库状态
    print("="*70)
    print("数据库状态")
    print("="*70)
    stats = get_database_stats(db_path)
    print(f"对局总数: {stats['total_logs']:,}")
    print(f"数据库大小: {stats['db_size_mb']:.2f} MB")
    print()
    
    # 创建分析器
    analyzer = LiveAnalyzer(db_path)
    
    # 测试查询
    print("="*70)
    print("测试查询：分析舍牌模式 7s-9s，目标牌 8s")
    print("只分析前 1000 场对局（快速测试）")
    print("="*70)
    print()
    
    def progress_callback(current, total):
        if current % 100 == 0:
            print(f"进度: {current:,}/{total:,} ({current/total*100:.1f}%)")
    
    result = analyzer.analyze_discard_pattern(
        query_pattern=["7s", "9s"],
        target_tile="8s",
        dora_constraint="dora_unrelated",
        visible_constraints={"8s": (0, 2)},
        sample_limit=1000,
        progress_callback=progress_callback
    )
    
    print()
    print("="*70)
    print("查询结果")
    print("="*70)
    print(f"查询模式: {result['query_pattern_str']}")
    print(f"等价变体数: {result['variants_count']}")
    print(f"目标牌: {result['target_tile']}")
    print(f"")
    print(f"分析对局数: {result['total_logs_analyzed']:,}")
    print(f"匹配状态数: {result['total_matches']:,}")
    print(f"")
    print(f"概率分布:")
    print(f"  有0张: {result['probability_distribution'][0]:.2f}% ({result['target_count_distribution'][0]:,} 例)")
    print(f"  有1张: {result['probability_distribution'][1]:.2f}% ({result['target_count_distribution'][1]:,} 例)")
    print(f"  有2张: {result['probability_distribution'][2]:.2f}% ({result['target_count_distribution'][2]:,} 例)")
    print(f"  有3张: {result['probability_distribution'][3]:.2f}% ({result['target_count_distribution'][3]:,} 例)")
    print()
    
    if result['matched_states']:
        print(f"匹配状态示例（前3个）:")
        for i, state in enumerate(result['matched_states'][:3], 1):
            print(f"  {i}. 小局 {state['round_num']}, 巡目 {state['turn']}, "
                  f"目标牌数量: {state['target_count']} 张")

if __name__ == "__main__":
    main()
