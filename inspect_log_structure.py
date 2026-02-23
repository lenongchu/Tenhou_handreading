"""检查对局 XML 结构，判断是否正常结束"""
import gzip
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    db_path = Path(__file__).parent / "data" / "tenhou.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 之前失败的两个案例
    log_ids = [
        "2020011611gm-00a9-0000-fbe44a74",
        "2020030121gm-00e1-0000-b2b63cf1",
    ]

    for log_id in log_ids:
        cur.execute("SELECT log FROM logs WHERE id = ?", (log_id,))
        row = cur.fetchone()
        if not row:
            print(f"\n[{log_id}] 未找到")
            continue

        blob = row[0]
        try:
            xml_str = gzip.decompress(blob).decode("utf-8")
        except Exception:
            xml_str = blob.decode("utf-8", errors="replace")

        root = ET.fromstring(xml_str)
        tags = [c.tag for c in root]

        print(f"\n{'='*60}")
        print(f"log_id: {log_id}")
        print(f"{'='*60}")
        print(f"根元素子标签数量: {len(tags)}")

        # 统计关键标签
        init_count = tags.count("INIT")
        agari_count = tags.count("AGARI")
        ryuukyoku_count = tags.count("RYUUKYOKU")
        bye_count = tags.count("BYE")
        print(f"  INIT(局数): {init_count}")
        print(f"  AGARI(和牌): {agari_count}")
        print(f"  RYUUKYOKU(流局): {ryuukyoku_count}")
        print(f"  BYE(掉线): {bye_count}")

        # 最后 15 个标签
        print(f"\n最后 15 个标签:")
        for t in tags[-15:]:
            print(f"  {t}")

        # 检查最后一个 AGARI 或 RYUUKYOKU 是否有 owari
        last_agari = last_ryuu = None
        for c in root:
            if c.tag == "AGARI":
                last_agari = c
            elif c.tag == "RYUUKYOKU":
                last_ryuu = c

        last_end = last_agari or last_ryuu
        if last_end is not None:
            owari = last_end.attrib.get("owari", "(无)")
            print(f"\n最后一个结束事件 ({last_end.tag}):")
            print(f"  owari: {owari}")
        else:
            print("\n未找到 AGARI 或 RYUUKYOKU")

        # 判断
        if bye_count > 0:
            print("\n结论: 含 BYE 标签 → 对局中存在掉线，非正常结束")
        elif init_count < 8:
            print("\n结论: 局数偏少，可能为中断对局")
        elif last_end is None:
            print("\n结论: 无和牌/流局事件，对局未正常结束")
        elif "owari" not in (last_end.attrib if last_end is not None else {}):
            print("\n结论: 最后一局无 owari → 可能是异常中断或特殊格式")
        else:
            print("\n结论: 结构完整，含 owari")

    conn.close()


if __name__ == "__main__":
    main()
