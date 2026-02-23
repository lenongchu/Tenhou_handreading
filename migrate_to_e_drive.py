"""
项目迁移工具 - 从OneDrive迁移到E盘

功能：
1. 将整个项目复制到E盘
2. 更新所有配置和路径
3. 验证迁移完整性
4. 可选：从OneDrive删除旧文件
"""
import shutil
import os
from pathlib import Path
import json
import sys

# 颜色输出
def print_info(msg):
    print(f"[INFO] {msg}")

def print_success(msg):
    print(f"[OK] {msg}")

def print_warning(msg):
    print(f"[WARNING] {msg}")

def print_error(msg):
    print(f"[ERROR] {msg}")

# 当前路径和目标路径
CURRENT_PATH = Path(__file__).parent.absolute()
TARGET_DRIVE = "E:"
TARGET_PATH = Path(TARGET_DRIVE) / "Tenhou_handreading"

print("="*80)
print("天凤读牌项目迁移工具")
print("="*80)

print(f"\n当前路径: {CURRENT_PATH}")
print(f"目标路径: {TARGET_PATH}")

# 检查当前项目大小
print("\n" + "="*80)
print("步骤 1: 检查项目大小")
print("="*80)

def get_dir_size(path):
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    except PermissionError:
        pass
    return total

current_size = get_dir_size(CURRENT_PATH)
print_info(f"当前项目大小: {current_size / (1024**3):.2f} GB")

# 检查E盘空间
drive_stat = shutil.disk_usage(TARGET_DRIVE)
free_space = drive_stat.free
print_info(f"E盘可用空间: {free_space / (1024**3):.2f} GB")

if free_space < current_size * 2:
    print_warning(f"E盘空间可能不足！建议至少保留 {current_size * 2 / (1024**3):.2f} GB 空间")
    response = input("\n是否继续？(yes/no): ").strip().lower()
    if response != 'yes':
        print_info("迁移已取消")
        sys.exit(0)

# 确认迁移
print("\n" + "="*80)
print("步骤 2: 确认迁移")
print("="*80)

print(f"\n将要执行的操作：")
print(f"  1. 复制整个项目到: {TARGET_PATH}")
print(f"  2. 保留OneDrive中的原始文件（手动删除）")
print(f"  3. 预计耗时: {current_size / (100 * 1024**2):.1f} 分钟（假设100MB/s）")

response = input("\n确认开始迁移？(yes/no): ").strip().lower()
if response != 'yes':
    print_info("迁移已取消")
    sys.exit(0)

# 开始迁移
print("\n" + "="*80)
print("步骤 3: 复制文件")
print("="*80)

if TARGET_PATH.exists():
    print_warning(f"目标路径已存在: {TARGET_PATH}")
    response = input("是否覆盖？(yes/no): ").strip().lower()
    if response != 'yes':
        print_info("迁移已取消")
        sys.exit(0)
    print_info("删除现有目录...")
    shutil.rmtree(TARGET_PATH)

print_info("开始复制文件...")
print_info("这可能需要几分钟，请耐心等待...")

try:
    shutil.copytree(CURRENT_PATH, TARGET_PATH, 
                    ignore=shutil.ignore_patterns('*.pyc', '__pycache__', '.git'))
    print_success(f"文件复制完成！")
except Exception as e:
    print_error(f"复制失败: {e}")
    sys.exit(1)

# 验证迁移
print("\n" + "="*80)
print("步骤 4: 验证迁移")
print("="*80)

target_size = get_dir_size(TARGET_PATH)
print_info(f"目标文件夹大小: {target_size / (1024**3):.2f} GB")

if abs(target_size - current_size) < 1024 * 1024:  # 差异小于1MB
    print_success("文件大小验证通过！")
else:
    print_warning(f"文件大小差异: {abs(target_size - current_size) / (1024**2):.2f} MB")

# 检查关键文件
key_files = [
    "data/tenhou.db",
    "src/database.py",
    "src/mjlog_parser.py",
    "src/simple_normalizer.py",
    "process_all_logs.py",
    "run.py"
]

print("\n检查关键文件:")
all_exist = True
for file in key_files:
    file_path = TARGET_PATH / file
    if file_path.exists():
        print_success(f"  ✓ {file}")
    else:
        print_error(f"  ✗ {file} - 缺失！")
        all_exist = False

if not all_exist:
    print_error("部分关键文件缺失，请检查！")
    sys.exit(1)

# 完成
print("\n" + "="*80)
print("迁移完成！")
print("="*80)

print(f"""
下一步操作：

1. 测试新路径的项目:
   cd {TARGET_PATH}
   py -3.12 check_db_status.py

2. 如果测试正常，可以从OneDrive删除旧文件:
   (手动操作，确保数据安全)

3. 更新你的IDE工作路径:
   - Cursor: File -> Open Folder -> {TARGET_PATH}
   - 或直接在新路径打开 Cursor

4. 继续后台下载（如果还在运行）:
   - 停止OneDrive路径中的下载
   - 在新路径运行: {TARGET_PATH}\\后台下载数据.bat

备注：
- 原始文件保留在OneDrive中，确保安全
- 建议测试完成后再删除旧文件
- 数据库文件: {TARGET_PATH / 'data' / 'tenhou.db'}
""")

print("\n" + "="*80)
print("[OK] 迁移工具执行完毕！")
print("="*80)
