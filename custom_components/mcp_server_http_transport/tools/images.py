"""Image access tools: capture live camera frames and read image files from disk."""

import base64
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from . import (
    ANNOTATION_READ_ONLY,
    register_tool,
)

_LOGGER = logging.getLogger(__name__)

# Image suffixes the file reader returns, mapped to their MIME type. Restricting
# to image types keeps this tool from becoming a general file-exfiltration path.
_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Cap on raw image bytes returned to the model. Base64 inflates by ~33% and MCP
# clients limit response size, so oversized frames are rejected with guidance.
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

_CAMERA_DISABLED_RESPONSE = {
    "content": [
        {
            "type": "text",
            "text": (
                "Camera image access is disabled. Enable it in the MCP Server integration "
                "settings: Settings → Devices & Services → MCP Server → Configure → "
                "Enable camera image access."
            ),
        }
    ]
}

_IMAGE_FILE_DISABLED_RESPONSE = {
    "content": [
        {
            "type": "text",
            "text": (
                "Image file access is disabled. Enable it in the MCP Server integration "
                "settings: Settings → Devices & Services → MCP Server → Configure → "
                "Enable image file access."
            ),
        }
    ]
}


def _camera_enabled(hass: HomeAssistant) -> bool:
    return hass.data.get(DOMAIN, {}).get("camera_image_access", False)


def _image_file_enabled(hass: HomeAssistant) -> bool:
    return hass.data.get(DOMAIN, {}).get("image_file_access", False)


def _image_content(data: bytes, mime_type: str) -> dict[str, Any]:
    """Wrap raw image bytes in an MCP image content block."""
    return {
        "content": [
            {
                "type": "image",
                "data": base64.b64encode(data).decode("ascii"),
                "mimeType": mime_type,
            }
        ]
    }


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


@register_tool(
    name="get_camera_image",
    description=(
        "Capture the current image from a Home Assistant camera entity and return it for "
        "visual analysis — what the camera sees right now, without writing a snapshot file. "
        "Optionally pass width and/or height (pixels) to downscale the frame and reduce its size"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "string",
                "description": "Camera entity ID, e.g. 'camera.front_door'",
            },
            "width": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional target width in pixels to scale the image down to",
            },
            "height": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional target height in pixels to scale the image down to",
            },
        },
        "required": ["entity_id"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_camera_image(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Capture a camera entity's current frame and return it as image content."""
    if not _camera_enabled(hass):
        return _CAMERA_DISABLED_RESPONSE

    entity_id = arguments["entity_id"]
    if not entity_id.startswith("camera."):
        return _text(f"'{entity_id}' is not a camera entity (expected a 'camera.' entity ID)")

    try:
        # Imported lazily: the camera component pulls in turbojpeg (a C extension)
        # at module load, which is absent in some environments. A top-level import
        # would break tool registration; deferring it keeps the failure local.
        from homeassistant.components.camera import async_get_image

        image = await async_get_image(
            hass,
            entity_id,
            width=arguments.get("width"),
            height=arguments.get("height"),
        )
    except Exception as e:
        return _text(f"Error capturing image from '{entity_id}': {e}")

    if len(image.content) > _MAX_IMAGE_BYTES:
        return _text(
            f"Image from '{entity_id}' is too large ({len(image.content)} bytes, "
            f"max {_MAX_IMAGE_BYTES}). Retry with a smaller width/height."
        )

    return _image_content(image.content, image.content_type)


@register_tool(
    name="get_image_file",
    description=(
        "Read an image file (JPEG, PNG, GIF, or WebP) from disk and return it for visual "
        "analysis. Use this to retrieve camera snapshots saved by the camera.snapshot service "
        "or other saved images. The path must be inside a directory Home Assistant is allowed "
        "to access (the config directory and configured media dirs by default)"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute path, or a path relative to the config directory. "
                    "E.g. 'www/snapshots/front_door.jpg' or '/config/www/snapshots/front_door.jpg'"
                ),
            }
        },
        "required": ["path"],
    },
    annotations=ANNOTATION_READ_ONLY,
)
async def get_image_file(hass: HomeAssistant, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read an allowed image file from disk and return it as image content."""
    if not _image_file_enabled(hass):
        return _IMAGE_FILE_DISABLED_RESPONSE

    raw_path = arguments["path"]
    suffix = Path(raw_path).suffix.lower()
    mime_type = _IMAGE_MIME_TYPES.get(suffix)
    if mime_type is None:
        allowed = ", ".join(sorted(_IMAGE_MIME_TYPES))
        return _text(f"Unsupported image type '{suffix or raw_path}'. Allowed: {allowed}")

    try:
        result = await hass.async_add_executor_job(_read_image_file_sync, hass, raw_path)
    except Exception as e:
        return _text(f"Error reading image file '{raw_path}': {e}")

    if isinstance(result, str):
        return _text(result)
    return _image_content(result, mime_type)


def _read_image_file_sync(hass: HomeAssistant, raw_path: str) -> bytes | str:
    """Validate and read an image file. Returns bytes, or an error string for the model.

    Path access is delegated to ``hass.config.is_allowed_path``, which honours
    Home Assistant's ``allowlist_external_dirs`` (config dir and media dirs) — the
    same allowlist that gates where ``camera.snapshot`` may write, so any snapshot
    the model created is readable here while everything else stays out of reach.
    """
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(hass.config.config_dir) / path

    if not hass.config.is_allowed_path(str(path)):
        return (
            f"Access to '{raw_path}' is not allowed. The path must be within a Home Assistant "
            "allowed directory (the config directory or a configured media dir)."
        )
    if not path.exists():
        return f"File '{raw_path}' does not exist"
    if not path.is_file():
        return f"'{raw_path}' is not a file"

    size = path.stat().st_size
    if size > _MAX_IMAGE_BYTES:
        return f"Image '{raw_path}' is too large ({size} bytes, max {_MAX_IMAGE_BYTES})"

    return path.read_bytes()
