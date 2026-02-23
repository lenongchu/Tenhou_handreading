import sqlite3
conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print('Current tables in database:', tables)
conn.close()
