import requests
r = requests.head('https://tenhou.net/sc/raw/dat/2024.zip', timeout=10)
print(f'Status: {r.status_code}')
if 'content-length' in r.headers:
    size_mb = int(r.headers['content-length']) / (1024*1024)
    print(f'Size: {size_mb:.2f} MB')
