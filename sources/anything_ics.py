from __future__ import annotations

from .http import HttpClient


class AnythingIcsSource:
    def __init__(self, http: HttpClient, base_url: str):
        self.http = http
        self.base_url = base_url.rstrip("/")

    async def birthdays(self) -> list[dict]:
        data = await self.http.json(f"{self.base_url}/ark-birthday.json")
        return [item for item in data if isinstance(item, dict) and item.get("name")]

    async def events(self) -> list[dict]:
        data = await self.http.json(f"{self.base_url}/ark-event.json")
        return [item for item in data if isinstance(item, dict) and item.get("name")]
