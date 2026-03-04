# -*- coding: utf-8 -*-
"""Fix remaining mojibake - handle special chars."""
path = r"e:\Cursor\Tenhou_handreading\src\live_analyzer.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

# Fix potential U+E1D7 (PUA) or other special - replace any char in 鏌?\u00a0?寮 with 查
# Actually let's try: the docstring lines 2-5 might have 查 (U+67E5) encoded wrong
# Common pattern: 鏌\u200b?\u200c?\u200d?ヨ -> 查询 (查 U+67E5, 询 U+8BE2)
# 寮曟搸 -> 引擎

import re
# Replace any occurrence of the mojibake docstring by matching flexible
# Match optional PUA/special chars (U+E000-U+F8FF) and ZWJ/ZWNJ
replacements = [
    (r"瀹炴椂鏌ヨ[\u200b-\u200d\ufeff\ue000-\uf8ff]?寮曟搸", "实时查询引擎"),
    (r"鐩存帴浠\?logs 琛ㄨ[\u200b-\u200d\ufeff\ue000-\uf8ff]?鍙栧畬鏁寸墝璋辨暟鎹[\u200b-\u200d\ufeff\ue000-\uf8ff]?苟瀹炴椂", "直接从 logs 表读取完整牌谱数据库并实时"),
    (r"鏀[\u200b-\u200d\ufeff\ue000-\uf8ff]?寔鍓[\u200b-\u200d\ufeff\ue000-\uf8ff]?湶绛夊畬鏁翠俊鎭[\u200b-\u200d\ufeff\ue000-\uf8ff]?\?", "支持副露等完整信息。"),
]

for pat, repl in replacements:
    text = re.sub(pat, repl, text)

# Simple string replacements for remaining - use regex to handle invisible/special chars
text = re.sub(r"鏀[\u200b-\u200d\ufeff\ue000-\uf8ff]?寔鍓[\u200b-\u200d\ufeff\ue000-\uf8ff]?湶绛夊畬鏁翠俊鎭[\u200b-\u200d\ufeff\ue000-\uf8ff]?\??", "支持副露等完整信息。", text)
text = text.replace("鏀\u200b寔鍓\u200b湶绛夊畬鏁翠俊鎭\u20ac?", "支持副露等完整信息。")
text = text.replace("每批浠庢暟鎹簱读取鐨勫灞€鏁般€傝秺澶у垯 SQL 次数越少、略快紝浣嗗崟鎵瑰唴瀛樼嚎鎬у鍔狅紙绾?1.2GB/1000 鏉★紝5000 条约 6GB锛?", "每批从数据库读取的对局数。越大则 SQL 次数越少、略快，但单批内存线性增加（约 1.2GB/1000 条，5000 条约 6GB）")
text = text.replace("主统计时最澶氫繚鐣欑殑匹配状态条数（仅用于返回给界面锛岃秴鍑洪儴鍒嗕笉淇濈暀锛岄伩鍏嶅唴瀛樻寔缁闀匡級", "主统计时最多保留的匹配状态条数（仅用于返回给界面，超出部分不保留，避免内存持续增长）")
text = text.replace("并行分析鏃讹紝姣忓处理多少批鍚庨噸鍚?worker 池，浠ラ释放惧瓙进程鍐?Python 持有鐨勫内存", "并行分析时，每处理多少批后重启 worker 池，以释放子进程内 Python 持有的内存")

with open(path, "w", encoding="utf-8", newline="") as f:
    f.write(text)
print("Fixed with regex.")
