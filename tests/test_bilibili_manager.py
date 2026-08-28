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

    class Video:
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
    components.Video = Video
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
from core.platform_utils import platform_supports_proactive_send  # noqa: E402


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


def test_proactive_send_uses_platform_metadata_and_fails_closed():
    supported = SimpleNamespace(
        get_platform_inst=lambda _: SimpleNamespace(
            meta=lambda: SimpleNamespace(name="custom", support_proactive_message=True)
        )
    )
    unsupported = SimpleNamespace(
        get_platform_inst=lambda _: SimpleNamespace(
            meta=lambda: SimpleNamespace(name="custom", support_proactive_message=False)
        )
    )

    assert platform_supports_proactive_send("p:Group:1", supported) is True
    assert platform_supports_proactive_send("p:Group:1", unsupported) is False
    assert platform_supports_proactive_send("invalid", supported) is False


def test_push_types_suppresses_filtered_dynamic(monkeypatch):
    source = FakeSource([{"id": "r1", "title": "转发", "dynamic_type": "repost"}])
    context = SimpleNamespace()
    manager = bilibili_manager.BilibiliDynamicManager(
        source, context,
        {"bilibili_dynamic": {"push_enabled": True, "target_sid_list": ["p:Group:1"], "push_types": ["video"]}},
    )
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.check_and_push()) == (0, 0)
    record = source.state["dynamics"]["r1"]
    assert record == {"state": "suppressed"}


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

    assert source.state["push_enabled"] is True
    assert all(record == {"state": "ignored"} for record in source.state["dynamics"].values())


def test_initial_baseline_does_not_replay_history_but_pushes_later_dynamic(monkeypatch):
    sent = []

    class Context:
        async def send_message(self, sid, chain):
            sent.append(sid)
            return True

    history = {
        "id": "history",
        "title": "安装前动态",
        "dynamic_type": "text",
        "description_text": "历史内容",
        "link": "https://example.invalid/history",
    }
    source = FakeSource([history])
    manager = bilibili_manager.BilibiliDynamicManager(
        source,
        Context(),
        {
            "bilibili_dynamic": {
                "push_enabled": True,
                "target_sid_list": ["p:Group:1"],
            }
        },
        require_baseline=True,
    )
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.initialize_state()) is True
    assert asyncio.run(manager.check_and_push()) == (0, 0)
    assert sent == []

    source.dynamics = [{
        "id": "new",
        "title": "安装后动态",
        "dynamic_type": "text",
        "description_text": "新内容",
        "link": "https://example.invalid/new",
    }, history]

    assert asyncio.run(manager.check_and_push()) == (1, 0)
    assert sent == ["p:Group:1"]


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
    assert components[0].path == str(rendered)
    assert components[1].text == "查看完整动态：https://example.invalid/dynamic"


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
    assert len(components) == 2
    assert components[0].path == str(rendered)
    assert components[1].text == "查看完整动态：https://example.invalid/dynamic"
    forward_components = asyncio.run(manager.build_forward_components({
        "images": ["https://example.invalid/1", "https://example.invalid/2"],
        "cached_images": source_images,
    }, "p:Group:1"))
    assert len(forward_components) == 1
    assert len(forward_components[0].nodes) == 2


def test_auto_push_sends_primary_chain_before_forwarded_images(tmp_path: Path, monkeypatch):
    image = tmp_path / "one.png"
    image.write_bytes(b"image")
    rendered = tmp_path / "rendered.png"
    rendered.write_bytes(b"image")
    sent = []

    class Context:
        def get_platform_inst(self, _platform_id):
            return SimpleNamespace(meta=lambda: SimpleNamespace(name="aiocqhttp"))

        async def send_message(self, sid, chain):
            sent.append((sid, chain))
            return True

    manager = bilibili_manager.BilibiliDynamicManager(
        FakeSource([{
            "id": "new", "dynamic_type": "image", "title": "图片动态", "description_text": "内容",
            "images": ["https://example.invalid/1", "https://example.invalid/2"],
            "cached_images": [str(image)], "link": "https://example.invalid/dynamic",
        }]),
        Context(),
        {"bilibili_dynamic": {"render_image_count_threshold": 1, "use_forward_on_qq": True}},
        renderer=FakeRenderer(rendered),
    )
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.force_push_recent(["p:Group:1"])) == (1, 0)
    assert len(sent) == 2
    assert sent[0][1][0].path == str(rendered)
    assert sent[0][1][1].text == "查看完整动态：https://example.invalid/dynamic"
    assert len(sent[1][1][0].nodes) == 1


