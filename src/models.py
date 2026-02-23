"""Pydantic models for structured data throughout the CV plugin."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Rect(BaseModel):
    """A rectangle in screen coordinates."""
    x: int
    y: int
    width: int
    height: int


class Point(BaseModel):
    """A point in screen coordinates."""
    x: int
    y: int


class WindowInfo(BaseModel):
    """Information about a single window."""
    hwnd: int
    title: str
    process_name: str
    class_name: str
    pid: int
    rect: Rect
    monitor_index: int = 0
    is_minimized: bool = False
    is_maximized: bool = False
    is_foreground: bool = False


class MonitorInfo(BaseModel):
    """Information about a single monitor."""
    index: int
    name: str
    rect: Rect
    work_area: Rect
    dpi: int
    scale_factor: float
    is_primary: bool


class ScreenshotResult(BaseModel):
    """Result of a screenshot capture operation."""
    image_base64: str
    rect: Rect
    physical_resolution: dict[str, int] = Field(default_factory=dict)
    logical_resolution: dict[str, int] = Field(default_factory=dict)
    dpi_scale: float = 1.0
    format: str = "png"


class OcrRegion(BaseModel):
    """A single OCR-detected text region."""
    text: str
    bbox: Rect
    confidence: float = 0.0


class OcrResult(BaseModel):
    """Full OCR extraction result."""
    text: str
    regions: list[OcrRegion] = Field(default_factory=list)
    engine: str = "winocr"


class UiaElement(BaseModel):
    """A single UI Automation accessibility element."""
    ref_id: str
    name: str
    control_type: str
    rect: Rect
    value: str | None = None
    is_enabled: bool = True
    is_interactive: bool = False
    children: list[UiaElement] = Field(default_factory=list)


class ClickParams(BaseModel):
    """Parameters for a mouse click action."""
    x: int
    y: int
    button: str = "left"
    click_type: str = "single"
    hwnd: int | None = None
    coordinate_space: str = "screen_absolute"
    start_x: int | None = None
    start_y: int | None = None


class KeyboardParams(BaseModel):
    """Parameters for keyboard input."""
    text: str = ""
    keys: str = ""
    max_length: int = 1000
