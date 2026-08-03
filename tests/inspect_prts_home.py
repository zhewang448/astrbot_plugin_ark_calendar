from __future__ import annotations
import asyncio, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'.testdeps')]
import aiohttp
from bs4 import BeautifulSoup

async def main():
    async with aiohttp.ClientSession(headers={'User-Agent':'AstrBot-ArkCalendar-Test/0.1'}) as s:
        text=await (await s.get('https://prts.wiki/')).text()
    soup=BeautifulSoup(text,'html.parser')
    for node in soup.find_all(True):
        raw=node.get_text(' ',strip=True)
        if raw and len(raw)<300 and any(k in raw for k in ('剿灭作战','保全派驻','网页活动','限时寻访')):
            attrs={k:v for k,v in node.attrs.items() if k.startswith('data-') or k in ('class','id')}
            if node.name == 'p' and 'mp-today-cn' in (node.get('class') or []):
                print(node.decode())
            elif attrs or node.name in ('li','td','div'):
                print(node.name, attrs, repr(raw[:260]))

asyncio.run(main())
