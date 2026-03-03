"""
Process logs and populate game_states table
支持通过命令行参数指定处理数量，例如：
  py process_logs_sample.py 1000          # 处理1000条
  py process_logs_sample.py 10000 --clear # 清空后处理10000条
"""
import sys
import os
import argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import sqlite3
import gzip
from src.database import Database
from src.live_analyzer import parse_log_to_game_states
from src.mjlog_parser import MjlogParser
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 默认处理数量（测试用）
DEFAULT_LIMIT = 100

def main():
    parser = argparse.ArgumentParser(description='处理对局 log 并填充 game_states 表')
    parser.add_argument('limit', nargs='?', type=int, default=DEFAULT_LIMIT,
                        help=f'处理的对局数量（默认 {DEFAULT_LIMIT}）')
    parser.add_argument('--clear', action='store_true',
                        help='处理前清空 game_states 表，避免重复数据')
    args = parser.parse_args()
    
    limit = max(1, args.limit)
    if limit != args.limit:
        logger.warning(f"limit 已调整为 {limit}")
    
    db_path = 'data/tenhou.db'
    
    # Step 1: Create tables
    logger.info("Step 1: Creating game_states tables...")
    with Database(db_path) as db:
        db.create_tables()
    logger.info("Tables created successfully!")
    
    # Step 1.5: 可选清空已有数据（扩大样本时建议使用，避免重复）
    if args.clear:
        logger.info("Step 1.5: Clearing existing game_states data...")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM visible_tile_stats")
        cur.execute("DELETE FROM game_states")
        conn.commit()
        conn.close()
        logger.info("Cleared successfully!")
    
    # Step 2: Process a sample of logs
    logger.info(f"Step 2: Processing logs (limit={limit:,})...")
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(logs)")
    columns = [row[1] for row in cur.fetchall()]
    if "log_json" not in columns:
        try:
            cur.execute("ALTER TABLE logs ADD COLUMN log_json BLOB")
            conn.commit()
            columns.append("log_json")
        except sqlite3.OperationalError:
            pass
    has_log_json = "log_json" in columns
    if has_log_json:
        cur.execute(
            "SELECT id, COALESCE(NULLIF(log_json, ''), log) FROM logs WHERE (log_json IS NOT NULL AND log_json != '') OR (log IS NOT NULL AND log != '') LIMIT ?",
            (limit,),
        )
    else:
        cur.execute("SELECT id, log FROM logs WHERE log IS NOT NULL AND log != '' LIMIT ?", (limit,))
    logs = cur.fetchall()
    logger.info(f"Found {len(logs)} logs to process")
    
    with Database(db_path) as db:
        processed_count = 0
        error_count = 0
        
        for log_id, log_content in logs:
            try:
                # 解析对局（parse_log_to_game_states 支持 tenhou6 JSON 或 gzip XML）
                game_states = parse_log_to_game_states(log_content)
                
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
                            honba=player_state.honba,  # Add honba field
                            player_id=player_state.player_id,
                            turn=discard.turn,
                            tile=discard.tile,
                            is_tsumogiri=discard.is_tsumogiri,
                            hand_tiles=player_state.hand_tiles,
                            visible_tiles=player_state.visible_tiles,
                            dora_indicators=player_state.dora_indicators,
                            normalized_pattern=norm_pattern
                        )
                
                db.conn.commit()
                processed_count += 1
                
                if processed_count % 10 == 0:
                    logger.info(f"Processed {processed_count}/{len(logs)} logs...")
                    
            except Exception as e:
                logger.error(f"Error processing log {log_id}: {e}")
                error_count += 1
                continue
    
    logger.info(f"\n=== Processing Complete ===")
    logger.info(f"Successfully processed: {processed_count}")
    logger.info(f"Errors: {error_count}")
    
    # Step 3: Show stats
    with Database(db_path) as db:
        stats = db.get_database_stats()
        logger.info(f"\nDatabase stats: {stats}")
    
    conn.close()

if __name__ == "__main__":
    main()
