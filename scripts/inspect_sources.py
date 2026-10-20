import html
import re
import urllib.request

url = 'https://extension.msstate.edu/publications/beginning-beekeeping-mississippi'
raw = urllib.request.urlopen(url, timeout=20).read().decode()
raw = re.sub(r'<script.*?</script>|<style.*?</style>', '', raw, flags=re.S)
print('HEADINGS', [re.sub('<[^>]+>', '', h) for h in re.findall(r'<h[234][^>]*>(.*?)</h[234]>', raw, re.S)])
plain = html.unescape(re.sub('<[^>]+>', ' ', raw))
plain = re.sub(r'\s+', ' ', plain)
for term in ['Initial Feeding of a New Colony', 'Hive Equipment Needed', 'When to Start New Colonies']:
    index = plain.lower().find(term.lower(), plain.find('P3594'))
    print(term, plain[max(0, index - 200):index + 3000] if index >= 0 else 'not found')
for url in ['https://www.fao.org/4/t0104e/t0104e00.htm', 'https://extension.msstate.edu/publications/choosing-apiary-location', 'https://extension.psu.edu/hive-inspection']:
    try:
        page = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15).read().decode()
        page = re.sub(r'<script.*?</script>|<style.*?</style>', '', page, flags=re.S)
        print('SOURCE', url, 'TITLE', re.findall(r'<title[^>]*>(.*?)</title>', page, re.S), 'HEADINGS', [re.sub('<[^>]+>', '', h) for h in re.findall(r'<h[1234][^>]*>(.*?)</h[1234]>', page, re.S)])
        if 'fao' in url:
            print('FAO', [(a, re.sub('<[^>]+>', '', b)) for a, b in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.I | re.S)][:30])
    except Exception as exc:
        print(url, type(exc).__name__)
