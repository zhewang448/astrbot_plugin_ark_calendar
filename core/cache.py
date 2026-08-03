from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def load(self, name: str) -> Any | None:
        path = self.path(name)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, name: str, data: Any) -> None:
        path = self.path(name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, path)
