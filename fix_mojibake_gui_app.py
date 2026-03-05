# -*- coding: utf-8 -*-
"""修正 gui_app.py 中的乱码"""
import re
with open("src/gui_app.py", "r", encoding="utf-8") as f:
    content = f.read()

# 移除私有区字符 (U+E000-U+F8FF)，多为 mojibake
content = re.sub(r'[\ue000-\uf8ff]', '', content)

# 全角标点 -> 半角（避免 parser 对某些 Unicode 的异常）
content = content.replace("\uff0c", ",")
content = content.replace("\u3002", ".")
content = content.replace("\u20ac", ",")  # Euro 常为 "选" 等字的 mojibake
content = content.replace("\ue100", "")   # 私有区字符
# 私有区字符 PUA 常为 mojibake，批量替换常见
for old, new in [("\ue632", "复"), ("\ue047", "剪"), ("\ue21c", "可"), ("\ue15c", "中"),
                 ("\ue1bd", "目"), ("\ue1e9", "说")]:
    content = content.replace(old, new)
content = content.replace("锝", "-")      # 范围符
content = content.replace("銆?", "</")    # 损坏的 </ 标签
# 双prime(″) 常被误解析为模式中的"式"
content = content.replace("妯″紡", "模式")
# 常见 mojibake 替换
replacements = [
    ("鑸嶇墝妯″紡", "舍牌模式"),
    ("鑸嶇墝", "舍牌"),
    ("瀛樻。缁撴灉鏌ョ湅瀵硅瘽妗嗭紝鐐瑰嚮灞曞紑鏄剧ず鍏蜂綋缁撴灉", "存档结果查看对话框, 点击展开显示具体结果"),
    ("鏍锋湰灞曠ず瀵硅瘽妗?", "样本展示对话框"),
    ("杈撳叆璇存槑", "输入说明"),
    # PATTERN_HELP_HTML 段落
    ("鐢?<b>-</b> 鎴?<b>AND</b> 鍒嗛殧鍚勫紶鐗岋紝鎸夊嚭鐗岄『搴忎粠宸﹀埌鍙充功鍐欍€?", "用 <b>-</b> 或 <b>AND</b> 分隔各张牌, 按出牌顺序从左到右书写."),
    ("鎸夊嚭鐗岄『搴忎粠宸﹀埌鍙充功鍐欍€?", "按出牌顺序从左到右书写."),
    # QMessageBox 复制成功
    ('"宸插鍒?, "鍒嗘瀽缁撴灉宸插鍒跺埌鍓创鏉匡紝鍙洿鎺ョ矘璐村埌 Excel 涓,?)', '"已复制", "分析结果已复制到剪贴板，可直接粘贴到 Excel 中")'),
]
for old, new in replacements:
    content = content.replace(old, new)

with open("src/gui_app.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Done")
