"""
数据库表结构迁移：为 game_states 表添加 UNIQUE 约束
执行后可防止重复插入同一对局状态
"""
import sqlite3
import logging
import sys
import io

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def migrate():
    db_path = 'data/tenhou.db'
    logger.info(f"开始迁移数据库: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 检查当前数据
    cur.execute("SELECT COUNT(*) FROM game_states")
    old_count = cur.fetchone()[0]
    logger.info(f"当前 game_states 表记录数: {old_count:,}")
    
    if old_count == 0:
        logger.info("表为空，直接重建表结构")
        cur.execute("DROP TABLE IF EXISTS game_states")
        cur.execute("DROP TABLE IF EXISTS visible_tile_stats")
        
        # 创建带 UNIQUE 约束的新表
        cur.execute("""
        CREATE TABLE game_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id TEXT NOT NULL,
            round_num INTEGER NOT NULL,
            honba INTEGER NOT NULL DEFAULT 0,
            player_id INTEGER NOT NULL,
            turn INTEGER NOT NULL,
            tile INTEGER NOT NULL,
            is_tsumogiri INTEGER NOT NULL,
            hand_tiles TEXT NOT NULL,
            visible_tiles TEXT NOT NULL,
            dora_indicators TEXT NOT NULL,
            normalized_pattern TEXT,
            UNIQUE(log_id, round_num, honba, player_id, turn),
            FOREIGN KEY(log_id) REFERENCES logs(id)
        )
        """)
        
        cur.execute("""
        CREATE TABLE visible_tile_stats (
            state_id INTEGER NOT NULL,
            tile INTEGER NOT NULL,
            visible_count INTEGER NOT NULL,
            FOREIGN KEY(state_id) REFERENCES game_states(id),
            PRIMARY KEY(state_id, tile)
        )
        """)
        
        # 重建索引
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_log_player ON game_states(log_id, player_id)
        """)
        
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_normalized_pattern ON game_states(normalized_pattern)
        """)
        
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_turn ON game_states(turn)
        """)
        
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_tile_count ON visible_tile_stats(tile, visible_count)
        """)
        
        conn.commit()
        logger.info("✓ 表结构已更新（添加 UNIQUE 约束）")
        
    else:
        logger.info("表中有数据，执行迁移...")
        
        # 备份旧表
        logger.info("Step 1: 备份旧表...")
        cur.execute("ALTER TABLE game_states RENAME TO game_states_old")
        cur.execute("ALTER TABLE visible_tile_stats RENAME TO visible_tile_stats_old")
        
        # 创建新表（带 UNIQUE 约束）
        logger.info("Step 2: 创建新表...")
        cur.execute("""
        CREATE TABLE game_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_id TEXT NOT NULL,
            round_num INTEGER NOT NULL,
            honba INTEGER NOT NULL DEFAULT 0,
            player_id INTEGER NOT NULL,
            turn INTEGER NOT NULL,
            tile INTEGER NOT NULL,
            is_tsumogiri INTEGER NOT NULL,
            hand_tiles TEXT NOT NULL,
            visible_tiles TEXT NOT NULL,
            dora_indicators TEXT NOT NULL,
            normalized_pattern TEXT,
            UNIQUE(log_id, round_num, honba, player_id, turn),
            FOREIGN KEY(log_id) REFERENCES logs(id)
        )
        """)
        
        cur.execute("""
        CREATE TABLE visible_tile_stats (
            state_id INTEGER NOT NULL,
            tile INTEGER NOT NULL,
            visible_count INTEGER NOT NULL,
            FOREIGN KEY(state_id) REFERENCES game_states(id),
            PRIMARY KEY(state_id, tile)
        )
        """)
        
        # 迁移数据（去重）
        logger.info("Step 3: 迁移数据（自动去重）...")
        cur.execute("""
        INSERT OR IGNORE INTO game_states 
        SELECT * FROM game_states_old
        """)
        
        cur.execute("SELECT COUNT(*) FROM game_states")
        new_count = cur.fetchone()[0]
        logger.info(f"迁移完成: {old_count:,} -> {new_count:,} 记录")
        
        if new_count < old_count:
            logger.info(f"✓ 已去除 {old_count - new_count:,} 条重复记录")
        
        # 迁移 visible_tile_stats（基于新的 state_id）
        logger.info("Step 4: 迁移可见牌统计...")
        cur.execute("""
        INSERT OR IGNORE INTO visible_tile_stats
        SELECT 
            gs.id as state_id,
            vts_old.tile,
            vts_old.visible_count
        FROM visible_tile_stats_old vts_old
        JOIN game_states_old gs_old ON vts_old.state_id = gs_old.id
        JOIN game_states gs ON (
            gs.log_id = gs_old.log_id AND
            gs.round_num = gs_old.round_num AND
            gs.honba = gs_old.honba AND
            gs.player_id = gs_old.player_id AND
            gs.turn = gs_old.turn
        )
        """)
        
        # 重建索引
        logger.info("Step 5: 重建索引...")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_log_player ON game_states(log_id, player_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_normalized_pattern ON game_states(normalized_pattern)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_turn ON game_states(turn)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tile_count ON visible_tile_stats(tile, visible_count)")
        
        # 删除旧表
        logger.info("Step 6: 清理旧表...")
        cur.execute("DROP TABLE game_states_old")
        cur.execute("DROP TABLE visible_tile_stats_old")
        
        conn.commit()
        logger.info("✓ 迁移完成")
    
    # 验证最终状态
    cur.execute("SELECT COUNT(*) FROM game_states")
    final_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT log_id) FROM game_states")
    unique_games = cur.fetchone()[0]
    
    logger.info(f"\n最终统计:")
    logger.info(f"  总状态数: {final_count:,}")
    logger.info(f"  唯一对局数: {unique_games:,}")
    
    conn.close()
    logger.info("\n✓ 数据库迁移成功！现在可以安全地多次运行处理脚本，不会产生重复数据。")

if __name__ == "__main__":
    print("="*70)
    print("数据库表结构迁移工具")
    print("功能: 为 game_states 表添加 UNIQUE 约束，防止重复插入")
    print("="*70)
    print()
    
    # 检查命令行参数：--auto 跳过确认
    import sys
    auto_mode = '--auto' in sys.argv
    
    if auto_mode or input("确认开始迁移？(yes/no): ").strip().lower() == 'yes':
        migrate()
    else:
        print("已取消")
