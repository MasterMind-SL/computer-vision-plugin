# Implementation Backlog: Desktop Computer Vision Plugin

## Foundation Tasks (Team Lead — must complete before parallel work)

### F-1. Project scaffold [S]
- Create full directory structure: `src/`, `src/tools/`, `src/utils/`, `tests/`, `tests/unit/`, `tests/integration/`, `tests/benchmarks/`, `.claude-plugin/`, `skills/cv-setup/`, `skills/cv-help/`
- Create ALL `__init__.py` files: `src/__init__.py`, `src/tools/__init__.py`, `src/utils/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/benchmarks/__init__.py`
- Create `pyproject.toml` with all dependencies (mcp>=1.26.0, mss>=9.0.0, pywin32>=306, Pillow>=10.0.0, winocr>=0.2.0, comtypes>=1.4.0, pydantic>=2.0.0) + dev extras (pytest, ruff, mypy)
- Create `.claude-plugin/plugin.json` (name: "computer-vision", version: "1.0.0")
- Create `.mcp.json` (command: `uv run --directory ${CLAUDE_PLUGIN_ROOT} python -m src`, NOT `python -m src.server`)
- Create `CLAUDE.md` with project overview, coding conventions (Pydantic BaseModel for all models, structured error responses, no HTTP transport), import patterns, testing instructions

### F-2. Cross-cutting modules (full implementation) [M]
- `src/errors.py` — Error code constants (WINDOW_NOT_FOUND, WINDOW_MINIMIZED, ACCESS_DENIED, SCREEN_LOCKED, TIMEOUT, INVALID_COORDINATES, OCR_UNAVAILABLE, RATE_LIMITED, DRY_RUN) + `make_error(code, message)` factory + `make_success(**payload)` factory
- `src/models.py` — Pydantic BaseModel classes (NOT dataclasses): `WindowInfo`, `MonitorInfo`, `ScreenshotResult`, `OcrRegion`, `UiaElement`, `Rect`, `Point`, `ClickParams`, `KeyboardParams`
- `src/config.py` — Settings from env vars with defaults: CV_RESTRICTED_PROCESSES (default: "credential manager,keepass,1password,bitwarden,windows security"), CV_DRY_RUN, CV_DEFAULT_MAX_WIDTH (1280), CV_MAX_TEXT_LENGTH (1000), CV_RATE_LIMIT (20), CV_AUDIT_LOG_PATH, CV_OCR_REDACTION_PATTERNS
- `src/dpi.py` — `init_dpi_awareness()` via ctypes SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2), `get_monitor_dpi(hmonitor)`, `physical_to_logical()`, `logical_to_physical()`
- `src/coordinates.py` — `to_screen_absolute(x, y, hwnd)`, `to_window_relative(x, y, hwnd)`, `normalize_for_sendinput(x, y)` (0-65535 range), `validate_coordinates(x, y)` against virtual desktop bounds

### F-3. Security utilities (full implementation) [M]
- `src/utils/security.py` — `check_restricted(pid, process_name)`, `validate_hwnd_fresh(hwnd, expected_pid, expected_title)` (TOCTOU prevention), `check_rate_limit(tool_name)` (token bucket, 20/sec), `log_action(tool, params, result)` (JSON lines to audit.jsonl, text logged as `[TEXT len=N]`), `guard_dry_run(tool, params)` decorator, `redact_ocr_output(text, regions, patterns)` for OCR redaction
- File: creates/rotates audit log at `%LOCALAPPDATA%/claude-cv-plugin/audit.jsonl`

### F-4. Utility module stubs (full signatures, type hints, docstrings, `pass` bodies) [M]
- `src/utils/screenshot.py` — `capture_window(hwnd: int, max_width: int = 1280) -> ScreenshotResult`, `capture_desktop(max_width: int = 1920) -> ScreenshotResult`, `capture_region(x0: int, y0: int, x1: int, y1: int) -> ScreenshotResult`, `encode_image(img: Image.Image, max_width: int, fmt: str) -> str`
- `src/utils/win32_input.py` — INPUT/MOUSEINPUT/KEYBDINPUT ctypes struct definitions, `send_mouse_click(x, y, button, click_type)`, `send_mouse_drag(start_x, start_y, end_x, end_y, button)`, `type_unicode_string(text: str)`, `send_key_combo(keys: str)`, VK code lookup table
- `src/utils/win32_window.py` — `enum_windows(include_children: bool = False) -> list[WindowInfo]`, `get_window_info(hwnd: int) -> WindowInfo`, `focus_window(hwnd: int) -> bool`, `move_window(hwnd: int, x, y, w, h) -> Rect`, `is_window_valid(hwnd: int) -> bool`
- `src/utils/uia.py` — `init_uia() -> CUIAutomation`, `get_ui_tree(hwnd: int, depth: int = 5, filter: str = "all") -> list[UiaElement]`

