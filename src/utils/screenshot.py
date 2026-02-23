"""Screen capture utilities using mss with PrintWindow fallback."""

from __future__ import annotations

import base64
import ctypes
import io
import logging
from typing import Any

import mss
import win32gui
import win32ui
import win32con
from PIL import Image

from src.dpi import get_window_dpi, get_scale_factor
from src.errors import WindowNotFoundError, CVPluginError, CAPTURE_FAILED
from src.models import Rect, ScreenshotResult
from src.utils.win32_window import is_window_valid

logger = logging.getLogger(__name__)


def capture_window(hwnd: int, max_width: int = 1280) -> ScreenshotResult:
    """Capture a specific window by HWND.

    Uses mss to capture the window's screen region. Falls back to PrintWindow
    for occluded or off-screen windows.

    Args:
        hwnd: Window handle to capture.
        max_width: Maximum width for downscaling. Default 1280.

    Returns:
        ScreenshotResult with base64-encoded image and metadata.
    """
    if not is_window_valid(hwnd):
        raise WindowNotFoundError(hwnd)

    rect_tuple = win32gui.GetWindowRect(hwnd)
    left, top, right, bottom = rect_tuple
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        raise CVPluginError(CAPTURE_FAILED, f"Window HWND {hwnd} has zero size")

    # Try mss first (fast, but only works for visible screen regions)
    img = _capture_region_mss(left, top, width, height)

    if img is None:
        # Fallback to PrintWindow for occluded/off-screen windows
        img = _capture_with_printwindow(hwnd, width, height)

    if img is None:
        raise CVPluginError(CAPTURE_FAILED, f"Failed to capture window HWND {hwnd}")

    dpi = get_window_dpi(hwnd)
    scale = get_scale_factor(dpi)

    b64 = encode_image(img, max_width=max_width)

    return ScreenshotResult(
        image_base64=b64,
        rect=Rect(x=left, y=top, width=width, height=height),
        physical_resolution={"width": img.width, "height": img.height},
        logical_resolution={
            "width": int(img.width / scale),
            "height": int(img.height / scale),
        },
        dpi_scale=scale,
        format="png",
    )


def capture_desktop(max_width: int = 1920) -> ScreenshotResult:
    """Capture the entire virtual desktop across all monitors.

    Args:
        max_width: Maximum width for downscaling. Default 1920.

    Returns:
        ScreenshotResult with base64-encoded image and metadata.
    """
    with mss.mss() as sct:
        # monitors[0] is the entire virtual desktop
        monitor = sct.monitors[0]
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    b64 = encode_image(img, max_width=max_width)

    return ScreenshotResult(
        image_base64=b64,
        rect=Rect(
            x=monitor["left"],
            y=monitor["top"],
            width=monitor["width"],
            height=monitor["height"],
        ),
        physical_resolution={"width": img.width, "height": img.height},
        logical_resolution={"width": img.width, "height": img.height},
        dpi_scale=1.0,
        format="png",
    )


def capture_region(x0: int, y0: int, x1: int, y1: int, max_width: int = 1280) -> ScreenshotResult:
    """Capture an arbitrary rectangular region of the screen.

    Args:
        x0: Left edge (screen-absolute).
        y0: Top edge (screen-absolute).
        x1: Right edge (screen-absolute).
        y1: Bottom edge (screen-absolute).
        max_width: Maximum width for downscaling.

    Returns:
        ScreenshotResult with base64-encoded image and metadata.
    """
    width = x1 - x0
    height = y1 - y0

    if width <= 0 or height <= 0:
        raise CVPluginError(
            CAPTURE_FAILED,
            f"Invalid region: ({x0},{y0})-({x1},{y1}) yields {width}x{height}",
        )

    region = {"left": x0, "top": y0, "width": width, "height": height}

    with mss.mss() as sct:
        screenshot = sct.grab(region)
        img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    b64 = encode_image(img, max_width=max_width)

    return ScreenshotResult(
        image_base64=b64,
        rect=Rect(x=x0, y=y0, width=width, height=height),
        physical_resolution={"width": img.width, "height": img.height},
        logical_resolution={"width": img.width, "height": img.height},
        dpi_scale=1.0,
        format="png",
    )