def test_auto_push_uses_shared_global_dedup_across_targets(monkeypatch):
    sent = []

    class Context:
        async def send_message(self, sid, chain):
            sent.append((sid, chain))
            return True

    source = FakeSource([{
        "id": "shared", "dynamic_type": "text", "title": "New dynamic",
        "description_text": "content", "link": "https://example.invalid/dynamic",
    }])
    manager = bilibili_manager.BilibiliDynamicManager(
        source,
        Context(),
        {"bilibili_dynamic": {"push_enabled": True, "target_sid_list": ["p:Group:1", "p:Group:2"]}},
    )
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.check_and_push()) == (2, 0)
    assert asyncio.run(manager.check_and_push()) == (0, 0)
    assert source.state["dynamics"]["shared"] == {
        "targets": {"p:Group:1": True, "p:Group:2": True}
    }


def test_auto_push_retries_only_failed_target(monkeypatch):
    sent = []
    attempts = {"p:Group:2": 0}

    class Context:
        async def send_message(self, sid, chain):
            sent.append(sid)
            if sid == "p:Group:2" and attempts[sid] == 0:
                attempts[sid] += 1
                return False
            return True

    source = FakeSource([{
        "id": "partial", "dynamic_type": "text", "title": "Partial",
        "description_text": "content", "link": "https://example.invalid/dynamic",
    }])
    config = {"bilibili_dynamic": {"push_enabled": True, "target_sid_list": ["p:Group:1", "p:Group:2"]}}
    manager = bilibili_manager.BilibiliDynamicManager(source, Context(), config)
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.check_and_push()) == (1, 1)
    assert source.state["dynamics"]["partial"] == {
        "targets": {"p:Group:1": True, "p:Group:2": False}
    }
    assert asyncio.run(manager.check_and_push()) == (1, 0)
    assert sent == ["p:Group:1", "p:Group:2", "p:Group:2"]
    assert source.state["dynamics"]["partial"] == {
        "targets": {"p:Group:1": True, "p:Group:2": True}
    }


def test_push_does_not_run_until_baseline_is_ready(monkeypatch):
    sent = []

    class Context:
        async def send_message(self, sid, chain):
            sent.append((sid, chain))
            return True

    source = FakeSource([{
        "id": "history", "dynamic_type": "text", "title": "历史",
        "description_text": "内容", "link": "https://example.invalid/history",
    }])
    manager = bilibili_manager.BilibiliDynamicManager(
        source,
        Context(),
        {"bilibili_dynamic": {"target_sid_list": ["p:Group:1"]}},
    )
    manager._baseline_ready = False
    async def failed_baseline():
        return False
    monkeypatch.setattr(manager, "initialize_state", failed_baseline)

    assert asyncio.run(manager.check_and_push()) == (0, 0)
    assert sent == []


def test_new_target_does_not_receive_previously_detected_dynamic(monkeypatch):
    sent = []

    class Context:
        async def send_message(self, sid, chain):
            sent.append(sid)
            return True

    source = FakeSource([{
        "id": "target-change", "dynamic_type": "text", "title": "动态",
        "description_text": "内容", "link": "https://example.invalid/dynamic",
    }])
    config = {"bilibili_dynamic": {"push_enabled": True, "target_sid_list": ["p:Group:1"]}}
    manager = bilibili_manager.BilibiliDynamicManager(source, Context(), config)
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.check_and_push()) == (1, 0)
    config["bilibili_dynamic"]["target_sid_list"] = ["p:Group:2"]
    assert asyncio.run(manager.check_and_push()) == (0, 0)
    assert sent == ["p:Group:1"]
    assert source.state["dynamics"]["target-change"] == {"state": "ignored"}


