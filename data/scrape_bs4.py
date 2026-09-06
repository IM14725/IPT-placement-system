import urllib.request
import json
from bs4 import BeautifulSoup

url = 'https://en.wikipedia.org/wiki/List_of_universities_in_Tanzania'
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    html = urllib.request.urlopen(req).read()
    soup = BeautifulSoup(html, 'html.parser')
    insts = []
    for table in soup.find_all('table', class_='wikitable'):
        for row in table.find_all('tr')[1:]:
            cols = row.find_all(['td', 'th'])
            if len(cols) > 0:
                name = cols[0].text.strip()
                abbr = cols[1].text.strip() if len(cols) > 1 else ''
                # remove citations
                name = name.split('[')[0].strip()
                abbr = abbr.split('[')[0].strip()
                insts.append({'name': name, 'abbreviation': abbr, 'aliases': [abbr, name]})
    print(json.dumps(insts, indent=2))
except Exception as e:
    print(e)

