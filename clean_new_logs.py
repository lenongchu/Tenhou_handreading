"""
数据下载后自动清理低质量对局
在导入新数据后运行此脚本
"""
import sqlite3
import logging
from src.log_quality import check_log_quality_compressed

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def clean_new_logs(db_path: str = 'data/tenhou.db'):
    """
    清理数据库中所有未检查的对局
    只删除新导入的低质量对局
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 添加一个标记列来跟踪已检查的对局（如果不存在）
    try:
        cur.execute("ALTER TABLE logs ADD COLUMN quality_checked INTEGER DEFAULT 0")
        conn.commit()
        logger.info("添加 quality_checked 列")
    except sqlite3.OperationalError:
        # 列已存在
        pass
    
    # 获取未检查的对局数量
    cur.execute("SELECT COUNT(*) FROM logs WHERE quality_checked = 0 AND log IS NOT NULL AND log != ''")
    unchecked_count = cur.fetchone()[0]
    
    if unchecked_count == 0:
        logger.info("没有需要检查的新对局")
        conn.close()
        return
    
    logger.info(f"发现 {unchecked_count:,} 条未检查的新对局")
    
    # 分批处理
    batch_size = 1000
    offset = 0
    to_delete = []
    checked = 0
    
    while True:
        cur.execute("""
            SELECT id, log 
            FROM logs 
            WHERE quality_checked = 0 AND log IS NOT NULL AND log != ''
            LIMIT ? OFFSET ?
        """, (batch_size, offset))
        
        batch = cur.fetchall()
        if not batch:
            break
        
        for log_id, log_data in batch:
            checked += 1
            
            if checked % 100 == 0:
                logger.info(f"已检查 {checked:,}/{unchecked_count:,} ({checked/unchecked_count*100:.1f}%)")
            
            # 检查质量
            is_valid, reason = check_log_quality_compressed(log_data)
            
            if is_valid:
                # 标记为已检查且合格
                cur.execute("UPDATE logs SET quality_checked = 1 WHERE id = ?", (log_id,))
            else:
                # 标记为待删除
                to_delete.append((log_id, reason))
        
        offset += batch_size
        
        # 定期提交
        if checked % 5000 == 0:
            conn.commit()
    
    # 最终提交
    conn.commit()
    
    logger.info(f"检查完成！")
    logger.info(f"  合格: {checked - len(to_delete):,} 条 ({(checked - len(to_delete))/checked*100:.2f}%)")
    logger.info(f"  不合格: {len(to_delete):,} 条 ({len(to_delete)/checked*100:.2f}%)")
    
    # 删除不合格的对局
    if to_delete:
        logger.info(f"开始删除 {len(to_delete):,} 条低质量对局...")
        
        # 统计删除原因
        reason_counts = {}
        for _, reason in to_delete:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        
        logger.info("删除原因统计:")
        for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  {reason}: {count:,} 条")
        
        # 分批删除
        delete_ids = [log_id for log_id, _ in to_delete]
        batch_size = 500
        deleted = 0
        
        for i in range(0, len(delete_ids), batch_size):
            batch_ids = delete_ids[i:i+batch_size]
            placeholders = ','.join('?' * len(batch_ids))
            cur.execute(f"DELETE FROM logs WHERE id IN ({placeholders})", batch_ids)
            deleted += len(batch_ids)
            
            if deleted % 5000 == 0:
                logger.info(f"  已删除 {deleted:,}/{len(delete_ids):,}")
                conn.commit()
        
        conn.commit()
        logger.info(f"删除完成！共删除 {deleted:,} 条记录")
        
        # 压缩数据库
        logger.info("正在压缩数据库...")
        cur.execute("VACUUM")
        logger.info("数据库压缩完成！")
    
    conn.close()
    logger.info("清理完成！")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='清理新导入的低质量对局')
    parser.add_argument('--db', default='data/tenhou.db',
                       help='数据库路径（默认: data/tenhou.db）')
    
    args = parser.parse_args()
    
    logger.info("开始清理新导入的对局...")
    logger.info(f"数据库: {args.db}")
    logger.info("="*80)
    
    clean_new_logs(args.db)
