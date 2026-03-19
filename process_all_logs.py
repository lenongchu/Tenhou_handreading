"""
Process ALL logs and populate game_states table
生产版本 - 批量处理所有已下载的对局数据，预填充 game_states 表

================================================================================
功能说明
================================================================================
本脚本遍历 logs 表中所有带内容的对局，解析牌谱并写入 game_states 表，用于：

1. 预计算舍牌状态：将每局、每玩家、每巡的舍牌状态（手牌、可见牌、宝牌、舍牌模式等）
   解析后写入 game_states，便于按 normalized_pattern 做索引查询。

2. 典型使用场景：
   - 初次导入大量牌谱后的批量预处理
   - compact_database 压缩数据库（删除 game_states）后重建索引
   - 需要 Database.query_by_pattern / query_with_visible_constraints 的旧查询路径

================================================================================
是否仍在使用
================================================================================
仍在项目中保留并使用，但需注意：

- 当前主分析流程（LiveAnalyzer）：直接从 logs 表读取 log_json（或 log），
  实时解析 tenhou6 JSON/XML，不依赖预填充的 game_states。因此主分析不要求
  事先运行本脚本。

- 若需要 game_states 表（如旧查询、统计、compact 后重建）或希望预建索引，
  可运行本脚本或其优化版。

================================================================================
数据源与格式限制
================================================================================
- 数据源：仅读取 logs 表的 log 列（gzip 压缩的 XML 格式）
- 不支持：log_json 列（tenhou6 JSON）。若数据已迁移为 tenhou6 且 log 列为空，
  本脚本无法处理。此时请使用 process_logs_sample.py（支持 log_json 与 XML）。

================================================================================
与其他脚本的关系
================================================================================
- process_all_logs_optimized.py：性能优化版（批量插入、WAL 模式等），功能等效，
  处理速度更快。compact_database 建议重建时使用：echo yes | py process_all_logs_optimized.py

- process_logs_sample.py：使用 parse_log_to_game_states，支持 tenhou6 JSON 与 XML，
  可指定处理数量（如 py process_logs_sample.py 10000 --clear），适合新架构与小批量测试。
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import sqlite3
import gzip
from src.database import Database
from src.mjlog_parser import MjlogParser
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    db_path = 'data/tenhou.db'
    
    # Step 1: Create tables
    logger.info("Step 1: Creating game_states tables...")
    with Database(db_path) as db:
        db.create_tables()
    logger.info("Tables created successfully!")
    
    # Step 2: Get total count
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
    total_logs = cur.fetchone()[0]
    logger.info(f"Found {total_logs:,} logs with content to process")
    
    # Ask for confirmation
    print(f"\n{'='*70}")
    print(f"准备处理 {total_logs:,} 条对局数据")
    print(f"预计处理时间: {total_logs * 0.15 / 60:.1f} 分钟")
    print(f"预计最终数据库大小: ~{total_logs * 0.5:.0f} MB 额外空间")
    print(f"{'='*70}\n")
    
    user_input = input("确认开始处理所有数据？(yes/no): ").strip().lower()
    if user_input != 'yes':
        logger.info("取消处理")
        return
    
    # Step 3: Process logs in batches
    logger.info("Step 3: Processing all logs...")
    
    batch_size = 1000
    processed_count = 0
    error_count = 0
    
    with Database(db_path) as db:
        offset = 0
        
        while True:
            # Fetch batch
            cur.execute("""
                SELECT id, log 
                FROM logs 
                WHERE log IS NOT NULL AND log != ''
                LIMIT ? OFFSET ?
            """, (batch_size, offset))
            
            logs = cur.fetchall()
            
            if not logs:
                break
            
            logger.info(f"Processing batch {offset // batch_size + 1} ({offset + 1} to {offset + len(logs)})...")
            
            for log_id, xml_content_compressed in logs:
                try:
                    # Decompress GZIP data
                    if isinstance(xml_content_compressed, bytes):
                        xml_content = gzip.decompress(xml_content_compressed).decode('utf-8')
                    else:
                        xml_content = xml_content_compressed
                    
                    # Parse XML for this log
                    parser = MjlogParser(xml_content)
                    game_states = parser.parse()
                    
                    # Process each player's states
                    for player_state in game_states:
                        # Extract hand-discarded tiles (not tsumogiri)
                        hand_discards = [d.tile for d in player_state.discards if not d.is_tsumogiri]
                        
                        if not hand_discards:
                            continue
                        
                        # Insert each discard state
                        for i, discard in enumerate(player_state.discards):
                            # Pattern up to this turn (only hand discards)
                            pattern_so_far = [d.tile for d in player_state.discards[:i+1] if not d.is_tsumogiri]
                            if pattern_so_far:
                                # Convert tile codes to strings
                                pattern_strings = [MjlogParser.tile_to_string(t) for t in pattern_so_far]
                                # Normalize the pattern for equivalence matching
                                norm_pattern = "-".join(pattern_strings) if pattern_strings else ""
                            else:
                                norm_pattern = None
                            
                            db.insert_game_state(
                                log_id=log_id,
                                round_num=player_state.round_num,
                                honba=player_state.honba,
                                player_id=player_state.player_id,
                                turn=discard.turn,
                                tile=discard.tile,
                                is_tsumogiri=discard.is_tsumogiri,
                                hand_tiles=player_state.hand_tiles,
                                visible_tiles=player_state.visible_tiles,
                                dora_indicators=player_state.dora_indicators,
                                normalized_pattern=norm_pattern
                            )
                    
                    processed_count += 1
                    
                    if processed_count % 100 == 0:
                        db.conn.commit()
                        logger.info(f"Progress: {processed_count:,}/{total_logs:,} logs ({processed_count/total_logs*100:.1f}%)")
                        
                except Exception as e:
                    logger.error(f"Error processing log {log_id}: {e}")
                    error_count += 1
                    continue
            
            db.conn.commit()
            offset += batch_size
    
    logger.info(f"\n{'='*70}")
    logger.info("=== Processing Complete ===")
    logger.info(f"Successfully processed: {processed_count:,}")
    logger.info(f"Errors: {error_count:,}")
    logger.info(f"{'='*70}")
    
    # Step 4: Show stats
    with Database(db_path) as db:
        stats = db.get_database_stats()
        logger.info(f"\nDatabase stats: {stats}")
    
    conn.close()

if __name__ == "__main__":
    main()
