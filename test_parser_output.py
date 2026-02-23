import sqlite3
import gzip
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.mjlog_parser import MjlogParser

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# Get one log
cur.execute("SELECT id, log FROM logs WHERE log IS NOT NULL AND log != '' LIMIT 1")
log_id, xml_compressed = cur.fetchone()

# Decompress
xml_content = gzip.decompress(xml_compressed).decode('utf-8')

print(f"Log ID: {log_id}")
print(f"XML length: {len(xml_content)}")
print(f"\nFirst 1000 chars of XML:\n{xml_content[:1000]}\n")

# Parse
parser = MjlogParser(xml_content)
game_states = parser.parse()

print(f"Number of game states returned: {len(game_states)}")
print(f"Type: {type(game_states)}")

# Check which states have discards
states_with_discards = [s for s in game_states if s.discards]
print(f"States with discards: {len(states_with_discards)}")

if states_with_discards:
    print(f"\nFirst state with discards:")
    state = states_with_discards[0]
    print(f"  Player ID: {state.player_id}")
    print(f"  Number of discards: {len(state.discards)}")
    print(f"  Hand tiles: {state.hand_tiles}")
    print(f"  Dora indicators: {state.dora_indicators}")
    print(f"  Visible tiles count: {len(state.visible_tiles)}")
    
    if state.discards:
        print(f"\n  First 5 discards:")
        for i, d in enumerate(state.discards[:5]):
            print(f"    {i+1}. Turn {d.turn}: tile {d.tile}, tsumogiri={d.is_tsumogiri}")
else:
    print("\nNo states with discards found!")

conn.close()
