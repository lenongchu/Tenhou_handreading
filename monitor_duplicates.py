import sqlite3
import time
import sys
import io

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

print("=== Monitor Download and Check Duplicates ===")
print("Press Ctrl+C to stop\n")

conn = sqlite3.connect('data/tenhou.db')
last_total = 0
last_unique = 0

try:
    while True:
        cur = conn.cursor()
        
        # Count total and unique
        cur.execute('SELECT COUNT(*) FROM logs')
        total = cur.fetchone()[0]
        
        cur.execute('SELECT COUNT(DISTINCT id) FROM logs')
        unique = cur.fetchone()[0]
        
        # Count downloaded
        cur.execute('SELECT COUNT(*) FROM logs WHERE log IS NOT NULL AND log != ""')
        downloaded = cur.fetchone()[0]
        
        # Calculate changes
        total_change = total - last_total
        unique_change = unique - last_unique
        
        # Check for duplicates
        duplicates = total - unique
        
        print(f"\rTotal: {total:,} | Unique: {unique:,} | Downloaded: {downloaded:,} | "
              f"Duplicates: {duplicates} | +{total_change} records", end='', flush=True)
        
        if duplicates > 0:
            print(f"\n*** WARNING: {duplicates} duplicates detected! ***")
        
        last_total = total
        last_unique = unique
        
        time.sleep(5)
        
except KeyboardInterrupt:
    print("\n\nMonitoring stopped")
finally:
    conn.close()