### F-5. FastMCP server with auto-registration [S]
- `src/server.py` — Create `FastMCP("computer-vision")` instance. Auto-import all tool modules from `src/tools/` so agents never need to edit server.py. Each tool file defines functions with `@mcp.tool()` decorators on the shared FastMCP instance imported from server.py.
- `src/__main__.py` — Import and call `dpi.init_dpi_awareness()`, then import `server.mcp` and call `mcp.run(transport="stdio")`. This is the entry point (`.mcp.json` runs `python -m src`).
- Verify: `uv run python -m src` starts and responds to MCP `initialize`.

### F-6. Skill files [S]
- `skills/cv-setup/SKILL.md` — Setup instructions: check uv, run uv sync, verify MCP server starts
- `skills/cv-help/SKILL.md` — Usage guide: list all 14 tools with brief descriptions and examples

### F-7. Test scaffolding [S]
- `tests/conftest.py` — Shared fixtures: mock HWND values, mock MonitorInfo list, sample PIL images, temp directories, mock security config
- Pytest config in `pyproject.toml`

**SYNC POINT SP-0:** All workstreams begin after F-5 passes smoke test (server starts, responds to MCP initialize).

---

## Workstream A: Windows, Monitors & Capture (dev-alpha)
**Features: F1, F2, F3, F4, F5, F9, F11**

### A-1. `utils/win32_window.py` full implementation [M]
- Replace stubs with real pywin32 calls: EnumWindows callback, GetWindowText, GetWindowRect, GetWindowThreadProcessId, GetClassName, MonitorFromWindow, OpenProcess + GetModuleFileNameEx for process name
- EnumChildWindows support for `include_children=True`
- `is_window_valid(hwnd)` using IsWindow
- **Creates:** `src/utils/win32_window.py` (full)

### A-2. `utils/screenshot.py` full implementation [M]
- mss capture: window (crop by rect), desktop (full virtual screen), region (explicit coords)
- PrintWindow fallback for occluded/off-screen windows via pywin32
- Pillow downscaling to max_width maintaining aspect ratio
- Base64 PNG encoding (and optional JPEG with quality param)
- Return PIL Image internally for OCR consumption, base64 only at MCP boundary
- DPI metadata in ScreenshotResult
- **Creates:** `src/utils/screenshot.py` (full)

### A-3. F11 — `tools/monitors.py` [M]
- `cv_list_monitors()`: EnumDisplayMonitors + GetMonitorInfo + GetDpiForMonitor
- Return list of MonitorInfo (index, name, rect, work_area, dpi, scale_factor, is_primary)
- Cross-reference with mss monitors for validation
- **Creates:** `src/tools/monitors.py`

### A-4. F1 — `tools/windows.py` (cv_list_windows) [M]
- `cv_list_windows(include_children: bool = False)`: calls utils/win32_window.py
- Filter to visible top-level windows, return list of WindowInfo
- **Creates:** `src/tools/windows.py` (partial — F1 only)

### A-5. F5 — `tools/windows.py` (cv_focus_window) [M]
- `cv_focus_window(hwnd: int)`: ShowWindow(SW_RESTORE) if minimized, AttachThreadInput + SetForegroundWindow + detach
- Security gate: check restricted process, validate HWND freshness, log action
- **Modifies:** `src/tools/windows.py` (adds F5)

### A-6. F9 — `tools/windows.py` (cv_move_window) [M]
- `cv_move_window(hwnd, x, y, width, height, action)`: MoveWindow for position/size, ShowWindow for maximize/minimize/restore
- Security gate: check restricted, validate HWND, log action
- **Modifies:** `src/tools/windows.py` (adds F9)

### A-7. F2 — `tools/capture.py` (cv_screenshot_window) [L]
- `cv_screenshot_window(hwnd: int, max_width: int = 1280)`: calls utils/screenshot.py
- mss primary, PrintWindow fallback
- Returns base64 PNG + metadata (rect, physical_res, logical_res, dpi_scale)
- **Creates:** `src/tools/capture.py` (partial — F2)

