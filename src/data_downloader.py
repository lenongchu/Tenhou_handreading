"""
数据下载模块

封装 houou-logs 工具，下载和更新天凤凤凰桌牌谱数据
"""

import subprocess
import logging
import sys
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def _convert_new_logs_to_tenhou6(db_path: str) -> None:
    """下载完成后，将新写入的 XML 转为 tenhou6 JSON（仅处理尚无 log_json 的记录）"""
    try:
        import sys
        root = Path(__file__).parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from convert_xml_to_tenhou6 import convert_new_logs_to_tenhou6
        convert_new_logs_to_tenhou6(db_path, skip_existing=True)
    except Exception as e:
        logger.debug(f"tenhou6 转换跳过: {e}")

# 年份范围常量
MIN_YEAR = 2015
MAX_YEAR = datetime.now().year


class DataDownloader:
    """数据下载器，封装 houou-logs 工具"""
    
    def __init__(self, db_path: str):
        """
        初始化数据下载器
        
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
    def _run_houou_logs(self, args: list) -> str:
        """
        运行 houou-logs 命令
        
        Args:
            args: 命令参数列表
            
        Returns:
            命令输出
        """
        # 使用 Python 模块方式调用 houou-logs
        cmd = [sys.executable, "-m", "houou_logs"] + args
        
        logger.debug(f"执行命令: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True
        )
        
        return result.stdout
        
    def download_archive_logs(self, start_year: int, end_year: Optional[int] = None) -> None:
        """
        下载指定年份范围的牌谱数据
        
        Args:
            start_year: 开始年份（2015-至今）
            end_year: 结束年份（可选，默认为当前年份）
        """
        if end_year is None:
            end_year = MAX_YEAR
            
        # 验证年份范围
        if start_year < MIN_YEAR or start_year > MAX_YEAR:
            raise ValueError(f"开始年份必须在 {MIN_YEAR}-{MAX_YEAR} 之间")
        if end_year < start_year or end_year > MAX_YEAR:
            raise ValueError(f"结束年份必须在 {start_year}-{MAX_YEAR} 之间")
        
        logger.info(f"开始下载 {start_year}-{end_year} 年的牌谱数据...")
        
        try:
            for year in range(start_year, end_year + 1):
                logger.info(f"正在下载 {year} 年数据...")
                
                # Step 1: Fetch log IDs (获取log ID列表)
                logger.info(f"  - 获取 {year} 年的 log ID 列表...")
                self._run_houou_logs([
                    "fetch",
                    "--archive",  # 获取历史数据
                    str(self.db_path)
                ])
                
                # Step 2: Download mjlog contents (下载实际内容)
                logger.info(f"  - 下载 {year} 年的 mjlog 内容...")
                self._run_houou_logs([
                    "download",
                    str(self.db_path),
                    "--players", "4",  # 仅四人麻
                ])
                
                logger.info(f"✓ {year} 年数据下载完成")
                _convert_new_logs_to_tenhou6(str(self.db_path))

            logger.info(f"所有年份数据下载完成！")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"下载失败: {e.stderr}")
            raise
        except FileNotFoundError as e:
            logger.error(f"未找到 houou-logs 工具，请确认已安装: {e}")
            raise
        except Exception as e:
            logger.error(f"下载过程中发生错误: {e}")
            raise
    
    def update_latest_logs(self, limit: Optional[int] = None) -> None:
        """
        获取并下载最新的牌谱数据
        
        Args:
            limit: 下载数量限制（可选）
        """
        logger.info("开始获取最新牌谱数据...")
        
        try:
            # Step 1: Fetch latest log IDs
            logger.info("获取最新 log ID 列表...")
            self._run_houou_logs([
                "fetch",
                str(self.db_path)
            ])
            
            # Step 2: Download mjlog contents
            logger.info("下载最新 mjlog 内容...")
            args = [
                "download",
                str(self.db_path),
                "--players", "4",
            ]
            
            if limit:
                args.extend(["--limit", str(limit)])
            
            self._run_houou_logs(args)
            _convert_new_logs_to_tenhou6(str(self.db_path))
            logger.info("✓ 最新数据更新完成")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"更新数据失败: {e.stderr}")
            raise
        except Exception as e:
            logger.error(f"更新过程中发生错误: {e}")
            raise
    
    def download_mjlog_contents(
        self, 
        players: int = 4, 
        length: Optional[str] = None, 
        limit: Optional[int] = None
    ) -> None:
        """
        下载 mjlog 内容（在已有 log ID 的情况下）
        
        Args:
            players: 玩家数量（默认4人麻）
            length: 对局长度（"t" 东风战, "h" 半庄战）
            limit: 下载数量限制
        """
        logger.info("开始下载 mjlog 内容...")
        
        try:
            args = [
                "download",
                str(self.db_path),
                "--players", str(players),
            ]
            
            if length:
                args.extend(["--length", length])
            
            if limit:
                args.extend(["--limit", str(limit)])
            
            self._run_houou_logs(args)
            _convert_new_logs_to_tenhou6(str(self.db_path))
            logger.info("✓ mjlog 内容下载完成")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"下载 mjlog 内容失败: {e.stderr}")
            raise
        except Exception as e:
            logger.error(f"下载过程中发生错误: {e}")
            raise
    
    def get_database_info(self) -> dict:
        """
        获取数据库统计信息
        
        Returns:
            包含对局数量、最新更新时间等信息的字典
        """
        # TODO: 实现数据库信息查询
        # 需要查询数据库获取统计信息
        return {
            "total_games": 0,
            "last_update": None,
        }


def download_archive_logs(start_year: int, end_year: Optional[int], db_path: str) -> None:
    """
    便捷函数：下载指定年份范围的牌谱存档
    
    Args:
        start_year: 开始年份
        end_year: 结束年份（可选）
        db_path: 数据库路径
    """
    downloader = DataDownloader(db_path)
    downloader.download_archive_logs(start_year, end_year)


def update_latest_logs(db_path: str, limit: Optional[int] = None) -> None:
    """
    便捷函数：更新最新牌谱数据
    
    Args:
        db_path: 数据库路径
        limit: 下载数量限制（可选）
    """
    downloader = DataDownloader(db_path)
    downloader.update_latest_logs(limit)


def download_mjlog_contents(
    db_path: str, 
    players: int = 4, 
    length: Optional[str] = None, 
    limit: Optional[int] = None
) -> None:
    """
    便捷函数：下载 mjlog 内容
    
    Args:
        db_path: 数据库路径
        players: 玩家数量
        length: 对局长度
        limit: 下载数量限制
    """
    downloader = DataDownloader(db_path)
    downloader.download_mjlog_contents(players, length, limit)


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    db_path = "../data/tenhou.db"
    downloader = DataDownloader(db_path)
    
    # 下载2020-2021年数据
    # downloader.download_archive_logs(2020, 2021)
    
    # 更新最新数据（限制100条）
    # downloader.update_latest_logs(limit=100)
