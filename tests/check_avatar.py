from __future__ import annotations
import asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'.testdeps'),str(ROOT.parent)]
import aiohttp
from astrbot_plugin.sources.http import HttpClient
from astrbot_plugin.sources.prts import PrtsSource
async def main():
 async with aiohttp.ClientSession() as s:
  p=PrtsSource(HttpClient(s,retries=0),'https://prts.wiki')
  raw=await p.http.json(p.api_url,params={'action':'query','titles':'File:头像_卡缇.png','prop':'imageinfo','iiprop':'url','format':'json','formatversion':'2'})
  print(json.dumps(raw,ensure_ascii=False,indent=2))
  print(await p.resolve_avatar_urls(['卡缇']))
asyncio.run(main())
