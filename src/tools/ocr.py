"""MCP tool for OCR text extraction using winocr (primary) with pytesseract fallback."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from PIL import Image

from src.server import mcp
from src.errors import make_error, make_success, OCR_UNAVAILABLE, INVALID_INPUT, CAPTURE_FAILED
from src.utils.security import redact_ocr_output
from src.utils.screenshot import capture_window_raw, capture_region

logger = logging.getLogger(__name__)


def _ocr_winocr(image: Image.Image) -> tuple[str, list[dict[str, Any]]]:
    """Run OCR using winocr (Windows built-in OCR via UWP API).

    Returns (full_text, regions) where each region is {text, bbox}.
    Raises ImportError or RuntimeError on failure.
    """
    import winocr
    import asyncio

    # Try OCR languages in preference order; the first that succeeds wins.
    # Windows ships with locale-specific OCR packs (e.g. es-MX, pt-BR) and may
    # not have en-US installed.  We try common languages until one works.
    _LANGS = ["en", "es", "es-MX", "pt", "fr", "de", "it", "ja", "zh-Hans", "ko"]

    async def _run(img, lang):
        return await winocr.recognize_pil(img, lang=lang)

    def _sync_run(img, lang):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _run(img, lang)).result()
        else:
            return asyncio.run(_run(img, lang))

    result = None
    for lang in _LANGS:
        try:
            result = _sync_run(image, lang)
            logger.info("OCR succeeded with language: %s", lang)
            break
        except Exception:
            continue

    if result is None:
        raise RuntimeError(
            "No working OCR language found. Install a language pack: "
            "Add-WindowsCapability -Online -Name 'Language.OCR~~~en-US~0.0.1.0'"
        )

    lines = result.lines if hasattr(result, "lines") else []
    full_text_parts: list[str] = []
    regions: list[dict[str, Any]] = []

    for line in lines:
        text = line.text if hasattr(line, "text") else str(line)
        full_text_parts.append(text)

        bbox_dict: dict[str, int] = {}
        if hasattr(line, "x"):
            bbox_dict = {"x": int(line.x), "y": int(line.y), "width": int(line.width), "height": int(line.height)}
        elif hasattr(line, "bbox"):
            b = line.bbox
            if isinstance(b, dict):
                bbox_dict = b
            else:
                bbox_dict = {"x": int(b[0]), "y": int(b[1]), "width": int(b[2]), "height": int(b[3])}

        regions.append({"text": text, "bbox": bbox_dict})

    full_text = "\n".join(full_text_parts)
    return full_text, regions


def _ocr_pytesseract(image: Image.Image) -> tuple[str, list[dict[str, Any]]]:
    """Run OCR using pytesseract fallback.

    Returns (full_text, regions).
    Raises ImportError if pytesseract is not installed.
    """
    import pytesseract

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    full_text_parts: list[str] = []
    regions: list[dict[str, Any]] = []

    n_boxes = len(data.get("text", []))
    for i in range(n_boxes):
        text = data["text"][i].strip()
        if not text:
            continue
        full_text_parts.append(text)
        regions.append({
            "text": text,
            "bbox": {
                "x": data["left"][i],
                "y": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
            },
        })

    full_text = " ".join(full_text_parts)
    return full_text, regions


@mcp.tool()
def cv_ocr(
    hwnd: int | None = None,
    x0: int | None = None,
    y0: int | None = None,
    x1: int | None = None,
    y1: int | None = None,
    image_base64: str | None = None,
) -> dict:
    """Extract text from a screenshot using OCR.

    Provide one of:
    - image_base64: a base64-encoded image to OCR directly.
    - hwnd: a window handle to capture and OCR.
    - x0, y0, x1, y1: a screen region to capture and OCR.
    If none provided, returns an error.

    Args:
        hwnd: Window handle to capture and OCR.
        x0: Left edge of region to capture.
        y0: Top edge of region to capture.
        x1: Right edge of region to capture.
        y1: Bottom edge of region to capture.
        image_base64: Base64-encoded image to OCR directly.
    """
    try:
        image: Image.Image | None = None

        # Resolve image source
        if image_base64:
            try:
                raw = base64.b64decode(image_base64)
                image = Image.open(io.BytesIO(raw))
            except Exception as e:
                return make_error(INVALID_INPUT, f"Failed to decode base64 image: {e}")

        elif hwnd is not None:
            image = capture_window_raw(hwnd)
            if image is None:
                return make_error(CAPTURE_FAILED, f"Failed to capture window HWND={hwnd}")

        elif all(v is not None for v in (x0, y0, x1, y1)):
            try:
                result = capture_region(x0, y0, x1, y1)
                raw = base64.b64decode(result.image_base64)
                image = Image.open(io.BytesIO(raw))
            except Exception as e:
                return make_error(CAPTURE_FAILED, f"Failed to capture region: {e}")
        else:
            return make_error(
                INVALID_INPUT,
                "Provide one of: image_base64, hwnd, or (x0, y0, x1, y1) region coordinates.",
            )

        # Run OCR: try winocr first, then pytesseract fallback
        engine = "winocr"
        full_text = ""
        regions: list[dict[str, Any]] = []

        try:
            full_text, regions = _ocr_winocr(image)
        except ImportError:
            logger.info("winocr not available, trying pytesseract fallback")
            try:
                full_text, regions = _ocr_pytesseract(image)
                engine = "pytesseract"
            except ImportError:
                return make_error(
                    OCR_UNAVAILABLE,
                    "No OCR engine available. Install winocr (pip install winocr) or pytesseract.",
                )
        except Exception as e:
            logger.warning("winocr failed (%s), trying pytesseract fallback", e)
            try:
                full_text, regions = _ocr_pytesseract(image)
                engine = "pytesseract"
            except ImportError:
                return make_error(
                    OCR_UNAVAILABLE,
                    f"winocr failed ({e}) and pytesseract is not installed.",
                )
            except Exception as e2:
                return make_error(OCR_UNAVAILABLE, f"Both OCR engines failed: winocr={e}, pytesseract={e2}")

        # Apply redaction
        full_text, regions = redact_ocr_output(full_text, regions)

        return make_success(
            text=full_text,
            regions=regions,
            engine=engine,
        )

    except Exception as e:
        return make_error(OCR_UNAVAILABLE, str(e))
