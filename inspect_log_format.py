import sqlite3

conn = sqlite3.connect('data/tenhou.db')
cur = conn.cursor()

# Get one log with content
cur.execute("SELECT id, log, LENGTH(log) FROM logs WHERE log IS NOT NULL AND log != '' LIMIT 1")
log_id, log_data, length = cur.fetchone()

print(f"Log ID: {log_id}")
print(f"Data length: {length} bytes")
print(f"Data type: {type(log_data)}")
print(f"First 100 bytes (hex): {log_data[:100].hex() if isinstance(log_data, bytes) else 'Not bytes'}")
print(f"First 200 chars: {log_data[:200] if isinstance(log_data, str) else 'Not string'}")

# Try to detect compression
if isinstance(log_data, bytes):
    # Check for gzip magic number
    if log_data[:2] == b'\x1f\x8b':
        print("\nDetected: GZIP compressed")
        import gzip
        try:
            xml_content = gzip.decompress(log_data).decode('utf-8')
            print(f"Decompressed length: {len(xml_content)}")
            print(f"First 500 chars of XML:\n{xml_content[:500]}")
        except Exception as e:
            print(f"Decompression failed: {e}")
    else:
        print("\nNot GZIP. Trying as raw text...")
        try:
            text = log_data.decode('utf-8')
            print(f"Decoded text (first 500 chars):\n{text[:500]}")
        except:
            print("Cannot decode as UTF-8")

conn.close()
