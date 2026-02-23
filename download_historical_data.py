"""
自动下载和导入 2020-2024 年历史数据

从天凤官网下载年度存档 ZIP 文件并导入到数据库
"""

import sys
import os
import requests
import subprocess
from pathlib import Path

# Windows 编码修复
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 配置
DB_PATH = "data/tenhou.db"
ARCHIVE_DIR = "data/archives"
BASE_URL = "https://tenhou.net/sc/raw/"

# 要下载的年份
YEARS = [2020, 2021, 2022, 2023, 2024]


def download_archive(year: int, archive_dir: str) -> str:
    """
    下载指定年份的存档文件
    
    Args:
        year: 年份
        archive_dir: 存档目录
        
    Returns:
        下载的文件路径
    """
    url = f"{BASE_URL}scraw{year}.zip"
    output_path = os.path.join(archive_dir, f"scraw{year}.zip")
    
    print(f"\n{'='*60}")
    print(f"下载 {year} 年数据")
    print(f"{'='*60}")
    print(f"URL: {url}")
    print(f"保存到: {output_path}")
    
    # 检查文件是否已存在
    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path) / (1024 * 1024)
        print(f"文件已存在 ({file_size:.2f} MB)，跳过下载")
        return output_path
    
    try:
        # 下载文件
        print("开始下载...")
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        print(f"文件大小: {total_size / (1024 * 1024):.2f} MB")
        
        # 写入文件并显示进度
        downloaded = 0
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # 显示进度
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        print(f"\r进度: {progress:.1f}% ({downloaded / (1024 * 1024):.2f} MB)", end='')
        
        print(f"\n[OK] 下载完成: {output_path}")
        return output_path
        
    except requests.exceptions.RequestException as e:
        print(f"\n[ERROR] 下载失败: {e}")
        
        # 检查文件是否存在（404 等）
        if hasattr(e, 'response') and e.response and e.response.status_code == 404:
            print(f"提示: {year} 年的数据可能不存在或URL已变更")
            print(f"请手动检查: {url}")
        
        raise


def import_archive(db_path: str, archive_path: str) -> None:
    """
    导入存档文件到数据库
    
    Args:
        db_path: 数据库路径
        archive_path: 存档文件路径
    """
    print(f"\n导入存档到数据库...")
    print(f"数据库: {db_path}")
    print(f"存档: {archive_path}")
    
    try:
        # 使用 houou-logs import 命令
        cmd = [
            sys.executable,
            "-m", "houou_logs",
            "import",
            db_path,
            archive_path
        ]
        
        print(f"执行: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        
        print(result.stdout)
        print("[OK] 导入完成")
        
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] 导入失败: {e.stderr}")
        raise


def main():
    """主函数"""
    print("="*60)
    print("天凤历史数据下载和导入工具")
    print("="*60)
    print(f"年份范围: {YEARS[0]}-{YEARS[-1]}")
    print(f"数据库: {DB_PATH}")
    print(f"存档目录: {ARCHIVE_DIR}")
    
    # 创建目录
    Path(ARCHIVE_DIR).mkdir(parents=True, exist_ok=True)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    
    # 显示注意事项
    print(f"\n注意事项:")
    print(f"1. 每年数据约 500MB-2GB")
    print(f"2. 总共需要下载 5-10GB 数据")
    print(f"3. 下载时间取决于网络速度")
    print(f"4. 需要足够的磁盘空间")
    print(f"\n自动开始下载（无需确认）...")
    
    # 处理每个年份
    success_count = 0
    failed_years = []
    
    for year in YEARS:
        try:
            # 下载存档
            archive_path = download_archive(year, ARCHIVE_DIR)
            
            # 导入到数据库
            import_archive(DB_PATH, archive_path)
            
            success_count += 1
            print(f"\n[OK] {year} 年数据处理完成")
            
        except Exception as e:
            print(f"\n[ERROR] {year} 年处理失败: {e}")
            failed_years.append(year)
            
            # 继续处理下一年（不询问）
            print(f"继续处理下一年...")
    
    # 总结
    print("\n" + "="*60)
    print("处理完成")
    print("="*60)
    print(f"成功: {success_count}/{len(YEARS)} 年")
    
    if failed_years:
        print(f"失败: {failed_years}")
    
    # 显示数据库统计
    if os.path.exists(DB_PATH):
        size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
        print(f"\n最终数据库大小: {size_mb:.2f} MB")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
