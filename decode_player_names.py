"""
解码 mjlog 中的 UN 标签（玩家信息）
"""
import urllib.parse

# 示例
encoded_name = "%74%6F%72%69%30%36%31%32"
decoded_name = urllib.parse.unquote(encoded_name)

print("URL 编码解码示例:")
print(f"  编码: {encoded_name}")
print(f"  解码: {decoded_name}")
print()

# 从实际文件中提取
import xml.etree.ElementTree as ET

xml_file = "sample_bye_log_3_2020010101gm_00a9_0000_6e71261a.xml"
tree = ET.parse(xml_file)
root = tree.getroot()

un_tag = root.find('.//UN')
if un_tag:
    print(f"文件: {xml_file}")
    print("玩家信息 (UN 标签):")
    print("="*70)
    
    for attr, value in un_tag.attrib.items():
        if attr.startswith('n'):  # 昵称 (n0, n1, n2, n3)
            player_num = attr[1:]
            decoded = urllib.parse.unquote(value)
            print(f"  玩家 {player_num}: {decoded}")
        elif attr == 'dan':  # 段位
            dans = value.split(',')
            print(f"\n  段位 (dan):")
            for i, d in enumerate(dans):
                print(f"    玩家 {i}: {d}")
        elif attr == 'rate':  # 等级分
            rates = value.split(',')
            print(f"\n  等级分 (rate):")
            for i, r in enumerate(rates):
                print(f"    玩家 {i}: {r}")
        elif attr == 'sx':  # 性别
            sexes = value.split(',')
            print(f"\n  性别 (sx):")
            for i, s in enumerate(sexes):
                print(f"    玩家 {i}: {s}")

print("\n" + "="*70)
print("标签含义:")
print("  n0-n3 = 玩家昵称（URL 编码）")
print("  dan   = 段位（0=新人, 1-20=级位/段位）")
print("  rate  = 等级分（Rating）")
print("  sx    = 性别（M=男, F=女, C=电脑）")
