"""Tests for image access tools (camera frames and image files)."""

import base64
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from custom_components.mcp_server_http_transport.const import DOMAIN
from custom_components.mcp_server_http_transport.tools import images
from custom_components.mcp_server_http_transport.tools.images import (
    get_camera_image,
    get_image_file,
)

# A minimal valid 1x1 PNG.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _make_hass(
    *,
    config_dir: Path | None = None,
    camera: bool = False,
    image_file: bool = False,
    is_allowed: bool = True,
) -> Mock:
    hass = Mock()
    hass.config.config_dir = str(config_dir) if config_dir is not None else "/config"
    hass.config.is_allowed_path = Mock(return_value=is_allowed)
    hass.data = {
        DOMAIN: {
            "camera_image_access": camera,
            "image_file_access": image_file,
        }
    }

    async def _run_in_executor(fn, *args):
        return fn(*args)

    hass.async_add_executor_job = AsyncMock(side_effect=_run_in_executor)
    return hass


def _fake_camera_module(image_obj=None, *, raises: Exception | None = None) -> types.ModuleType:
    """Build a stand-in for homeassistant.components.camera with async_get_image."""
    module = types.ModuleType("homeassistant.components.camera")
    if raises is not None:
        module.async_get_image = AsyncMock(side_effect=raises)
    else:
        module.async_get_image = AsyncMock(return_value=image_obj)
    return module


class TestDisabledByDefault:
    async def test_camera_disabled(self):
        hass = _make_hass(camera=False)
        result = await get_camera_image(hass, {"entity_id": "camera.front_door"})
        assert "disabled" in result["content"][0]["text"].lower()

    async def test_image_file_disabled(self, tmp_path):
        hass = _make_hass(config_dir=tmp_path, image_file=False)
        result = await get_image_file(hass, {"path": "snap.png"})
        assert "disabled" in result["content"][0]["text"].lower()


class TestGetCameraImage:
    async def test_returns_image_content(self):
        hass = _make_hass(camera=True)
        image = types.SimpleNamespace(content=_PNG_BYTES, content_type="image/png")
        fake = _fake_camera_module(image)
        with patch.dict(sys.modules, {"homeassistant.components.camera": fake}):
            result = await get_camera_image(hass, {"entity_id": "camera.front_door"})

        block = result["content"][0]
        assert block["type"] == "image"
        assert block["mimeType"] == "image/png"
        assert base64.b64decode(block["data"]) == _PNG_BYTES

    async def test_passes_width_height(self):
        hass = _make_hass(camera=True)
        image = types.SimpleNamespace(content=_PNG_BYTES, content_type="image/jpeg")
        fake = _fake_camera_module(image)
        with patch.dict(sys.modules, {"homeassistant.components.camera": fake}):
            await get_camera_image(
                hass, {"entity_id": "camera.front_door", "width": 640, "height": 480}
            )

        fake.async_get_image.assert_awaited_once_with(
            hass, "camera.front_door", width=640, height=480
        )

    async def test_rejects_non_camera_entity(self):
        hass = _make_hass(camera=True)
        result = await get_camera_image(hass, {"entity_id": "light.kitchen"})
        assert "not a camera entity" in result["content"][0]["text"]

    async def test_handles_capture_error(self):
        hass = _make_hass(camera=True)
        fake = _fake_camera_module(raises=RuntimeError("camera offline"))
        with patch.dict(sys.modules, {"homeassistant.components.camera": fake}):
            result = await get_camera_image(hass, {"entity_id": "camera.front_door"})
        assert "Error capturing image" in result["content"][0]["text"]
        assert "camera offline" in result["content"][0]["text"]

    async def test_rejects_oversized_image(self, monkeypatch):
        monkeypatch.setattr(images, "_MAX_IMAGE_BYTES", 4)
        hass = _make_hass(camera=True)
        image = types.SimpleNamespace(content=b"12345", content_type="image/jpeg")
        fake = _fake_camera_module(image)
        with patch.dict(sys.modules, {"homeassistant.components.camera": fake}):
            result = await get_camera_image(hass, {"entity_id": "camera.front_door"})
        assert "too large" in result["content"][0]["text"]


class TestGetImageFile:
    async def test_reads_relative_path(self, tmp_path):
        (tmp_path / "snap.png").write_bytes(_PNG_BYTES)
        hass = _make_hass(config_dir=tmp_path, image_file=True, is_allowed=True)
        result = await get_image_file(hass, {"path": "snap.png"})

        block = result["content"][0]
        assert block["type"] == "image"
        assert block["mimeType"] == "image/png"
        assert base64.b64decode(block["data"]) == _PNG_BYTES
        # Relative paths are resolved against the config directory before the
        # allowlist check.
        hass.config.is_allowed_path.assert_called_once_with(str(tmp_path / "snap.png"))

    async def test_reads_absolute_path(self, tmp_path):
        target = tmp_path / "sub" / "front.jpg"
        target.parent.mkdir()
        target.write_bytes(_PNG_BYTES)
        hass = _make_hass(config_dir=tmp_path, image_file=True, is_allowed=True)
        result = await get_image_file(hass, {"path": str(target)})

        block = result["content"][0]
        assert block["mimeType"] == "image/jpeg"
        assert base64.b64decode(block["data"]) == _PNG_BYTES

    async def test_rejects_unsupported_extension(self, tmp_path):
        hass = _make_hass(config_dir=tmp_path, image_file=True)
        result = await get_image_file(hass, {"path": "secrets.yaml"})
        assert "Unsupported image type" in result["content"][0]["text"]

    async def test_rejects_disallowed_path(self, tmp_path):
        hass = _make_hass(config_dir=tmp_path, image_file=True, is_allowed=False)
        result = await get_image_file(hass, {"path": "/etc/some.png"})
        assert "not allowed" in result["content"][0]["text"]

    async def test_missing_file(self, tmp_path):
        hass = _make_hass(config_dir=tmp_path, image_file=True, is_allowed=True)
        result = await get_image_file(hass, {"path": "nope.png"})
        assert "does not exist" in result["content"][0]["text"]

    async def test_rejects_directory(self, tmp_path):
        (tmp_path / "shots.png").mkdir()
        hass = _make_hass(config_dir=tmp_path, image_file=True, is_allowed=True)
        result = await get_image_file(hass, {"path": "shots.png"})
        assert "is not a file" in result["content"][0]["text"]

    async def test_handles_read_error(self, tmp_path):
        hass = _make_hass(config_dir=tmp_path, image_file=True, is_allowed=True)
        hass.async_add_executor_job = AsyncMock(side_effect=OSError("disk error"))
        result = await get_image_file(hass, {"path": "snap.png"})
        assert "Error reading image file" in result["content"][0]["text"]
        assert "disk error" in result["content"][0]["text"]

    async def test_rejects_oversized_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(images, "_MAX_IMAGE_BYTES", 4)
        (tmp_path / "big.png").write_bytes(b"12345")
        hass = _make_hass(config_dir=tmp_path, image_file=True, is_allowed=True)
        result = await get_image_file(hass, {"path": "big.png"})
        assert "too large" in result["content"][0]["text"]
