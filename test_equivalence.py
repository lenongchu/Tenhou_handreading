import sqlite3
import sys
import os
sys.path.insert(0, 'src')
from simple_normalizer import normalize_discard_pattern

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

print("=== Equivalence Matching Test ===\n")

# Test different equivalent patterns
test_patterns = [
    ["1s", "2s"],
    ["1m", "2m"],  # Should normalize to 1s-2s
    ["1p", "2p"],  # Should normalize to 1s-2s
    ["2s", "3s"],  # Should normalize to 1s-2s
    ["7m", "8m"],  # Should normalize to 1s-2s
    ["9p", "8p"],  # Order matters: becomes 8s-9s, then normalizes to 1s-2s
]

print("Testing patterns (all should match 1s-2s after normalization):\n")
for pattern_tiles in test_patterns:
    # Normalize the pattern
    normalized = normalize_discard_pattern(pattern_tiles)
    
    # Query database
    cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{normalized}%",))
    count = cur.fetchone()[0]
    
    print(f"  {pattern_tiles} -> normalized: '{normalized}' -> {count:,} matches")

# Now test the actual "1s-2s" pattern
print(f"\n=== Direct Database Query ===")
pattern = "1s-2s"
cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{pattern}%",))
count = cur.fetchone()[0]
print(f"  Pattern '%{pattern}%': {count:,} matches")

# Show some examples
print(f"\n=== Sample Matches ===")
cur.execute("SELECT id, log_id, player_id, turn, normalized_pattern FROM game_states WHERE normalized_pattern LIKE ? LIMIT 5", (f"%{pattern}%",))
for row in cur.fetchall():
    print(f"  State {row[0]}: Player {row[2]}, Turn {row[3]}")
    print(f"    Pattern: {row[4]}")

conn.close()
