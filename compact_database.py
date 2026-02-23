"""
压缩数据库：删除 game_states，收缩文件大小
使用 VACUUM 清理膨胀的数据库文件
"""
import sqlite3
import sys
import io
import os
import shutil
from datetime import datetime

# Windows编码修复
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

db_path = 'data/tenhou.db'
backup_path = f'data/tenhou_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'

print("="*70)
print("数据库压缩工具")
print("="*70)
print()

# 检查文件大小
db_size_gb = os.path.getsize(db_path) / (1024**3)
print(f"当前数据库大小: {db_size_gb:.2f} GB")
print()

print("步骤 1: 删除 game_states 和 visible_tile_stats 表...")
try:
    conn = sqlite3.connect(db_path, timeout=300)
    cur = conn.cursor()
    
    # 检查表是否存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('game_states', 'visible_tile_stats')")
    tables = [row[0] for row in cur.fetchall()]
    
    if tables:
        print(f"  找到表: {tables}")
        for table in tables:
            print(f"  正在删除 {table}...")
            cur.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
        print("  ✓ 表已删除")
    else:
        print("  ✓ 表不存在，跳过")
    
    conn.close()
except Exception as e:
    print(f"  ✗ 错误: {e}")
    sys.exit(1)

print()
print("步骤 2: 执行 VACUUM 压缩数据库...")
print("  （这可能需要几分钟，请耐心等待...）")
try:
    conn = sqlite3.connect(db_path, timeout=600)
    print("  正在执行 VACUUM...")
    conn.execute("VACUUM")
    conn.close()
    print("  ✓ VACUUM 完成")
except Exception as e:
    print(f"  ✗ 错误: {e}")
    sys.exit(1)

print()
print("步骤 3: 检查压缩后大小...")
new_size_gb = os.path.getsize(db_path) / (1024**3)
print(f"  压缩前: {db_size_gb:.2f} GB")
print(f"  压缩后: {new_size_gb:.2f} GB")
print(f"  节省: {db_size_gb - new_size_gb:.2f} GB ({(db_size_gb - new_size_gb) / db_size_gb * 100:.1f}%)")

print()
print("步骤 4: 重新创建 game_states 表...")
try:
    conn = sqlite3.connect(db_path, timeout=120)
    cur = conn.cursor()
    
    # 创建 game_states 表（带 UNIQUE 约束）
    cur.execute("""
    CREATE TABLE IF NOT EXISTS game_states (
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
    CREATE TABLE IF NOT EXISTS visible_tile_stats (
        state_id INTEGER NOT NULL,
        tile INTEGER NOT NULL,
        visible_count INTEGER NOT NULL,
        FOREIGN KEY(state_id) REFERENCES game_states(id),
        PRIMARY KEY(state_id, tile)
    )
    """)
    
    # 创建索引
    cur.execute("CREATE INDEX IF NOT EXISTS idx_log_player ON game_states(log_id, player_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_normalized_pattern ON game_states(normalized_pattern)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_turn ON game_states(turn)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tile_count ON visible_tile_stats(tile, visible_count)")
    
    conn.commit()
    conn.close()
    print("  ✓ 表和索引已创建")
except Exception as e:
    print(f"  ✗ 错误: {e}")
    sys.exit(1)

print()
print("="*70)
print("✓ 数据库压缩完成！")
print("="*70)
print()
print("现在可以运行:")
print("  echo yes | py process_all_logs_optimized.py")
