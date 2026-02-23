"""
Process ALL logs - OPTIMIZED VERSION
性能优化版：批量插入，减少 commit 次数
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import sqlite3
import gzip
import json
from collections import Counter
from src.database import Database
from src.mjlog_parser import MjlogParser
from src.simple_normalizer import normalize_discard_pattern
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def batch_insert_states(db, states_batch):
    """
    批量插入游戏状态（优化性能）
    
    Args:
        db: Database实例
        states_batch: 状态数据列表 [(log_id, round_num, honba, player_id, turn, ...)]
    """
    if not states_batch:
        return
    
    # 批量插入 game_states
    db.cursor.executemany("""
    INSERT OR IGNORE INTO game_states (
        log_id, round_num, honba, player_id, turn, tile, is_tsumogiri,
        hand_tiles, visible_tiles, dora_indicators, normalized_pattern
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, states_batch)
    
    # 注意：visible_tile_stats 暂不处理（可选优化：后期批量生成）

def main():
    db_path = 'data/tenhou.db'
    
    # Step 1: Create tables
    logger.info("Step 1: Creating game_states tables...")
    with Database(db_path) as db:
        db.create_tables()
    logger.info("Tables created successfully!")
    
    # Step 2: Get total count
    conn_temp = sqlite3.connect(db_path)
    cur_temp = conn_temp.cursor()
    
    cur_temp.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
    total_logs = cur_temp.fetchone()[0]
    logger.info(f"Found {total_logs:,} logs with content to process")
    
    conn_temp.close()
    
    # Ask for confirmation
    print(f"\n{'='*70}")
    print(f"准备处理 {total_logs:,} 条对局数据（优化版）")
    print(f"预计处理时间: {total_logs * 0.03 / 60:.1f} 分钟（优化后）")
    print(f"{'='*70}\n")
    
    user_input = input("确认开始处理所有数据？(yes/no): ").strip().lower()
    if user_input != 'yes':
        logger.info("取消处理")
        return
    
    # Step 3: Process logs in batches
    logger.info("Step 3: Processing all logs (OPTIMIZED)...")
    
    batch_size = 1000
    insert_batch_size = 5000  # 每5000条状态记录一次性插入
    processed_count = 0
    error_count = 0
    
    with Database(db_path) as db:
        # 性能优化：WAL模式 + 异步写入
        db.cursor.execute("PRAGMA journal_mode=WAL")
        db.cursor.execute("PRAGMA synchronous=NORMAL")  # NORMAL比OFF更安全，但仍快
        db.cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        db.conn.commit()
        
        offset = 0
        states_buffer = []  # 缓冲区：累积待插入的状态
        
        while True:
            # Fetch batch (使用同一个数据库连接)
            db.cursor.execute("""
                SELECT id, log 
                FROM logs 
                WHERE log IS NOT NULL AND log != ''
                LIMIT ? OFFSET ?
            """, (batch_size, offset))
            
            logs = db.cursor.fetchall()
            
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
                        
                        # Prepare data for batch insert
                        for i, discard in enumerate(player_state.discards):
                            # Pattern up to this turn (only hand discards)
                            pattern_so_far = [d.tile for d in player_state.discards[:i+1] if not d.is_tsumogiri]
                            if pattern_so_far:
                                # Convert tile codes to strings
                                pattern_strings = [MjlogParser.tile_to_string(t) for t in pattern_so_far]
                                # Normalize the pattern for equivalence matching
                                norm_pattern = normalize_discard_pattern(pattern_strings)
                            else:
                                norm_pattern = None
                            
                            # Convert data to JSON
                            hand_tiles_json = json.dumps(list(player_state.hand_tiles))
                            visible_tiles_json = json.dumps(dict(player_state.visible_tiles))
                            dora_indicators_json = json.dumps(player_state.dora_indicators)
                            
                            # Add to buffer
                            states_buffer.append((
                                log_id,
                                player_state.round_num,
                                player_state.honba,
                                player_state.player_id,
                                discard.turn,
                                discard.tile,
                                int(discard.is_tsumogiri),
                                hand_tiles_json,
                                visible_tiles_json,
                                dora_indicators_json,
                                norm_pattern
                            ))
                            
                            # 达到批量插入阈值：立即插入
                            if len(states_buffer) >= insert_batch_size:
                                batch_insert_states(db, states_buffer)
                                db.conn.commit()
                                states_buffer = []
                    
                    processed_count += 1
                    
                    if processed_count % 100 == 0:
                        logger.info(f"Progress: {processed_count:,}/{total_logs:,} logs ({processed_count/total_logs*100:.1f}%)")
                        
                except Exception as e:
                    logger.error(f"Error processing log {log_id}: {e}")
                    error_count += 1
                    continue
            
            # 每批次结束：提交剩余数据
            if states_buffer:
                batch_insert_states(db, states_buffer)
                db.conn.commit()
                states_buffer = []
            
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

if __name__ == "__main__":
    main()
