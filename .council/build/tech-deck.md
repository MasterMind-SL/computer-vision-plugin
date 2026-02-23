# Tech Deck: Desktop Computer Vision Plugin for Claude Code

## 1. Technology Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| MCP Framework | `mcp>=1.26.0` (FastMCP) | Standard Claude Code plugin protocol, stdio transport, `@mcp.tool()` decorator |
| Build System | `uv` + `hatchling` | Matches the-council pattern; single `uv sync` install |
| Screen Capture | `mss` | DXGI Desktop Duplication on Windows — 15-30ms per frame, cross-monitor |
| Window Management | `pywin32` (win32gui, win32api, win32process, win32con) | Native Win32 API for EnumWindows, SetForegroundWindow, MoveWindow |
| Input Injection | `ctypes` (stdlib) | Direct SendInput MOUSEINPUT/KEYBDINPUT; zero external deps, most reliable |
| Image Processing | `Pillow` | Resize/encode/format conversion; used at MCP response boundary only |
| OCR Primary | `winocr` | Windows.Media.Ocr via WinRT; zero binaries, GPU-accelerated, ships with OS |
| OCR Fallback | `pytesseract` (optional) | For edge cases where WinRT unavailable; requires tesseract.exe on PATH |
| UI Automation | `comtypes` + UIAutomationCore | Direct COM access to Windows UIA; lighter than pywinauto |
| Input Validation | `pydantic` | Strict type checking, range validation on all tool inputs |
| DPI Handling | `ctypes` (user32/shcore) | SetProcessDpiAwarenessContext, GetDpiForMonitor |
| Python | 3.11+ | Match pyproject.toml standard |

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                     Claude Code                          │
│              (stdio JSON-RPC over MCP)                   │
└──────────────────┬───────────────────────────────────────┘
                   │ stdin/stdout
