import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def _install_astrbot_stubs() -> None:
    astrbot = ModuleType("astrbot")
    astrbot.__path__ = []
    api = ModuleType("astrbot.api")
    api.__path__ = []
    api.logger = SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)
    components = ModuleType("astrbot.api.message_components")
    event = ModuleType("astrbot.api.event")
    platform = ModuleType("astrbot.api.platform")
    core = ModuleType("astrbot.core")
    core.__path__ = []
    message = ModuleType("astrbot.core.message")
    message.__path__ = []
    message_components = ModuleType("astrbot.core.message.components")

    class Plain:
        def __init__(self, text: str):
            self.text = text

    class Image:
        @classmethod
        def fromFileSystem(cls, path: str):
            return cls(path)

        def __init__(self, path: str):
            self.path = path

    class MessageChain(list):
        pass

    class Node:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Nodes:
        def __init__(self, nodes):
            self.nodes = nodes

    components.Plain = Plain
    components.Image = Image
    event.MessageChain = MessageChain
    platform.MessageType = SimpleNamespace(GROUP_MESSAGE=SimpleNamespace(value="Group"))
    message_components.Node = Node
    message_components.Nodes = Nodes
    sys.modules.update({
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.message_components": components,
        "astrbot.api.event": event,
        "astrbot.api.platform": platform,
        "astrbot.core": core,
        "astrbot.core.message": message,
        "astrbot.core.message.components": message_components,
    })


_install_astrbot_stubs()

from core import bilibili_manager  # noqa: E402


class FakeSource:
    def __init__(self, dynamics, state=None):
        self.dynamics = dynamics
        self.state = state or {"dynamics": {}}

    async def recent_dynamics(self, **_kwargs):
        return self.dynamics

    @staticmethod
    def should_push(dynamic, push_types):
        return not push_types or dynamic["dynamic_type"] in push_types

    @staticmethod
    def format_relative_time(_pub_date):
        return "刚刚"

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.state = state


def test_push_types_suppresses_filtered_dynamic(monkeypatch):
    source = FakeSource([{"id": "r1", "title": "转发", "dynamic_type": "repost"}])
    context = SimpleNamespace()
    manager = bilibili_manager.BilibiliDynamicManager(
        source, context,
        {"bilibili_dynamic": {"target_sid_list": ["p:Group:1"], "push_types": ["video"]}},
    )
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.check_and_push()) == (0, 0)
    record = source.state["dynamics"]["r1"]
    assert record["suppressed"] is True
    assert record["pushed"] is True


def test_initial_enable_establishes_a_new_history_baseline():
    dynamics = [
        {"id": "old", "title": "旧动态", "dynamic_type": "text"},
        {"id": "during-disabled", "title": "停用期间", "dynamic_type": "text"},
    ]
    source = FakeSource(dynamics, {"baseline_established": True, "push_ever_enabled": False, "dynamics": {}})
    config = {"bilibili_dynamic": {"push_enabled": False}}
    manager = bilibili_manager.BilibiliDynamicManager(source, SimpleNamespace(), config)

    asyncio.run(manager.initialize_state())
    config["bilibili_dynamic"]["push_enabled"] = True
    asyncio.run(manager.initialize_state())

    assert source.state["push_ever_enabled"] is True
    assert all(record["pushed"] for record in source.state["dynamics"].values())


def test_disabling_qq_forward_uses_a_regular_message(tmp_path: Path):
    images = []
    for name in ("one.png", "two.png"):
        image = tmp_path / name
        image.write_bytes(b"image")
        images.append(str(image))
    context = SimpleNamespace(get_platform_inst=lambda _platform_id: SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp")))
    manager = bilibili_manager.BilibiliDynamicManager(
        FakeSource([]), context,
        {"bilibili_dynamic": {"use_forward_on_qq": False}},
    )
    components = asyncio.run(manager.build_message_components({
        "dynamic_type": "image", "title": "图片动态", "description_text": "内容",
        "cached_images": images, "link": "https://example.invalid",
    }, "p:Group:1"))

    assert len(components) == 4
    assert not hasattr(components[0], "nodes")


class FakeRenderer:
    def __init__(self, rendered: Path):
        self.rendered = rendered
        self.calls: list[bool] = []

    async def bilibili_dynamic(self, _dynamic, *, include_images: bool):
        self.calls.append(include_images)
        return str(self.rendered)


def test_small_dynamic_renders_text_and_all_images_into_one_card(tmp_path: Path):
    source_images = []
    for name in ("one.png", "two.png"):
        image = tmp_path / name
        image.write_bytes(b"image")
        source_images.append(str(image))
    rendered = tmp_path / "rendered.png"
    rendered.write_bytes(b"image")
    renderer = FakeRenderer(rendered)
    context = SimpleNamespace(get_platform_inst=lambda _platform_id: None)
    manager = bilibili_manager.BilibiliDynamicManager(
        FakeSource([]), context,
        {"bilibili_dynamic": {"render_image_count_threshold": 2}},
        renderer=renderer,
    )

    components = asyncio.run(manager.build_message_components({
        "dynamic_type": "image", "title": "双图动态", "description_text": "内容",
        "images": ["https://example.invalid/1", "https://example.invalid/2"],
        "cached_images": source_images, "link": "https://example.invalid/dynamic",
    }, "p:Group:1"))

    assert renderer.calls == [True]
    assert len(components) == 2
    assert components[0].text.endswith("https://example.invalid/dynamic")
    assert components[1].path == str(rendered)


def test_large_dynamic_uses_text_card_then_forwarded_original_images(tmp_path: Path):
    source_images = []
    for name in ("one.png", "two.png"):
        image = tmp_path / name
        image.write_bytes(b"image")
        source_images.append(str(image))
    rendered = tmp_path / "rendered.png"
    rendered.write_bytes(b"image")
    renderer = FakeRenderer(rendered)
    context = SimpleNamespace(
        get_platform_inst=lambda _platform_id: SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp"))
    )
    manager = bilibili_manager.BilibiliDynamicManager(
        FakeSource([]), context,
        {"bilibili_dynamic": {"render_image_count_threshold": 1, "use_forward_on_qq": True}},
        renderer=renderer,
    )

    components = asyncio.run(manager.build_message_components({
        "dynamic_type": "image", "title": "双图动态", "description_text": "内容",
        "images": ["https://example.invalid/1", "https://example.invalid/2"],
        "cached_images": source_images, "link": "https://example.invalid/dynamic",
    }, "p:Group:1"))

    assert renderer.calls == [False]
    assert len(components) == 3
    assert components[0].text.endswith("https://example.invalid/dynamic")
    assert components[1].path == str(rendered)
    assert len(components[2].nodes) == 2
