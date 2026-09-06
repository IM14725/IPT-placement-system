import urllib.request
import re
import json

def get_list():
    url = 'https://en.wikipedia.org/wiki/List_of_universities_in_Tanzania'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req).read().decode('utf-8')
    matches = re.findall(r'<td>(?:<a[^>]+>)?([^<]+)(?:</a>)?</td>\s*<td>([^<]+)</td>', html)
    for m in matches:
        print(f'{m[0].strip()} | {m[1].strip()}')

get_list()