### A-8. F3 — `tools/capture.py` (cv_screenshot_desktop) [S]
- `cv_screenshot_desktop(max_width: int = 1920, quality: int = 95)`: mss full virtual screen
- **Modifies:** `src/tools/capture.py` (adds F3)

### A-9. F4 — `tools/capture.py` (cv_screenshot_region) [S]
- `cv_screenshot_region(x0, y0, x1, y1)`: mss explicit region, validate coordinates
- **Modifies:** `src/tools/capture.py` (adds F4)

### A-10. Unit tests for Workstream A [M]
- `tests/unit/test_windows.py` — mock EnumWindows, GetWindowText, focus/move
- `tests/unit/test_capture.py` — mock mss, PrintWindow, image encoding
- `tests/unit/test_monitors.py` — mock EnumDisplayMonitors, GetMonitorInfo
- **Creates:** 3 test files

**SYNC POINT SP-1:** After A-2 completes, notify WS-B that screenshot utility is ready for OCR (F10).

---

## Workstream B: Input & OCR (dev-beta)
**Features: F6, F7, F8, F10**

### B-1. `utils/win32_input.py` full implementation [M]
- Replace stubs with ctypes struct definitions: INPUT, MOUSEINPUT, KEYBDINPUT
- `send_mouse_click(x, y, button, click_type)`: normalize coords to 0-65535, build INPUT array, SendInput
- `send_mouse_drag(start_x, start_y, end_x, end_y, button)`: move + button_down + move + button_up
- `type_unicode_string(text)`: KEYEVENTF_UNICODE for each char, batch into single SendInput
- `send_key_combo(keys)`: parse "ctrl+shift+s" → VK codes, build modifier-down + key-down + key-up + modifier-up, SendInput
- VK code lookup table + VkKeyScanW for printable chars
- **Creates:** `src/utils/win32_input.py` (full)

### B-2. F6 — `tools/input_mouse.py` [L]
- `cv_mouse_click(x, y, button, click_type, hwnd, coordinate_space, start_x, start_y)`:
- If coordinate_space == "window_relative": convert via coordinates.py
- If hwnd provided: auto-focus window first (call focus_window)
- Security gate: check restricted, validate HWND freshness, rate limit, log, dry_run
- Support: left_click, right_click, double_click, middle_click, drag
- **Creates:** `src/tools/input_mouse.py`

### B-3. F7 — `tools/input_keyboard.py` (cv_type_text) [M]
- `cv_type_text(text: str)`: validate max 1000 chars, call type_unicode_string
- Security gate: rate limit, log (text as `[TEXT len=N]`), dry_run
- **Creates:** `src/tools/input_keyboard.py` (partial — F7)

### B-4. F8 — `tools/input_keyboard.py` (cv_send_keys) [M]
- `cv_send_keys(keys: str)`: parse key string, validate against allowed keys, call send_key_combo
- Security gate: rate limit, log, dry_run
- **Modifies:** `src/tools/input_keyboard.py` (adds F8)

### B-5. F10 — `tools/ocr.py` [L]
- `cv_ocr(hwnd, region, image_base64)`: capture screenshot (reuse utils/screenshot.py), run winocr primary (with sync wrapper + 5s timeout), pytesseract fallback
- Return text + regions with bounding boxes + engine name
- Call `security.redact_ocr_output()` before returning
- **BLOCKED BY SP-1** (needs utils/screenshot.py from WS-A)
- **Creates:** `src/tools/ocr.py`

### B-6. Unit tests for Workstream B [M]
- `tests/unit/test_input.py` — mock SendInput, test coordinate normalization, key parsing
- `tests/unit/test_ocr.py` — mock winocr/pytesseract, test redaction, test bounding boxes
- **Creates:** 2 test files

---

## Workstream C: Accessibility, Sync & Integration (dev-gamma)
**Features: F12, F13 + integration testing + final polish**

### C-1. `utils/uia.py` full implementation [M]
- Replace stubs with comtypes UIAutomationCore initialization
- `init_uia()`: CoCreateInstance for CUIAutomation, cache instance
- `get_ui_tree(hwnd, depth, filter)`: ElementFromHandle, CreateTreeWalker(CreateTrueCondition), recursive walk up to depth
- For each element: CurrentName, CurrentControlType, CurrentBoundingRectangle, CurrentIsEnabled, GetCurrentPropertyValue(ValueValuePropertyId)
- Filter "interactive": only Button, Edit, ComboBox, CheckBox, MenuItem, Link, Slider, Tab types
- Hard timeout (5s) to handle unresponsive apps (comtypes COM can hang)
- **Creates:** `src/utils/uia.py` (full)

