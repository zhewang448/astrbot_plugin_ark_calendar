from pathlib import Path

import pytest

from core.cache import JsonCache
from core.image_scale import ImageScaler


def test_json_cache_rejects_paths_outside_root(tmp_path: Path):
    cache = JsonCache(tmp_path / "cache")

    assert cache.path("valid.json") == (tmp_path / "cache" / "valid.json").resolve()
    for name in ("", "../outside.json", str(tmp_path / "absolute.json"), "bad\x00name"):
        with pytest.raises(ValueError):
            cache.path(name)


def test_json_cache_rejects_symlink_outside_root(tmp_path: Path):
    cache = JsonCache(tmp_path / "cache")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = cache.root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"当前 Windows 环境不允许创建符号链接：{exc}")

    with pytest.raises(ValueError):
        cache.path("linked/escape.json")


def test_image_scaler_checks_dimensions_before_decoding(tmp_path: Path, monkeypatch):
    source = tmp_path / "oversized.img"
    source.write_bytes(b"header")

    class ImageStub:
        size = (10_000, 5_001)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def load(self):
            raise AssertionError("oversized image was decoded")

    from PIL import Image

    monkeypatch.setattr(Image, "open", lambda _path: ImageStub())
    scaler = ImageScaler(tmp_path / "scaled")
    with pytest.raises(ValueError, match="图片像素数过大"):
        scaler._build(source, 100, 100, None, "cover", False)
