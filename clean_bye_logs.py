"""
清理数据库：删除包含"BYE后未重连"的对局
例外：如果BYE发生在最后一局，则保留（玩家提前离开是正常的）
"""
import sqlite3
import gzip
from xml.etree import ElementTree as ET
from typing import List, Tuple
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def should_keep_log(xml_content: str) -> Tuple[bool, str]:
    """
    判断是否应该保留该对局
    
    返回: (是否保留, 原因说明)
    """
    try:
        root = ET.fromstring(xml_content)
        all_elements = list(root)
        
        # 找到所有 INIT 标签的位置（每个 INIT 代表一个小局）
        init_indices = [i for i, elem in enumerate(all_elements) if elem.tag == 'INIT']
        
        if not init_indices:
            return True, "无INIT标签"
        
        last_init_index = init_indices[-1]
        
        # 检查每个 BYE 标签
        for elem_idx, elem in enumerate(all_elements):
            if elem.tag == 'BYE':
                who = elem.get('who', '?')
                
                # 检查是否在最后一局
                is_in_last_round = elem_idx > last_init_index
                
                if is_in_last_round:
                    # 在最后一局的 BYE，允许（玩家提前离开）
                    continue
                
                # 不在最后一局，检查是否有重连（UN 标签）
                found_reconnect = False
                
                # 查找下一个 INIT 的位置
                next_init_index = len(all_elements)
                for init_idx in init_indices:
                    if init_idx > elem_idx:
                        next_init_index = init_idx
                        break
                
                # 在当前小局内查找 UN 标签
                for i in range(elem_idx + 1, next_init_index):
                    if all_elements[i].tag == 'UN':
                        # 检查是否包含该玩家的信息
                        un_elem = all_elements[i]
                        if f'n{who}' in un_elem.attrib:
                            found_reconnect = True
                            break
                
                if not found_reconnect:
                    # BYE 不在最后一局，且没有重连 → 删除
                    return False, f"玩家{who}在非最后一局掉线且未重连"
        
        return True, "正常对局"
    
    except Exception as e:
        logger.error(f"解析XML失败: {e}")
        return True, f"解析错误，保留: {e}"


def clean_database(db_path: str, dry_run: bool = True):
    """
    清理数据库
    
    Args:
        db_path: 数据库路径
        dry_run: 如果为True，只统计不删除
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 获取总数
    cur.execute("SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ''")
    total_logs = cur.fetchone()[0]
    logger.info(f"数据库中共有 {total_logs:,} 条对局记录")
    
    # 统计
    to_keep = []
    to_delete = []
    processed = 0
    
    # 分批处理
    batch_size = 1000
    offset = 0
    
    while True:
        cur.execute("""
            SELECT id, log 
            FROM logs 
            WHERE log IS NOT NULL AND log != ''
            LIMIT ? OFFSET ?
        """, (batch_size, offset))
        
        batch = cur.fetchall()
        if not batch:
            break
        
        for log_id, log_data in batch:
            processed += 1
            
            if processed % 100 == 0:
                logger.info(f"已处理 {processed:,}/{total_logs:,} ({processed/total_logs*100:.1f}%)")
            
            try:
                xml_content = gzip.decompress(log_data).decode('utf-8')
                keep, reason = should_keep_log(xml_content)
                
                if keep:
                    to_keep.append((log_id, reason))
                else:
                    to_delete.append((log_id, reason))
            
            except Exception as e:
                logger.warning(f"处理对局 {log_id} 失败: {e}")
                to_keep.append((log_id, f"处理失败，保留"))
        
        offset += batch_size
    
    # 输出统计
    logger.info("="*80)
    logger.info(f"处理完成！")
    logger.info(f"  保留: {len(to_keep):,} 条 ({len(to_keep)/total_logs*100:.2f}%)")
    logger.info(f"  删除: {len(to_delete):,} 条 ({len(to_delete)/total_logs*100:.2f}%)")
    
    # 显示删除原因统计
    if to_delete:
        logger.info("\n删除原因统计:")
        reason_counts = {}
        for _, reason in to_delete:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        
        for reason, count in sorted(reason_counts.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  {reason}: {count:,} 条")
    
    # 执行删除
    if not dry_run and to_delete:
        logger.info("\n开始删除...")
        delete_ids = [log_id for log_id, _ in to_delete]
        
        # 分批删除
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
        logger.info(f"删除完成！共删除 {deleted:,} 条记录")
        
        # VACUUM 回收空间
        logger.info("\n正在压缩数据库...")
        cur.execute("VACUUM")
        logger.info("数据库压缩完成！")
    else:
        logger.info("\n[试运行模式] 未执行实际删除")
        logger.info("若要执行删除，请运行: py clean_bye_logs.py --execute")
    
    conn.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='清理包含未重连BYE的对局')
    parser.add_argument('--execute', action='store_true', 
                       help='实际执行删除（默认只是试运行）')
    parser.add_argument('--db', default='data/tenhou.db',
                       help='数据库路径（默认: data/tenhou.db）')
    
    args = parser.parse_args()
    
    logger.info("开始清理数据库...")
    logger.info(f"数据库: {args.db}")
    logger.info(f"模式: {'执行删除' if args.execute else '试运行（不会实际删除）'}")
    logger.info("="*80)
    
    clean_database(args.db, dry_run=not args.execute)
