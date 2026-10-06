import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def ai_tools(monkeypatch):
    """用最小替身加载 core.ai_tools，替身只在本测试内生效。"""
    components = ModuleType("astrbot.api.message_components")

    class Image:
        @classmethod
        def fromFileSystem(cls, path):
            image = cls()
            image.path = path
            return image

    components.Image = Image
    event_module = ModuleType("astrbot.api.event")

    class MessageChain(list):
        pass

    event_module.MessageChain = MessageChain
    tool_module = ModuleType("astrbot.core.agent.tool")

    class FunctionTool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ToolSet:
        def __init__(self, tools):
            self.tools = list(tools)

        def get(self, name):
            return next(tool for tool in self.tools if tool.name == name)

    tool_module.FunctionTool = FunctionTool
    tool_module.ToolSet = ToolSet
    for name in ("astrbot", "astrbot.api", "astrbot.core", "astrbot.core.agent"):
        package = ModuleType(name)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setitem(sys.modules, "astrbot.api.message_components", components)
    monkeypatch.setitem(sys.modules, "astrbot.api.event", event_module)
    monkeypatch.setitem(sys.modules, "astrbot.core.agent.tool", tool_module)
    monkeypatch.delitem(sys.modules, "core.ai_tools", raising=False)
    module = importlib.import_module("core.ai_tools")
    yield module
    sys.modules.pop("core.ai_tools", None)


def _plugin(tmp_path: Path, *, render_fails: bool = False, max_tags=5, enabled_tools=None):
    image = tmp_path / "recruit.png"
    image.write_bytes(PNG)

    class Renderer:
        def __init__(self):
            self.calls = []

        async def recruitment_result(self, results, tags):
            self.calls.append(tags)
            if render_fails:
                raise RuntimeError("t2i down")
            return str(image)

    class Recruitment:
        async def get_recruitment_pool(self):
            return {"characters": [
                {"name": "甲", "rarity": 4, "tags": ["输出", "近卫干员"]},
                {"name": "乙", "rarity": 5, "tags": ["输出", "生存"]},
            ]}

    config = {"ai": {"enabled_tools": enabled_tools}} if enabled_tools is not None else {}
    service = SimpleNamespace(
        value=lambda section, key, default, *_: config.get(section, {}).get(key, default),
        int_value=lambda section, key, default, **_: default,
        recruit_max_tags=lambda: max_tags,
        logger=SimpleNamespace(warning=lambda *a, **k: None),
    )
    return SimpleNamespace(service=service, recruitment_source=Recruitment(), renderer=Renderer())


class Event:
    def __init__(self):
        self.sent = []

    async def send(self, chain):
        self.sent.append(chain)


def test_recruitment_image_tool_sends_image_and_returns_summary(ai_tools, tmp_path):
    plugin = _plugin(tmp_path)
    tool = ai_tools.build_ai_tools(plugin).get("ark_calendar_recruitment_image")
    event = Event()

    result = json.loads(asyncio.run(tool.handler(event, tags=["输出", "生存"])))

    assert result["image_sent"] is True
    assert result["valid_tags"] == ["输出", "生存"]
    assert any(row["operators"] == ["乙"] for row in result["results"])
    assert len(event.sent) == 1 and event.sent[0][0].path == str(tmp_path / "recruit.png")


def test_recruitment_image_tool_respects_tag_limit_without_rendering(ai_tools, tmp_path):
    plugin = _plugin(tmp_path, max_tags=1)
    tool = ai_tools.build_ai_tools(plugin).get("ark_calendar_recruitment_image")
    event = Event()

    result = json.loads(asyncio.run(tool.handler(event, tags=["输出", "生存"])))

    assert (result["error"], result["max_tags"], result["image_sent"]) == ("too_many_tags", 1, False)
    assert event.sent == [] and plugin.renderer.calls == []


def test_recruitment_image_tool_falls_back_to_text_when_render_fails(ai_tools, tmp_path):
    plugin = _plugin(tmp_path, render_fails=True)
    tool = ai_tools.build_ai_tools(plugin).get("ark_calendar_recruitment_image")
    event = Event()

    result = json.loads(asyncio.run(tool.handler(event, tags=["输出"])))

    assert (result["image_sent"], result["error"]) == (False, "render_failed")
    assert result["results"] and event.sent == []


def test_previous_full_default_tool_list_gets_new_image_tool(ai_tools, tmp_path):
    previous_full = list(ai_tools.TOOL_NAMES[:11])
    names = {tool.name for tool in ai_tools.build_ai_tools(_plugin(tmp_path, enabled_tools=previous_full)).tools}
    assert "ark_calendar_recruitment_image" in names

    # 用户手动挑选过的子集保持原样，不自动补齐。
    subset = ["ark_calendar_today", "ark_calendar_recruitment"]
    names = {tool.name for tool in ai_tools.build_ai_tools(_plugin(tmp_path, enabled_tools=subset)).tools}
    assert names == set(subset)