def capture_window_raw(hwnd: int) -> Image.Image | None:
    """Capture a window and return as PIL Image (no encoding).

    Used internally by OCR to avoid decode-encode round-trips.
    Returns None on failure.
    """
    if not is_window_valid(hwnd):
        return None

    try:
        rect_tuple = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect_tuple
        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            return None

        img = _capture_region_mss(left, top, width, height)
        if img is None:
            img = _capture_with_printwindow(hwnd, width, height)

        return img
    except Exception as exc:
        logger.debug("capture_window_raw failed for HWND %s: %s", hwnd, exc)
        return None


def capture_region_raw(x0: int, y0: int, x1: int, y1: int) -> Image.Image | None:
    """Capture a screen region and return as PIL Image (no encoding).

    Used internally by OCR to avoid encode-decode round-trips.
    Returns None on failure.
    """
    width = x1 - x0
    height = y1 - y0
    if width <= 0 or height <= 0:
        return None
    try:
        return _capture_region_mss(x0, y0, width, height)
    except Exception as exc:
        logger.debug("capture_region_raw failed for (%s,%s)-(%s,%s): %s", x0, y0, x1, y1, exc)
        return None


def _capture_region_mss(left: int, top: int, width: int, height: int) -> Image.Image | None:
    """Capture a screen region using mss. Returns PIL Image or None on failure."""
    try:
        region = {"left": left, "top": top, "width": width, "height": height}
        with mss.mss() as sct:
            screenshot = sct.grab(region)
            return Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    except Exception as exc:
        logger.debug("mss capture failed for region (%s,%s,%s,%s): %s", left, top, width, height, exc)
        return None


def _capture_with_printwindow(hwnd: int, width: int, height: int) -> Image.Image | None:
    """Capture a window using PrintWindow (works for occluded windows).

    Returns PIL Image or None on failure.
    """
    hdc_window = None
    hdc_mem = None
    bitmap = None
    try:
        hdc_window = win32gui.GetWindowDC(hwnd)
        hdc_mem = win32ui.CreateDCFromHandle(hdc_window)
        hdc_compat = hdc_mem.CreateCompatibleDC()

        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(hdc_mem, width, height)
        hdc_compat.SelectObject(bitmap)

        # PW_RENDERFULLCONTENT = 0x00000002 (captures DWM-composed content)
        PW_RENDERFULLCONTENT = 2
        result = ctypes.windll.user32.PrintWindow(hwnd, hdc_compat.GetSafeHdc(), PW_RENDERFULLCONTENT)

        if not result:
            # Fallback without PW_RENDERFULLCONTENT
            result = ctypes.windll.user32.PrintWindow(hwnd, hdc_compat.GetSafeHdc(), 0)

        if not result:
            return None

        bmp_info = bitmap.GetInfo()
        bmp_bits = bitmap.GetBitmapBits(True)

        img = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bits,
            "raw",
            "BGRX",
            0,
            1,
        )
        return img
    except Exception as exc:
        logger.debug("PrintWindow failed for HWND %s: %s", hwnd, exc)
        return None
    finally:
        if bitmap is not None:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass
        if hdc_mem is not None:
            try:
                hdc_mem.DeleteDC()
            except Exception:
                pass
        if hdc_window is not None:
            try:
                win32gui.ReleaseDC(hwnd, hdc_window)
            except Exception:
                pass


def encode_image(img: Image.Image, max_width: int = 1280, fmt: str = "png", quality: int = 95) -> str:
    """Downscale and encode a PIL Image to base64 string.

    Args:
        img: PIL Image to encode.
        max_width: Maximum width for downscaling.
        fmt: Image format ("png" or "jpeg").
        quality: JPEG quality (1-100). Ignored for PNG.

    Returns:
        Base64-encoded image string.
    """
    if img.width > max_width:
        ratio = max_width / img.width
        new_height = int(img.height * ratio)
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    if fmt.lower() == "jpeg":
        img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=quality)
    else:
        img.save(buffer, format="PNG")

    return base64.b64encode(buffer.getvalue()).decode("ascii")