### C-2. F12 — `tools/accessibility.py` [L]
- `cv_read_ui(hwnd: int, depth: int = 5, filter: str = "all")`: calls utils/uia.py
- Return structured element tree with ref_id, name, control_type, rect, value, is_enabled, is_interactive
- Security gate: check restricted process for target window
- **Creates:** `src/tools/accessibility.py`

### C-3. F13 — `tools/synchronization.py` [M]
- `cv_wait_for_window(title_pattern: str, timeout: float = 10.0)`: poll EnumWindows every 250ms, regex match on title, return on match or timeout
- `cv_wait(seconds: float)`: asyncio.sleep, capped at 30s max
- **Creates:** `src/tools/synchronization.py`

### C-4. Unit tests for Workstream C [M]
- `tests/unit/test_accessibility.py` — mock comtypes UIA, test tree walking, depth limiting, interactive filter
- `tests/unit/test_synchronization.py` — mock EnumWindows, test timeout behavior, regex matching
- `tests/unit/test_security.py` — test restricted process check, rate limiting, HWND freshness, dry_run, redaction
- `tests/unit/test_dpi.py` — test coordinate transforms, DPI scaling math
- `tests/unit/test_coordinates.py` — test window_relative/screen_absolute conversions
- `tests/unit/test_models.py` — test Pydantic validation edge cases, serialization
- `tests/unit/test_config.py` — test env var loading, defaults
- **Creates:** 7 test files

### C-5. Integration tests [L]
- **BLOCKED BY SP-2** (all WS-A and WS-B feature tasks complete)
- `tests/integration/test_full_workflow.py` — enumerate → find Notepad → capture → OCR → focus → type → send Ctrl+A → verify
- `tests/integration/test_multi_monitor.py` — monitor enumeration, cross-monitor capture
- `tests/integration/test_security.py` — restricted process blocking, rate limiting, dry-run mode
- **Creates:** 3 integration test files

### C-6. CLAUDE.md & skill docs finalization [S]
- Complete `CLAUDE.md` with all 14 tool descriptions, usage examples, coding conventions
- Finalize `skills/cv-setup/SKILL.md` and `skills/cv-help/SKILL.md` with full content

### C-7. Final smoke test & polish [M]
- Full server startup verification: all 14 tools registered
- MCP protocol compliance check
- Audit log verification (actions logged, text sanitized)
- Dry-run mode validation
- Run unit tests, report coverage

---

## Cross-Workstream Sync Points

| Point | Trigger | Unblocks |
|-------|---------|----------|
| **SP-0** | Foundation F-5 passes (server starts, MCP initialize works) | WS-A, WS-B, WS-C all begin |
| **SP-1** | WS-A task A-2 completes (utils/screenshot.py ready) | WS-B task B-5 (OCR) |
| **SP-2** | WS-A all feature tasks + WS-B all feature tasks complete | WS-C tasks C-5, C-6, C-7 (integration) |

## Complexity Summary

| Size | Count | Tasks |
|------|-------|-------|
| Small (S) | 5 | F-1, F-6, F-7, A-8, A-9 |
| Medium (M) | 16 | F-2, F-3, F-4, F-5, A-1, A-2, A-3, A-4, A-5, A-6, A-10, B-1, B-3, B-4, B-6, C-1, C-3, C-4, C-6, C-7 |
| Large (L) | 5 | A-7, B-2, B-5, C-2, C-5 |

## Feature Coverage Verification

| Feature | Workstream | Task(s) |
|---------|-----------|---------|
| F1 Window Enumeration | A | A-1, A-4 |
| F2 Screenshot Window | A | A-2, A-7 |
| F3 Screenshot Desktop | A | A-2, A-8 |
| F4 Region Capture | A | A-2, A-9 |
| F5 Window Focus | A | A-5 |
| F6 Mouse Click | B | B-1, B-2 |
| F7 Type Text | B | B-1, B-3 |
| F8 Send Keys | B | B-1, B-4 |
| F9 Window Resize/Move | A | A-6 |
| F10 OCR | B | B-5 |
| F11 Multi-Monitor | A | A-3 |
| F12 UI Accessibility | C | C-1, C-2 |
| F13 Wait/Sync | C | C-3 |

**All 13 PRD features assigned. Zero deferrals.**
