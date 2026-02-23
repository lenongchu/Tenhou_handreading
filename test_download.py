"""
测试数据下载功能

使用少量数据测试 houou-logs 集成
"""

import logging
import sys
from src.data_downloader import DataDownloader

# 配置日志和输出编码
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 设置标准输出编码为UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logger = logging.getLogger(__name__)

def test_download():
    """测试数据下载功能"""
    
    db_path = "data/test_download.db"
    downloader = DataDownloader(db_path)
    
    print("\n" + "="*60)
    print("测试数据下载功能")
    print("="*60 + "\n")
    
    try:
        # 测试1：获取最新数据（限制10条）
        print("[测试1] 获取最新数据（限制10条）...")
        downloader.update_latest_logs(limit=10)
        print("SUCCESS: 测试1通过\n")
        
        # 测试2：下载单年数据（2023年，如果要测试历史数据）
        # print("[测试2] 下载2023年数据...")
        # downloader.download_archive_logs(2023, 2023)
        # print("SUCCESS: 测试2通过\n")
        
        print("="*60)
        print("所有测试通过！")
        print("="*60)
        
    except Exception as e:
        print(f"\nERROR: 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_download()
