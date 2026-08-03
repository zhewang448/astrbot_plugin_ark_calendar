from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_NAME = "astrbot_plugin_ark_calendar"

ROOT_FILES = {
    "__init__.py",
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
}
SOURCE_DIRS = {"core", "sources", "templates"}
EXCLUDED_PARTS = {"__pycache__", ".testdeps", "preview", "tests", "tools", "dist", ".git"}


def version() -> str:
    text = (ROOT / "metadata.yaml").read_text("utf-8")
    match = re.search(r"^version:\s*v?(.+?)\s*$", text, re.MULTILINE)
    return match.group(1) if match else "dev"


def include(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if path.suffix in {".pyc", ".pyo"}:
        return False
    if len(relative.parts) == 1:
        return relative.name in ROOT_FILES
    if relative.parts[0] in SOURCE_DIRS:
        return True
    if relative.parts[0] == "assets":
        # 干员头像全部通过 PRTS API 获取，发布包不携带逐干员头像。
        return not relative.name.startswith("operator-")
    return False


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
