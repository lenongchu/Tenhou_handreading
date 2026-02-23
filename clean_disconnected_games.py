"""
清理数据库：删除包含 BYE 标签（掉线）的对局
"""
import sqlite3
import gzip
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def has_disconnect(xml_content: str) -> bool:
    """检查对局是否有玩家掉线"""
    return '<BYE' in xml_content

def clean_disconnected_games(db_path: str, dry_run: bool = True):
    """
    清理包含掉线的对局
    
    Args:
        db_path: 数据库路径
        dry_run: 如果为 True，只统计不删除
    """
    conn = sqlite3.connect(db_path, timeout=120)
    cur = conn.cursor()
    
    # 获取总对局数
    cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
    total_logs = cur.fetchone()[0]
    logger.info(f"数据库中有内容的对局总数: {total_logs:,}")
    
    # 检查每个对局
    cur.execute("SELECT id, log FROM logs WHERE log IS NOT NULL AND log != ''")
    
    disconnected_ids = []
    checked = 0
    
    logger.info("开始检查对局...")
    
    while True:
        # 批量读取
        batch = cur.fetchmany(1000)
        if not batch:
            break
        
        for log_id, xml_compressed in batch:
            try:
                # 解压
                if isinstance(xml_compressed, bytes):
                    xml_content = gzip.decompress(xml_compressed).decode('utf-8')
                else:
                    xml_content = xml_compressed
                
                # 检查是否有掉线
                if has_disconnect(xml_content):
                    disconnected_ids.append(log_id)
            
            except Exception as e:
                logger.error(f"检查对局 {log_id} 时出错: {e}")
            
            checked += 1
            if checked % 10000 == 0:
                logger.info(f"已检查: {checked:,}/{total_logs:,} ({checked/total_logs*100:.1f}%), 发现掉线: {len(disconnected_ids):,}")
    
    logger.info(f"检查完成！")
    logger.info(f"总对局数: {total_logs:,}")
    logger.info(f"有掉线的对局: {len(disconnected_ids):,} ({len(disconnected_ids)/total_logs*100:.2f}%)")
    logger.info(f"清理后剩余: {total_logs - len(disconnected_ids):,} ({(total_logs - len(disconnected_ids))/total_logs*100:.2f}%)")
    
    if dry_run:
        logger.info("\n[DRY RUN] 未执行删除操作")
        logger.info("如需真正删除，请运行: py clean_disconnected_games.py --execute")
    else:
        logger.info("\n开始删除掉线对局...")
        
        # 批量删除
        batch_size = 1000
        deleted = 0
        
        for i in range(0, len(disconnected_ids), batch_size):
            batch_ids = disconnected_ids[i:i+batch_size]
            placeholders = ','.join(['?'] * len(batch_ids))
            cur.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", batch_ids)
            deleted += len(batch_ids)
            
            if deleted % 10000 == 0:
                conn.commit()
                logger.info(f"已删除: {deleted:,}/{len(disconnected_ids):,}")
        
        conn.commit()
        logger.info(f"✓ 删除完成！共删除 {deleted:,} 场对局")
        
        # VACUUM 压缩数据库
        logger.info("正在压缩数据库（VACUUM）...")
        conn.execute("VACUUM")
        logger.info("✓ 数据库已压缩")
    
    conn.close()

if __name__ == "__main__":
    import sys
    
    db_path = 'data/tenhou.db'
    
    # 检查命令行参数
    execute = '--execute' in sys.argv
    
    if not execute:
        print("="*70)
        print("数据库清理工具 - 删除掉线对局")
        print("="*70)
        print()
        print("这将扫描所有对局并删除包含 BYE 标签（玩家掉线）的对局")
        print()
        print("首次运行将进行模拟统计（不删除数据）")
        print("如需真正删除，请添加 --execute 参数")
        print()
    
    clean_disconnected_games(db_path, dry_run=not execute)
