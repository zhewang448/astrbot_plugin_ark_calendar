from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if ROOT.name == "astrbot_plugin":
    sys.path.insert(0, str(ROOT.parent))
elif "astrbot_plugin" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "astrbot_plugin",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["astrbot_plugin"] = module
    spec.loader.exec_module(module)
