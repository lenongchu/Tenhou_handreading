import sqlite3

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# Search for "1s-2s" pattern
pattern = "1s-2s"
cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{pattern}%",))
count = cur.fetchone()[0]

print(f"Found {count} states matching pattern: {pattern}")

if count > 0:
    # Show first 5 matches
    cur.execute("SELECT id, log_id, player_id, turn, normalized_pattern FROM game_states WHERE normalized_pattern LIKE ? LIMIT 5", (f"%{pattern}%",))
    print(f"\nFirst 5 matches:")
    for row in cur.fetchall():
        print(f"  State ID: {row[0]}, Log: {row[1]}, Player: {row[2]}, Turn: {row[3]}")
        print(f"    Pattern: {row[4]}")

# Now search for equivalent patterns
equivalent_patterns = ["1m-2m", "1p-2p", "2s-3s"]
print(f"\n=== Testing Equivalence ===")
for eq_pattern in equivalent_patterns:
    cur.execute("SELECT COUNT(*) FROM game_states WHERE normalized_pattern LIKE ?", (f"%{eq_pattern}%",))
    eq_count = cur.fetchone()[0]
    print(f"  {eq_pattern}: {eq_count} matches")

conn.close()