┌──────────────────▼───────────────────────────────────────┐
│              FastMCP Server (server.py)                   │
│         @mcp.tool() registrations for 14 tools           │
├──────────────────────────────────────────────────────────┤
│                    SECURITY GATE                         │
│  security.py: restricted_processes, dry_run, rate_limit, │
│  HWND freshness, action logging                          │
├──────────┬──────────┬──────────┬─────────┬──────────────┤
│ tools/   │ tools/   │ tools/   │ tools/  │ tools/       │
│windows.py│capture.py│input_*.py│ ocr.py  │accessibility │
│ F1,F5,F9 │F2,F3,F4  │ F6,F7,F8 │  F10   │F11,F12,F13  │
├──────────┴──────────┴──────────┴─────────┴──────────────┤
│                  UTILITY LAYER (utils/)                   │
│  screenshot.py | win32_input.py | win32_window.py        │
│  security.py   | uia.py                                  │
├──────────────────────────────────────────────────────────┤
│               CROSS-CUTTING (src/ root)                  │
│  dpi.py | coordinates.py | errors.py | models.py         │
│  config.py                                               │
├──────────────────────────────────────────────────────────┤
│                  Win32 API / mss / winocr                │
│                   (Operating System)                      │
└──────────────────────────────────────────────────────────┘
```

Data flow for every tool call:
1. Claude Code sends JSON-RPC request via stdin
2. FastMCP dispatches to `@mcp.tool()` handler
3. Pydantic validates input parameters
4. Security gate: check restricted_processes, rate limit, dry_run, HWND freshness
5. Tool function calls utils layer
6. Utils layer calls Win32 API / mss / winocr
7. Structured JSON response returned via stdout

## 3. Component Design

### 3a. Entry Layer

**`src/__main__.py`** — Calls `dpi.init_dpi_awareness()` first, then `mcp.run(transport="stdio")`. DPI must be set before any Win32 API call.

**`src/server.py`** — Creates `FastMCP("computer-vision")` instance. Imports and registers all tool functions from `src/tools/*`. Pure registration file — no business logic.

**Constraint:** The server MUST only accept stdio transport. HTTP/SSE transport MUST NOT be implemented or configurable.

### 3b. Tool Layer (`src/tools/`)

| Tool Name | File | Parameters | Response |
|-----------|------|-----------|----------|
| `cv_list_windows` | windows.py | `include_children: bool = False` | `{windows: [{hwnd, title, process_name, class_name, pid, rect, monitor_index, is_minimized, is_maximized, is_foreground}]}` |
| `cv_screenshot_window` | capture.py | `hwnd: int, max_width: int = 1280` | `{image: base64, rect, physical_res, logical_res, dpi_scale, format}` |
| `cv_screenshot_desktop` | capture.py | `max_width: int = 1920, quality: int = 95` | `{image: base64, monitors, virtual_rect, format}` |
| `cv_screenshot_region` | capture.py | `x0, y0, x1, y1: int` | `{image: base64, region, format}` |
| `cv_focus_window` | windows.py | `hwnd: int` | `{success: bool, hwnd, was_minimized}` |
| `cv_mouse_click` | input_mouse.py | `x, y: int, button: str = "left", click_type: str = "single", hwnd: int? = None, coordinate_space: str = "screen_absolute", start_x: int? = None, start_y: int? = None` | `{success: bool, position: {x, y}, button, click_type}` |
| `cv_type_text` | input_keyboard.py | `text: str` (max 1000 chars) | `{success: bool, length: int}` |
| `cv_send_keys` | input_keyboard.py | `keys: str` (e.g., "ctrl+s", "alt+tab") | `{success: bool, keys: str}` |
| `cv_move_window` | windows.py | `hwnd: int, x: int?, y: int?, width: int?, height: int?, action: str?` | `{success: bool, new_rect}` |
| `cv_ocr` | ocr.py | `hwnd: int? = None, region: {x0,y0,x1,y1}? = None, image_base64: str? = None` | `{text: str, regions: [{text, bbox: {x, y, w, h}}], engine: str}` |
| `cv_list_monitors` | monitors.py | (none) | `{monitors: [{index, name, rect, work_area, dpi, scale_factor, is_primary}]}` |
| `cv_read_ui` | accessibility.py | `hwnd: int, depth: int = 5, filter: str = "all"` | `{elements: [{name, control_type, rect, value, is_enabled, is_interactive, ref_id}]}` |
| `cv_wait_for_window` | synchronization.py | `title_pattern: str, timeout: float = 10.0` | `{found: bool, hwnd: int?, title: str?}` |
| `cv_wait` | synchronization.py | `seconds: float` (max 30) | `{waited: float}` |

### 3c. Utility Layer (`src/utils/`)

**`screenshot.py`** — `capture_window(hwnd, max_width)`, `capture_desktop(max_width)`, `capture_region(rect)`. Uses mss as primary, PrintWindow via pywin32 as fallback for occluded windows. Returns PIL Image internally — base64 encoding happens only at MCP response boundary. This lets OCR consume images without decode-encode round-trips.

**`win32_input.py`** — Defines ctypes structs (INPUT, MOUSEINPUT, KEYBDINPUT) once. Provides: `send_mouse_click(x, y, button, click_type)`, `send_mouse_drag(start, end, button)`, `type_unicode_string(text)`, `send_key_combo(keys)`. Batches all events into single SendInput calls for performance.

**`win32_window.py`** — `enum_windows(include_children)`, `get_window_info(hwnd)`, `focus_window(hwnd)`, `move_window(hwnd, rect)`, `is_window_valid(hwnd)`. Wraps pywin32 with error handling.

**`security.py`** — Central security gate:
- `check_restricted(pid, process_name)` — blocks interaction with restricted processes
- `validate_hwnd_fresh(hwnd, expected_pid, expected_title)` — TOCTOU prevention
- `log_action(tool, params, result)` — structured JSON audit log
- `guard_dry_run(tool, params)` — returns planned action without executing
- `check_rate_limit(tool)` — max 20 input actions/second

**`uia.py`** — Initializes CUIAutomation COM object via `comtypes.CoCreateInstance`. Provides `get_ui_tree(hwnd, depth, filter)` that walks with IUIAutomationTreeWalker and returns list of element dicts.

### 3d. Cross-Cutting Modules (`src/` root)

**`dpi.py`** — `init_dpi_awareness()` calls `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` via ctypes at startup. Provides `get_monitor_dpi(hmonitor)`, `physical_to_logical(x, y, dpi)`, `logical_to_physical(x, y, dpi)`.

**`coordinates.py`** — `to_screen_absolute(x, y, hwnd)` converts window-relative to screen-absolute using `GetWindowRect`. `to_window_relative(x, y, hwnd)` does the inverse. `normalize_for_sendinput(x, y)` converts to 0-65535 range for MOUSEINPUT absolute coordinates.

**`errors.py`** — Error code constants: `WINDOW_NOT_FOUND`, `WINDOW_MINIMIZED`, `ACCESS_DENIED`, `SCREEN_LOCKED`, `TIMEOUT`, `INVALID_COORDINATES`, `OCR_UNAVAILABLE`, `RATE_LIMITED`, `DRY_RUN`. Factory: `make_error(code, message) -> dict`. All tools catch exceptions and convert to structured errors.

**`models.py`** — Pydantic models for input validation and response typing: `WindowInfo`, `MonitorInfo`, `OcrRegion`, `UiaElement`, `ScreenshotResult`, `ClickParams`, `KeyboardParams`. Strict type checking, range validation (coordinates within virtual desktop, HWND as positive int, timeout capped at 60s).

**`config.py`** — Loads from environment variables with defaults:
- `CV_RESTRICTED_PROCESSES` — comma-separated process names (default: `"credential manager,keepass,1password,bitwarden,windows security"`)
- `CV_DRY_RUN` — bool, default False
- `CV_DEFAULT_MAX_WIDTH` — int, default 1280
- `CV_MAX_TEXT_LENGTH` — int, default 1000
- `CV_RATE_LIMIT` — int, max actions/sec, default 20
- `CV_AUDIT_LOG_PATH` — path, default `%LOCALAPPDATA%/claude-cv-plugin/audit.jsonl`
- `CV_OCR_REDACTION_PATTERNS` — comma-separated regex patterns for redacting sensitive text in OCR output

## 4. Data Models

### WindowInfo
```python
@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process_name: str
    class_name: str
    pid: int
    rect: dict  # {x, y, width, height}
    monitor_index: int
    is_minimized: bool
    is_maximized: bool
    is_foreground: bool
```

### MonitorInfo
```python
@dataclass
class MonitorInfo:
    index: int
    name: str
    rect: dict       # {x, y, width, height}
    work_area: dict   # {x, y, width, height}
    dpi: int
    scale_factor: float
    is_primary: bool
```

### ScreenshotResult
```python
@dataclass
class ScreenshotResult:
    image_base64: str
    rect: dict            # {x, y, width, height}
    physical_resolution: dict  # {width, height}
    logical_resolution: dict   # {width, height}
    dpi_scale: float
    format: str           # "png" or "jpeg"
```

### OcrRegion
```python
@dataclass
class OcrRegion:
    text: str
    bbox: dict  # {x, y, width, height}
    confidence: float
```

### UiaElement
```python
@dataclass
class UiaElement:
    ref_id: str          # unique reference for this element
    name: str
    control_type: str    # "Button", "Edit", "ComboBox", etc.
    rect: dict           # {x, y, width, height}
    value: str | None
    is_enabled: bool
    is_interactive: bool
    children: list       # nested UiaElements up to depth
```

## 5. API Contracts

### Standard Response Envelope

**Success:**
```json
{
  "success": true,
  "<payload_key>": "<payload_value>"
}
```

**Error:**
```json
{
  "success": false,
  "error": {
    "code": "WINDOW_NOT_FOUND",
    "message": "Window with HWND 12345 no longer exists"
  }
}
```

### Screenshot Response (MCP Content)
Screenshots are returned as MCP image content:
```json
{
  "success": true,
  "image": "<base64-encoded PNG/JPEG>",
  "metadata": {
    "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    "physical_resolution": {"width": 1920, "height": 1080},
    "logical_resolution": {"width": 1280, "height": 720},
    "dpi_scale": 1.5,
    "format": "png"
  }
}
```

### OCR Response
```json
{
  "success": true,
  "text": "Full extracted text here",
  "regions": [
    {"text": "File", "bbox": {"x": 10, "y": 5, "width": 30, "height": 16}},
    {"text": "Edit", "bbox": {"x": 45, "y": 5, "width": 30, "height": 16}}
  ],
  "engine": "winocr"
}
```

## 6. Security Architecture

### Transport Security
- Server MUST only accept stdio transport — no HTTP/SSE binding
- Validate at startup that communication is via stdin/stdout pipes

### Input Validation
- All tool inputs validated via Pydantic models with strict types
- Coordinates validated against virtual desktop bounds
- HWND validated as positive integer
- Timeouts capped at 60 seconds
- Text input capped at 1000 characters (configurable)
- Key sequences validated against allowed key names

### Access Control
- **Restricted processes**: Configurable blocklist with sensible defaults (credential managers, password vaults, Windows Security)
- **HWND freshness**: Before any input injection, re-verify the target HWND maps to the expected process/title (TOCTOU prevention)
- **Rate limiting**: Max 20 input actions/second to prevent runaway automation
- **Dry-run mode**: All mutating tools support `dry_run` parameter returning planned action without executing

### Audit Logging
- Structured JSON log to `%LOCALAPPDATA%/claude-cv-plugin/audit.jsonl`
- Each entry: timestamp, tool_name, target_hwnd, target_process, parameters (sanitized — text logged as `[TEXT len=N]`), result_status
- Log rotation: 10MB max, 5 files retained

### OCR Redaction
- Configurable regex patterns (`CV_OCR_REDACTION_PATTERNS`) applied to OCR output before returning
- Matched text replaced with `[REDACTED]`

## 7. Testing Strategy

### Unit Tests (`tests/unit/`)
- Mock all Win32 API calls via `unittest.mock.patch`
- Test: coordinate transforms, DPI math, image encoding, error handling, key parsing, struct construction, security checks, config loading
- Target: 85%+ coverage on `src/` modules
- Framework: pytest

### Integration Tests (`tests/integration/`)
- Spin up known window (Notepad via subprocess)
- Test full workflows: enumerate → find Notepad → capture → focus → type text → send Ctrl+A → OCR verify
- Requires real Windows desktop (GitHub Actions `windows-latest`)

### Performance Benchmarks (`tests/benchmarks/`)
- pytest-benchmark for capture latency, enumeration latency, input injection latency
- CI gate: capture < 500ms, enumeration < 100ms

### Test Structure
```
tests/
├── conftest.py          # Shared fixtures (mock hwnds, sample images, monitor geometries)
├── unit/
│   ├── test_windows.py
│   ├── test_capture.py
│   ├── test_input.py
│   ├── test_ocr.py
│   ├── test_accessibility.py
│   ├── test_coordinates.py
│   ├── test_dpi.py
│   ├── test_security.py
│   └── test_config.py
├── integration/
│   ├── test_full_workflow.py
│   └── test_multi_monitor.py
└── benchmarks/
    └── test_performance.py
```

## 8. Deployment & Infrastructure

### Plugin Manifest (`.claude-plugin/plugin.json`)
```json
{
  "name": "computer-vision",
  "version": "1.0.0",
  "description": "Desktop computer vision and input control for Claude Code on Windows",
  "author": {"name": "Computer Vision Plugin"},
  "license": "MIT",
  "keywords": ["computer-vision", "windows", "desktop", "automation", "mcp"]
}
```

### MCP Configuration (`.mcp.json`)
```json
{
  "mcpServers": {
    "computer-vision": {
      "command": "uv",
      "args": ["run", "--directory", "${CLAUDE_PLUGIN_ROOT}", "python", "-m", "src.server"],
      "cwd": "${CLAUDE_PLUGIN_ROOT}"
    }
  }
}
```

### Dependencies (`pyproject.toml`)
```toml
[project]
name = "computer-vision-claude-code"
version = "1.0.0"
description = "Desktop computer vision and input control for Claude Code"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.26.0",
    "mss>=9.0.0",
    "pywin32>=306",
    "Pillow>=10.0.0",
    "winocr>=0.2.0",
    "comtypes>=1.4.0",
    "pydantic>=2.0.0",
]

[project.optional-dependencies]
ocr-fallback = ["pytesseract>=0.3.10"]
dev = ["pytest>=8.0", "pytest-benchmark>=4.0", "ruff>=0.5.0", "mypy>=1.10"]

[tool.hatch.build.targets.wheel]
packages = ["src"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Install & Run
```bash
cd <plugin-directory>
uv sync                    # Install all dependencies
# Plugin auto-starts via Claude Code's MCP server management
```

## 9. File/Directory Structure (Final)

```
computer-vision-claude-code/
├── .claude-plugin/
│   └── plugin.json
├── .mcp.json
├── pyproject.toml
├── CLAUDE.md
├── skills/
│   ├── cv-setup/
│   │   └── SKILL.md
│   └── cv-help/
│       └── SKILL.md
├── src/
│   ├── __init__.py
│   ├── __main__.py
│   ├── server.py
│   ├── config.py
│   ├── errors.py
│   ├── models.py
│   ├── dpi.py
│   ├── coordinates.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── windows.py          # F1, F5, F9
│   │   ├── capture.py          # F2, F3, F4
│   │   ├── input_mouse.py      # F6
│   │   ├── input_keyboard.py   # F7, F8
│   │   ├── ocr.py              # F10
│   │   ├── monitors.py         # F11
│   │   ├── accessibility.py    # F12
│   │   └── synchronization.py  # F13
│   └── utils/
│       ├── __init__.py
│       ├── screenshot.py
│       ├── win32_input.py
│       ├── win32_window.py
│       ├── security.py
│       └── uia.py
└── tests/
    ├── conftest.py
    ├── unit/
    │   ├── test_windows.py
    │   ├── test_capture.py
    │   ├── test_input.py
    │   ├── test_ocr.py
    │   ├── test_accessibility.py
    │   ├── test_coordinates.py
    │   ├── test_dpi.py
    │   ├── test_security.py
    │   └── test_config.py
    ├── integration/
    │   ├── test_full_workflow.py
    │   └── test_multi_monitor.py
    └── benchmarks/
        └── test_performance.py
```
