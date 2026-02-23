import requests

test_urls = [
    'https://tenhou.net/sc/raw/scraw2024.zip',
    'https://tenhou.net/sc/raw/scraw2023.zip',
    'https://tenhou.net/sc/raw/scraw2020.zip',
    'https://tenhou.net/sc/raw/scraw2019.zip',
    'https://tenhou.net/sc/raw/scraw2015.zip',
    'https://tenhou.net/sc/raw/scraw2009.zip',
]

for url in test_urls:
    try:
        r = requests.get(url, timeout=10, stream=True)
        size = int(r.headers.get("content-length", 0))
        print(f'{url.split("/")[-1]}: {r.status_code}, {size / (1024 * 1024):.2f} MB')
    except Exception as e:
        print(f'{url.split("/")[-1]}: Error - {e}')


