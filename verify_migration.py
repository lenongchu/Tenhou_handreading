"""
迁移后验证脚本
验证E盘项目是否正常工作
"""
import sqlite3
import sys
import os
from pathlib import Path

print("="*70)
print("迁移后验证测试")
print("="*70)

# 检查当前路径
current_path = Path.cwd()
print(f"\n当前工作目录: {current_path}")
print(f"盘符: {current_path.drive}")

# 验证1: 检查数据库文件
print("\n" + "="*70)
print("验证 1: 数据库文件")
print("="*70)

db_path = Path('data/tenhou.db')
if db_path.exists():
    size_gb = db_path.stat().st_size / (1024**3)
    print(f"[OK] 数据库文件存在: {db_path.absolute()}")
    print(f"[OK] 数据库大小: {size_gb:.2f} GB")
else:
    print(f"[ERROR] 数据库文件不存在！")
    sys.exit(1)

# 验证2: 数据库连接和查询
print("\n" + "="*70)
print("验证 2: 数据库连接")
print("="*70)

try:
    conn = sqlite3.connect('data/tenhou.db')
    cur = conn.cursor()
    
    # 检查表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cur.fetchall()]
    print(f"[OK] 数据库表: {len(tables)} 个")
    print(f"     {', '.join(tables)}")
    
    # 检查对局数
    cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL")
    log_count = cur.fetchone()[0]
    print(f"[OK] 已下载对局数: {log_count:,}")
    
    # 检查game_states
    if 'game_states' in tables:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT log_id) FROM game_states")
        state_count, unique_games = cur.fetchone()
        print(f"[OK] 游戏状态数: {state_count:,}")
        print(f"[OK] 已处理对局数: {unique_games:,}")
    
    conn.close()
    print(f"\n[OK] 数据库连接正常！")
    
except Exception as e:
    print(f"[ERROR] 数据库连接失败: {e}")
    sys.exit(1)

# 验证3: Python模块导入
print("\n" + "="*70)
print("验证 3: Python模块")
print("="*70)

# 添加src到路径
src_path = current_path / 'src'
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

try:
    import database
    print("[OK] database 模块导入成功")
    
    import mjlog_parser
    print("[OK] mjlog_parser 模块导入成功")
    
    import simple_normalizer
    print("[OK] simple_normalizer 模块导入成功")
    
    # 测试标准化
    test_result = simple_normalizer.normalize_discard_pattern(["1m", "2m"])
    print(f"[OK] 标准化测试: ['1m', '2m'] -> {test_result}")
    
except Exception as e:
    print(f"[ERROR] 模块导入失败: {e}")
    sys.exit(1)

# 验证4: 关键文件存在
print("\n" + "="*70)
print("验证 4: 关键文件")
print("="*70)

key_files = [
    "src/database.py",
    "src/mjlog_parser.py",
    "src/simple_normalizer.py",
    "process_all_logs.py",
    "process_logs_sample.py",
    "后台下载数据.bat",
    "run.py"
]

all_exist = True
for file in key_files:
    if Path(file).exists():
        print(f"[OK] {file}")
    else:
        print(f"[ERROR] {file} - 缺失！")
        all_exist = False

# 最终结论
print("\n" + "="*70)
if all_exist and current_path.drive.upper() == 'E:':
    print("[OK] 迁移验证通过！")
    print("="*70)
    print("\n项目已成功迁移到E盘，可以正常使用。")
    print("\n建议：")
    print("  1. 运行 check_db_status.py 查看详细统计")
    print("  2. 如需继续下载，运行 后台下载数据.bat")
    print("  3. 确认无误后，可删除OneDrive中的旧文件")
elif not all_exist:
    print("[ERROR] 验证失败 - 缺少关键文件")
    print("="*70)
else:
    print(f"[WARNING] 当前盘符: {current_path.drive}")
    print("="*70)
    print("\n请确保在E盘路径下运行此脚本")
