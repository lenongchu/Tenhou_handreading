"""
快速清空：DROP + CREATE 表（比 DELETE 快得多）
"""
import sqlite3
import sys
import io

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db_path = 'data/tenhou.db'

print("开始重建 game_states 表（快速清空方案）...")

try:
    conn = sqlite3.connect(db_path, timeout=120)
    cur = conn.cursor()
    
    # 获取清空前的记录数
    try:
        cur.execute("SELECT COUNT(*) FROM game_states")
        old_count = cur.fetchone()[0]
        print(f"当前 game_states 表记录数: {old_count:,}")
    except:
        old_count = 0
        print("game_states 表不存在或为空")
    
    print("\n正在删除旧表...")
    cur.execute("DROP TABLE IF EXISTS visible_tile_stats")
    cur.execute("DROP TABLE IF EXISTS game_states")
    print("✓ 旧表已删除")
    
    print("\n正在创建新表...")
    # 创建 game_states 表（带 UNIQUE 约束）
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
    
    # 创建 visible_tile_stats 表
    cur.execute("""
    CREATE TABLE visible_tile_stats (
        state_id INTEGER NOT NULL,
        tile INTEGER NOT NULL,
        visible_count INTEGER NOT NULL,
        FOREIGN KEY(state_id) REFERENCES game_states(id),
        PRIMARY KEY(state_id, tile)
    )
    """)
    
    print("✓ 新表已创建")
    
    print("\n正在创建索引...")
    cur.execute("CREATE INDEX idx_log_player ON game_states(log_id, player_id)")
    cur.execute("CREATE INDEX idx_normalized_pattern ON game_states(normalized_pattern)")
    cur.execute("CREATE INDEX idx_turn ON game_states(turn)")
    cur.execute("CREATE INDEX idx_tile_count ON visible_tile_stats(tile, visible_count)")
    print("✓ 索引已创建")
    
    conn.commit()
    
    # 验证
    cur.execute("SELECT COUNT(*) FROM game_states")
    new_count = cur.fetchone()[0]
    
    print(f"\n✓ 重建完成!")
    print(f"  记录数: {old_count:,} -> {new_count:,}")
    
    conn.close()
    print("\n✓ 数据库已准备就绪，可以重新处理对局数据")
    
except Exception as e:
    print(f"✗ 错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
