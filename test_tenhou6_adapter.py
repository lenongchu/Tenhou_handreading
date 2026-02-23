"""
测试 tenhou6 适配器
使用 tenhou-paifu-to-json 的 temp.json 格式样本验证解析正确性
"""
import sys
import json

# 最小 tenhou6 样本：一局东1局，4 玩家各 13 张初始手牌，若干摸打
SAMPLE_TENHOU6 = {
    "type": 169,
    "games": [
        {
            "data": {
                "bakaze": "E",
                "dora_marker": "9p",
                "honba": 0,
                "kyoku": 1,
                "oya": 0,
                "scores": [25000, 25000, 25000, 25000],
                "tehais": [
                    ["1m", "2m", "3m", "4p", "5p", "6p", "1s", "2s", "3s", "1z", "2z", "3z", "4z"],
                    ["5m", "6m", "7m", "7p", "8p", "9p", "4s", "5s", "6s", "5z", "6z", "7z", "1z"],
                    ["1m", "4m", "7m", "1p", "4p", "7p", "1s", "4s", "7s", "东", "南", "西", "北"],
                    ["2m", "5m", "8m", "2p", "5p", "8p", "2s", "5s", "8s", "白", "发", "中", "9m"],
                ],
            },
            "game": [
                {"actor": 0, "pai": "4m", "type": "tsumo"},
                {"actor": 0, "pai": "4z", "type": "dahai", "tsumogiri": False},
                {"actor": 1, "pai": "2z", "type": "tsumo"},
                {"actor": 1, "pai": "2z", "type": "dahai", "tsumogiri": True},
                {"actor": 2, "pai": "2m", "type": "tsumo"},
                {"actor": 2, "pai": "3z", "type": "dahai", "tsumogiri": False},
                {"actor": 3, "pai": "3m", "type": "tsumo"},
                {"actor": 3, "pai": "3m", "type": "dahai", "tsumogiri": True},
            ],
        }
    ],
}


def main():
    # 绕过 gui_app 依赖，手动构建 src 子模块
    import importlib.util
    # 创建空的 src 包（不执行 __init__.py）
    if "src" not in sys.modules:
        import types
        sys.modules["src"] = types.ModuleType("src")
    # 加载 mjlog_parser
    spec_m = importlib.util.spec_from_file_location("src.mjlog_parser", "src/mjlog_parser.py")
    mod_m = importlib.util.module_from_spec(spec_m)
    sys.modules["src.mjlog_parser"] = mod_m
    spec_m.loader.exec_module(mod_m)
    MjlogParser = mod_m.MjlogParser
    # 加载 tenhou6_adapter（依赖 .mjlog_parser）
    spec_t = importlib.util.spec_from_file_location("src.tenhou6_adapter", "src/tenhou6_adapter.py")
    mod_t = importlib.util.module_from_spec(spec_t)
    mod_t.__package__ = "src"
    sys.modules["src.tenhou6_adapter"] = mod_t
    spec_t.loader.exec_module(mod_t)
    parse_tenhou6_json = mod_t.parse_tenhou6_json

    states = parse_tenhou6_json(SAMPLE_TENHOU6)
    print(f"解析得到 {len(states)} 个 GameState (应为 4)")
    assert len(states) == 4

    # 玩家 0：舍牌应为 4z
    p0 = states[0]
    print(f"玩家 0 舍牌数: {len(p0.discards)}")
    if p0.discards:
        d = p0.discards[0]
        s = MjlogParser.tile_to_string(d.tile)
        print(f"  第 1 张: {s}, 摸切={d.is_tsumogiri}")
        assert s in ("北", "4z") or "4" in s  # 4z = 北
    print("[OK] tenhou6 adapter basic parsing works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
