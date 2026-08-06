from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_NAME = "astrbot_plugin_ark_calendar"

RUNTIME_FILES = {
    "__init__.py",
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "core/__init__.py",
    "core/assets.py",
    "core/cache.py",
    "core/command_args.py",
    "core/config.py",
    "core/messages.py",
    "core/models.py",
    "core/render_cache.py",
    "core/renderer.py",
    "core/scheduler_utils.py",
    "core/service.py",
    "core/subscription.py",
    "sources/__init__.py",
    "sources/anything_ics.py",
    "sources/gacha.py",
    "sources/http.py",
    "sources/prts.py",
    "templates/calendar.html",
    "templates/history_schedule.html",
    "templates/help.html",
    "assets/SourceHanSerifCN-Medium-6.otf",
}


def version() -> str:
    text = (ROOT / "metadata.yaml").read_text("utf-8")
    match = re.search(r"^version:\s*v?(.+?)\s*$", text, re.MULTILINE)
    return match.group(1) if match else "dev"


def include(path: Path) -> bool:
    return path.relative_to(ROOT).as_posix() in RUNTIME_FILES


def main() -> None:
    DIST.mkdir(exist_ok=True)
    target = DIST / f"{PACKAGE_NAME}-{version()}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(ROOT.rglob("*")):
            if path.is_file() and include(path):
                relative = path.relative_to(ROOT)
                archive.write(path, Path(PACKAGE_NAME) / relative)
    print(target)


if __name__ == "__main__":
    main()
