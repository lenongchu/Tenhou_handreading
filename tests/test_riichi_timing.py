from src.tenhou6_adapter import _parse_round_from_tenhou6


def _base_game_data():
    return {
        "oya": 0,
        "honba": 0,
        "kyoku": 1,
        "bakaze": "E",
        "dora_marker": "1m",
        "tehais": [
            ["1m"] * 13,
            ["1p"] * 13,
            ["1s"] * 13,
            ["1z"] * 13,
        ],
    }


def test_self_riichi_does_not_count_as_opponent_riichi_at_match_timing():
    game_data = _base_game_data()
    events = [
        {"type": "riichi", "actor": 0},
        {"type": "dahai", "actor": 0, "pai": "3p", "tsumogiri": False},
    ]
    states = _parse_round_from_tenhou6(game_data, events)
    d = states[0].discards[0]
    assert d.riichi_happened is True
    assert d.opponent_riichi_happened is False


def test_opponent_riichi_counts_at_discard_match_timing():
    game_data = _base_game_data()
    events = [
        {"type": "riichi", "actor": 1},
        {"type": "dahai", "actor": 1, "pai": "3p", "tsumogiri": False},
        {"type": "dahai", "actor": 0, "pai": "4p", "tsumogiri": False},
    ]
    states = _parse_round_from_tenhou6(game_data, events)
    d = states[0].discards[0]
    assert d.riichi_happened is True
    assert d.opponent_riichi_happened is True
