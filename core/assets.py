from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import aiohttp


class AssetCache:
    def __init__(
        self, root: Path, session: aiohttp.ClientSession, proxy: str = ""
    ):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.session = session
        self.proxy = proxy.strip()

    async def data_uri(self, source: str) -> str:
        if not source:
            return ""
        if source.startswith("data:"):
            return source
        if source.startswith(("http://", "https://")):
            try:
                path = await self._download(source)
            except Exception:
                return ""
        else:
            path = Path(source)
        if not path.exists():
            return ""
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"

    async def _download(self, url: str) -> Path:
        suffix = Path(urlparse(url).path).suffix or ".bin"
        target = self.root / f"{hashlib.sha256(url.encode()).hexdigest()}{suffix}"
        if target.exists() and target.stat().st_size:
            return target
        request_kwargs = {"proxy": self.proxy} if self.proxy else {}
        async with self.session.get(url, **request_kwargs) as resp:
            resp.raise_for_status()
            payload = await resp.read()
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_bytes(payload)
            temp.replace(target)
        return target
