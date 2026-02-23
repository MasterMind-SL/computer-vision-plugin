"""MCP tools for screen capture: window, desktop, and region screenshots."""

from __future__ import annotations

import json
import logging

from mcp.types import ImageContent, TextContent

from src.errors import (
    CAPTURE_FAILED,
    INVALID_COORDINATES,
    WINDOW_NOT_FOUND,
    make_error,
    CVPluginError,
)
from src.coordinates import validate_coordinates
from src.server import mcp
from src.utils.screenshot import capture_desktop, capture_region, capture_window
from src.models import ScreenshotResult

logger = logging.getLogger(__name__)


def _make_image_response(result: ScreenshotResult) -> list[ImageContent | TextContent]:
    """Build MCP content blocks from a ScreenshotResult.

    Returns [ImageContent, TextContent] so Claude sees the image natively
    and also receives structured metadata (rect, DPI, resolution) for
    coordinate math.

    NOTE: Use ImageContent directly with the existing base64 string from
    encode_image(). Do NOT use FastMCP's Image wrapper — it accepts raw
    bytes and would double-encode the already-base64 data.
    """
    metadata = {
        "success": True,
        "rect": result.rect.model_dump(),
        "physical_resolution": result.physical_resolution,
        "logical_resolution": result.logical_resolution,
        "dpi_scale": result.dpi_scale,
        "format": result.format,
    }
    return [
        ImageContent(type="image", data=result.image_base64, mimeType=f"image/{result.format}"),
        TextContent(type="text", text=json.dumps(metadata)),
    ]


def _make_error_response(error_dict: dict) -> list[TextContent]:
    """Wrap an error dict as a TextContent list for consistent return type."""
    return [TextContent(type="text", text=json.dumps(error_dict))]


@mcp.tool()
def cv_screenshot_window(hwnd: int, max_width: int = 1280) -> list[ImageContent | TextContent]:
    """Capture a screenshot of a specific window.

    Returns the screenshot as a native image visible to Claude, plus
    window geometry and DPI metadata as structured JSON.

    Args:
        hwnd: The window handle to capture.
        max_width: Maximum width in pixels for the output image. Default 1280.
    """
    try:
        result = capture_window(hwnd, max_width=max_width)
        return _make_image_response(result)
    except CVPluginError as exc:
        return _make_error_response(exc.to_dict())
    except Exception as exc:
        logger.error("cv_screenshot_window failed: %s", exc)
        return _make_error_response(make_error(CAPTURE_FAILED, f"Failed to capture window HWND {hwnd}: {exc}"))


@mcp.tool()
def cv_screenshot_desktop(max_width: int = 1920) -> list[ImageContent | TextContent]:
    """Capture a screenshot of the entire virtual desktop (all monitors).

    Returns the screenshot as a native image visible to Claude, plus
    desktop geometry metadata as structured JSON.

    Args:
        max_width: Maximum width in pixels for the output image. Default 1920.
    """
    try:
        result = capture_desktop(max_width=max_width)
        return _make_image_response(result)
    except CVPluginError as exc:
        return _make_error_response(exc.to_dict())
    except Exception as exc:
        logger.error("cv_screenshot_desktop failed: %s", exc)
        return _make_error_response(make_error(CAPTURE_FAILED, f"Failed to capture desktop: {exc}"))


@mcp.tool()
def cv_screenshot_region(x0: int, y0: int, x1: int, y1: int, max_width: int = 1280) -> list[ImageContent | TextContent]:
    """Capture a screenshot of a rectangular screen region.

    Coordinates are screen-absolute pixels. (x0,y0) is the top-left corner,
    (x1,y1) is the bottom-right corner.

    Args:
        x0: Left edge X coordinate.
        y0: Top edge Y coordinate.
        x1: Right edge X coordinate.
        y1: Bottom edge Y coordinate.
        max_width: Maximum width in pixels for the output image. Default 1280.
    """
    try:
        # Validate that corners are within the virtual desktop
        if not validate_coordinates(x0, y0):
            return _make_error_response(make_error(
                INVALID_COORDINATES,
                f"Top-left corner ({x0}, {y0}) is outside the virtual desktop",
            ))
        if not validate_coordinates(x1 - 1, y1 - 1):
            return _make_error_response(make_error(
                INVALID_COORDINATES,
                f"Bottom-right corner ({x1}, {y1}) is outside the virtual desktop",
            ))

        result = capture_region(x0, y0, x1, y1, max_width=max_width)
        return _make_image_response(result)
    except CVPluginError as exc:
        return _make_error_response(exc.to_dict())
    except Exception as exc:
        logger.error("cv_screenshot_region failed: %s", exc)
        return _make_error_response(make_error(CAPTURE_FAILED, f"Failed to capture region: {exc}"))
