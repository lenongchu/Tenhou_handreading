"""
Log 质量检查模块
用于在导入数据时自动过滤低质量的对局
"""
import gzip
from xml.etree import ElementTree as ET
from typing import Tuple, List
import logging

logger = logging.getLogger(__name__)


def check_log_quality(xml_content: str) -> Tuple[bool, str]:
    """
    检查对局质量，判断是否应该导入
    
    Args:
        xml_content: mjlog XML 内容（未压缩）
    
    Returns:
        (是否通过检查, 原因说明)
    
    拒绝理由:
        - BYE后未重连（除非在最后一局）
    """
    try:
        root = ET.fromstring(xml_content)
        all_elements = list(root)
        
        # 找到所有 INIT 标签的位置（每个 INIT 代表一个小局）
        init_indices = [i for i, elem in enumerate(all_elements) if elem.tag == 'INIT']
        
        if not init_indices:
            return True, "无INIT标签（空对局）"
        
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
                    # BYE 不在最后一局，且没有重连 → 拒绝
                    return False, f"玩家{who}在非最后一局掉线且未重连"
        
        return True, "通过"
    
    except Exception as e:
        logger.error(f"检查对局质量失败: {e}")
        # 解析错误时保守处理，拒绝该对局
        return False, f"解析错误: {str(e)}"


def check_log_quality_compressed(compressed_data: bytes) -> Tuple[bool, str]:
    """
    检查压缩后的对局质量
    
    Args:
        compressed_data: gzip 压缩的 mjlog XML 数据
    
    Returns:
        (是否通过检查, 原因说明)
    """
    try:
        xml_content = gzip.decompress(compressed_data).decode('utf-8')
        return check_log_quality(xml_content)
    except Exception as e:
        logger.error(f"解压对局数据失败: {e}")
        return False, f"解压错误: {str(e)}"


def batch_check_logs(log_data_list: List[Tuple[str, bytes]]) -> dict:
    """
    批量检查对局质量
    
    Args:
        log_data_list: [(log_id, compressed_data), ...]
    
    Returns:
        {
            'passed': [(log_id, reason), ...],
            'rejected': [(log_id, reason), ...]
        }
    """
    passed = []
    rejected = []
    
    for log_id, compressed_data in log_data_list:
        is_valid, reason = check_log_quality_compressed(compressed_data)
        
        if is_valid:
            passed.append((log_id, reason))
        else:
            rejected.append((log_id, reason))
    
    return {
        'passed': passed,
        'rejected': rejected
    }
