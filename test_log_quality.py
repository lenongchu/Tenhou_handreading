"""
测试数据质量检查功能
"""
from src.log_quality import check_log_quality
import gzip


def test_log_quality():
    """测试对局质量检查"""
    
    # 测试案例1：正常对局（无BYE标签）
    normal_log = """<?xml version="1.0"?>
<mjloggm ver="2.3">
<INIT seed="0,0,0,0,0,0" ten="250,250,250,250" oya="0" hai0="1,2,3,4,5,6,7,8,9,10,11,12,13"/>
<T14/>
<D14/>
<AGARI/>
</mjloggm>
"""
    
    is_valid, reason = check_log_quality(normal_log)
    assert is_valid, f"正常对局应该通过检查，但得到: {reason}"
    print("[PASS] 测试1通过：正常对局")
    
    # 测试案例2：最后一局掉线（应该保留）
    last_round_bye = """<?xml version="1.0"?>
<mjloggm ver="2.3">
<INIT seed="0,0,0,0,0,0" ten="250,250,250,250" oya="0"/>
<T14/>
<D14/>
<AGARI/>
<INIT seed="1,0,0,0,0,0" ten="250,250,250,250" oya="1"/>
<BYE who="2"/>
<T15/>
<D15/>
<AGARI/>
</mjloggm>
"""
    
    is_valid, reason = check_log_quality(last_round_bye)
    assert is_valid, f"最后一局掉线应该保留，但得到: {reason}"
    print("[PASS] 测试2通过：最后一局掉线（保留）")
    
    # 测试案例3：非最后一局掉线且未重连（应该删除）
    early_bye_no_reconnect = """<?xml version="1.0"?>
<mjloggm ver="2.3">
<INIT seed="0,0,0,0,0,0" ten="250,250,250,250" oya="0"/>
<BYE who="2"/>
<T14/>
<D14/>
<AGARI/>
<INIT seed="1,0,0,0,0,0" ten="250,250,250,250" oya="1"/>
<T15/>
<D15/>
<AGARI/>
</mjloggm>
"""
    
    is_valid, reason = check_log_quality(early_bye_no_reconnect)
    assert not is_valid, f"非最后一局掉线且未重连应该删除，但得到: {reason}"
    assert "玩家2在非最后一局掉线且未重连" in reason
    print("[PASS] 测试3通过：非最后一局掉线且未重连（删除）")
    
    # 测试案例4：掉线后重连（应该保留）
    bye_with_reconnect = """<?xml version="1.0"?>
<mjloggm ver="2.3">
<INIT seed="0,0,0,0,0,0" ten="250,250,250,250" oya="0"/>
<BYE who="2"/>
<T14/>
<D14/>
<UN n2="player2"/>
<T15/>
<D15/>
<AGARI/>
<INIT seed="1,0,0,0,0,0" ten="250,250,250,250" oya="1"/>
<T16/>
<D16/>
<AGARI/>
</mjloggm>
"""
    
    is_valid, reason = check_log_quality(bye_with_reconnect)
    assert is_valid, f"掉线后重连应该保留，但得到: {reason}"
    print("[PASS] 测试4通过：掉线后重连（保留）")
    
    print("\n所有测试通过！")


if __name__ == '__main__':
    test_log_quality()