def test_auto_push_sends_parser_video(monkeypatch, tmp_path):
    sent = []
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"video")

    class Context:
        async def send_message(self, sid, chain):
            sent.append((sid, chain))
            return True

    async def fake_fetch_video_path(_context, video_url):
        assert video_url == "https://www.bilibili.com/video/BV1videoTest"
        return video_file

    monkeypatch.setattr(bilibili_manager, "fetch_video_path", fake_fetch_video_path)
    source = FakeSource([{
        "id": "video", "dynamic_type": "video", "title": "视频动态",
        "description_text": "视频链接：https://www.bilibili.com/video/BV1videoTest",
        "description_html": '<a href="https://www.bilibili.com/video/BV1videoTest">视频</a>',
        "link": "https://example.invalid/dynamic",
    }])
    manager = bilibili_manager.BilibiliDynamicManager(
        source,
        Context(),
        {
            "bilibili_dynamic": {
                "push_enabled": True,
                "target_sid_list": ["p:Group:1"],
                "push_types": ["video"],
                "send_video_via_parser": True,
            }
        },
    )
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.check_and_push()) == (1, 0)
    assert len(sent) == 2
    assert sent[1][1][0].path == str(video_file)
    assert source.state["dynamics"]["video"]["targets"] == {"p:Group:1": True}


def test_auto_push_aggregates_parser_video_failures(monkeypatch):
    sent = []
    notifications = []

    class Context:
        async def send_message(self, sid, _chain):
            sent.append(sid)
            return True

    class Notifier:
        async def notify(self, text, event):
            notifications.append((text, event))

    async def fake_fetch_video_path(_context, _video_url):
        return None

    monkeypatch.setattr(bilibili_manager, "fetch_video_path", fake_fetch_video_path)
    source = FakeSource([{
        "id": "video", "dynamic_type": "video", "title": "视频动态",
        "description_html": '<a href="https://www.bilibili.com/video/BV1videoTest">视频</a>',
        "link": "https://example.invalid/dynamic",
    }])
    manager = bilibili_manager.BilibiliDynamicManager(
        source,
        Context(),
        {
            "bilibili_dynamic": {
                "push_enabled": True,
                "target_sid_list": ["p:Group:1", "p:Group:2"],
                "push_types": ["video"],
                "send_video_via_parser": True,
            }
        },
        notification_manager=Notifier(),
    )
    monkeypatch.setattr(bilibili_manager, "platform_supports_proactive_send", lambda *_: True)

    assert asyncio.run(manager.check_and_push()) == (2, 0)
    assert len(sent) == 2
    assert len(notifications) == 1
    text, event = notifications[0]
    assert event == "bilibili_video_send_failed"
    assert "p:Group:1、p:Group:2" in text
    assert source.state["dynamics"]["video"]["targets"] == {
        "p:Group:1": True,
        "p:Group:2": True,
    }


def test_manual_query_builds_parser_video_components(monkeypatch, tmp_path):
    video_file = tmp_path / "manual.mp4"
    video_file.write_bytes(b"video")

    async def fake_fetch_video_path(_context, video_url):
        assert video_url == "https://www.bilibili.com/video/BV1videoTest"
        return video_file

    monkeypatch.setattr(bilibili_manager, "fetch_video_path", fake_fetch_video_path)
    manager = bilibili_manager.BilibiliDynamicManager(
        FakeSource([]),
        SimpleNamespace(),
        {
            "bilibili_dynamic": {
                "send_video_via_parser": True,
            }
        },
    )
    dynamic = {
        "dynamic_type": "video",
        "description_html": '<a href="https://www.bilibili.com/video/BV1videoTest">视频</a>',
    }

    components = asyncio.run(manager.build_parser_video_components(dynamic, "p:Group:1"))
    assert len(components) == 1
    assert components[0].path == str(video_file)


def test_manual_query_skips_parser_video_when_disabled():
    manager = bilibili_manager.BilibiliDynamicManager(
        FakeSource([]),
        SimpleNamespace(),
        {"bilibili_dynamic": {"send_video_via_parser": False}},
    )
    dynamic = {
        "dynamic_type": "video",
        "description_html": '<a href="https://www.bilibili.com/video/BV1videoTest">视频</a>',
    }

    assert asyncio.run(manager.build_parser_video_components(dynamic, "p:Group:1")) == []
