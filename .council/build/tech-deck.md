# Tech Deck: Native Windows Control v1.6.0

## 1. Technology Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| MCP Server | FastMCP (stdio) | Existing, no change |
| Language | Python 3.11+ | Existing |
| Win32 API | pywin32 + ctypes | Existing. Add MOUSEEVENTF_WHEEL/HWHEEL constants |
| Screenshots | Pillow + mss | Existing PrintWindow-first 3-tier capture |
| Models | Pydantic BaseModel | Existing |
| Testing | pytest + unittest.mock | Existing pattern |

No new dependencies required.

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    MCP Tool Layer                     │
│  input_keyboard.py  input_mouse.py  scroll.py  find.py │
│     (F1,F2,F5)       (F2,F5)      (F3,F2,F5)  (F4)  │
└──────────┬──────────────┬──────────────┬────────────┘
           │              │              │
┌──────────▼──────────────▼──────────────▼────────────┐
│              Shared Helpers Layer                      │
│  action_helpers.py: _capture_post_action()            │
│                     _build_window_state()              │
└──────────┬──────────────┬──────────────┬────────────┘
           │              │              │
┌──────────▼──────────┐ ┌▼────────────┐ ┌▼───────────┐
│  win32_window.py    │ │screenshot.py│ │win32_input.py│
│  focus_window()     │ │capture_window│ │send_mouse_  │
│  _is_focused()      │ │save_image() │ │  scroll()   │
│  get_window_info()  │ │PrintWindow  │ │type_unicode │
│                     │ │3-tier       │ │send_key_combo│
└─────────────────────┘ └─────────────┘ └─────────────┘
           │              │              │
┌──────────▼──────────────▼──────────────▼────────────┐
│              Security Layer                            │
│  security.py: validate_hwnd_range/fresh               │
│               check_restricted, check_rate_limit       │
│               guard_dry_run, log_action                │
└─────────────────────────────────────────────────────┘
```

## 3. Component Design

### 3.1 `src/utils/action_helpers.py` (NEW)

Shared helpers used by all mutating tools.

```python
"""Shared helpers for mutating tool actions: post-action screenshots and window state."""

import ctypes
import logging
import time

import win32gui

from src.utils.screenshot import capture_window
from src.utils.security import get_process_name_by_pid

logger = logging.getLogger(__name__)


def _build_window_state(hwnd: int) -> dict | None:
    """Build window state metadata dict for tool responses.

    Returns: {"hwnd": int, "title": str, "is_foreground": bool, "rect": {...}} or None on failure.
    """
    try:
        title = win32gui.GetWindowText(hwnd)
        fg = ctypes.windll.user32.GetForegroundWindow()
        rect = win32gui.GetWindowRect(hwnd)
        return {
            "hwnd": hwnd,
            "title": title,
            "is_foreground": fg == hwnd,
            "rect": {
                "x": rect[0], "y": rect[1],
                "width": rect[2] - rect[0], "height": rect[3] - rect[1],
            },
        }
    except Exception:
        return None


def _capture_post_action(hwnd: int, delay_ms: int = 150, max_width: int = 1280) -> str | None:
    """Capture screenshot after a mutating action. Returns image_path or None."""
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    try:
        result = capture_window(hwnd, max_width=max_width)
        return result.image_path
    except Exception:
        logger.debug("Post-action screenshot failed for HWND %s", hwnd, exc_info=True)
        return None


def _get_hwnd_process_name(hwnd: int) -> str:
    """Get process name for an hwnd. Returns empty string on failure."""
    try:
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return ""
        return get_process_name_by_pid(pid.value)
    except Exception:
        return ""
```

### 3.2 `src/tools/input_keyboard.py` (MODIFIED — F1, F2, F5)

Changes to `cv_type_text`:
- Add params: `hwnd: int | None = None`, `screenshot: bool = True`, `screenshot_delay_ms: int = 150`
- When hwnd provided: full security gate → atomic focus+verify+inject loop (3 retries) → post-action screenshot → window state
- When hwnd=None: existing behavior + foreground process check (security fix)

Atomic focus+inject loop pattern:
```python
MAX_RETRIES = 3

for attempt in range(MAX_RETRIES):
    # Focus window
    focus_window(hwnd)

    # Verify focus right before injection (TOCTOU mitigation)
    if ctypes.windll.user32.GetForegroundWindow() != hwnd:
        if attempt < MAX_RETRIES - 1:
            time.sleep(0.05)
            continue
        return make_error(INPUT_FAILED, f"Could not acquire focus on HWND {hwnd} after {MAX_RETRIES} attempts")

    # Rate limit inside retry loop (security: each injection counted)
    check_rate_limit()

    # Inject immediately (no yield)
    ok = type_unicode_string(text)
    if ok:
        break
```

Same pattern for `cv_send_keys`.

### 3.3 `src/tools/input_mouse.py` (MODIFIED — F2, F5)

Changes to `cv_mouse_click`:
- Add params: `screenshot: bool = True`, `screenshot_delay_ms: int = 150`
- After successful click/drag, call `_capture_post_action()` if screenshot=True and hwnd provided
- Add `window_state` to response dict
- No changes to existing focus/security logic

### 3.4 `src/tools/scroll.py` (NEW — F3)

```python
@mcp.tool()
def cv_scroll(
    hwnd: int,
    direction: str = "down",
    amount: int = 3,
    x: int | None = None,
    y: int | None = None,
    screenshot: bool = True,
    screenshot_delay_ms: int = 150,
) -> dict:
```

Implementation:
1. Validate direction in ("up", "down", "left", "right")
2. Clamp amount to [1, 20] range
3. Full security gate: validate_hwnd_range → validate_hwnd_fresh → check_restricted → check_rate_limit → guard_dry_run → log_action
4. Focus window
5. If x/y provided: validate via validate_coordinates(), convert to screen absolute via to_screen_absolute(x, y, hwnd)
6. If x/y not provided: default to window center from GetWindowRect
7. Normalize coordinates via normalize_for_sendinput()
8. Call send_mouse_scroll()
9. Post-action screenshot + window state
10. Return make_success(action="scroll", direction=..., amount=..., image_path=..., window_state=...)

### 3.5 `src/utils/win32_input.py` (MODIFIED — F3)

Add constants and function:
```python
# Mouse wheel event flags
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

def send_mouse_scroll(x: int, y: int, direction: str, amount: int = 3) -> bool:
    """Send scroll at normalized coordinates. Returns True on success."""
```

**Critical**: Change `MOUSEINPUT.mouseData` field from `c_ulong` to `c_long` for signed scroll data. This is backward-compatible since click operations always pass 0 for mouseData.

### 3.6 `src/tools/find.py` (MODIFIED — F4)

Changes to `cv_find`:
- On success: always capture screenshot (no cooldown), add `image_path`, `image_scale`, `window_origin` to response
- On failure: keep existing 5s cooldown, add `image_scale` and `window_origin` when screenshot captured
- Update tool docstring with coordinate mapping formula

### 3.7 `src/utils/screenshot.py` (MODIFIED — F4)

Throttle `_cleanup_old_screenshots()` to run every 10th call:
```python
_cleanup_counter = 0

def save_image(...):
    global _cleanup_counter
    _cleanup_counter += 1
    if _cleanup_counter % 10 == 0:
        _cleanup_old_screenshots()
    ...
```

## 4. Data Models

### WindowState (src/models.py)
```python
class WindowState(BaseModel):
    hwnd: int
    title: str
    is_foreground: bool
    rect: Rect
```

No other model changes needed. All responses remain `dict` via `make_success()`.

## 5. Security Architecture

### Security Gate Patterns

| Tool | hwnd | Gate |
|------|------|------|
| cv_type_text (hwnd=None) | No | check_restricted(fg_process) + check_rate_limit + guard_dry_run |
| cv_type_text (hwnd=N) | Yes | validate_hwnd_range + validate_hwnd_fresh + check_restricted + check_rate_limit (in retry) + guard_dry_run + log_action |
| cv_send_keys (hwnd=None) | No | check_restricted(fg_process) + check_rate_limit + guard_dry_run |
| cv_send_keys (hwnd=N) | Yes | validate_hwnd_range + validate_hwnd_fresh + check_restricted + check_rate_limit (in retry) + guard_dry_run + log_action |
| cv_mouse_click | Optional | Existing pattern unchanged |
| cv_scroll | Required | validate_hwnd_range + validate_hwnd_fresh + check_restricted + check_rate_limit + guard_dry_run + log_action |
| cv_find | Required | Read-only gate (unchanged) |

### Security Fixes
1. **Empty process name = block**: When `get_process_name_by_pid()` returns `""`, log warning and return ACCESS_DENIED error
2. **Rate limit in retry loop**: `check_rate_limit()` called inside atomic retry loop, before each SendInput
3. **Scroll amount clamped**: [1, 20] range enforced

## 6. Testing Strategy

Target: ~50-60 new tests across 4 test files.

### test_keyboard_hwnd.py (~15 tests)
- Atomic focus+inject: success on first try, success on retry, exhausted retries
- Security gate: all 5 steps called in order when hwnd provided
- Backward compat: hwnd=None path unchanged
- Screenshot in response: image_path present when hwnd provided
- Window state in response
- Rate limit called per retry

### test_scroll.py (~15 tests)
- Direction mapping: up/down/left/right → correct WHEEL/HWHEEL + sign
- Amount clamping: 0→1, 25→20, normal values pass
- Coordinate defaults: window center when x/y omitted
- Security gate: full pattern
- Screenshot and window state in response

### test_post_action.py (~10 tests)
- _capture_post_action: calls capture_window, returns path
- _capture_post_action: returns None on failure
- _build_window_state: returns 4 keys
- _build_window_state: returns None for invalid hwnd
- Delay honored: time.sleep called with correct value
- cv_mouse_click now returns image_path and window_state

### test_find_vision.py (~10 tests)
- Success response includes image_path (always)
- Success response includes image_scale and window_origin
- Failure response still has cooldown
- Coordinate mapping metadata correct

## 7. File/Directory Structure

```
src/
├── tools/
│   ├── input_keyboard.py   (MODIFIED: F1, F2, F5)
│   ├── input_mouse.py      (MODIFIED: F2, F5)
│   ├── scroll.py            (NEW: F3)
│   ├── find.py              (MODIFIED: F4)
│   └── ... (unchanged)
├── utils/
│   ├── action_helpers.py    (NEW: shared helpers)
│   ├── win32_input.py       (MODIFIED: wheel constants + send_mouse_scroll)
│   ├── screenshot.py        (MODIFIED: cleanup throttle)
│   └── ... (unchanged)
├── models.py                (MODIFIED: WindowState model)
├── errors.py                (unchanged)
└── server.py                (unchanged)

tests/unit/
├── test_keyboard_hwnd.py    (NEW: ~15 tests)
├── test_scroll.py           (NEW: ~15 tests)
├── test_post_action.py      (NEW: ~10 tests)
├── test_find_vision.py      (NEW: ~10 tests)
└── ... (existing 272 tests unchanged)

.claude-plugin/plugin.json   (MODIFIED: version 1.6.0)
pyproject.toml               (MODIFIED: version 1.6.0)
```

## 8. Performance Budget

| Operation | Added Latency | When |
|-----------|--------------|------|
| Post-action screenshot | ~100-200ms | Every mutating tool (opt-out with screenshot=False) |
| Window state metadata | <5ms | Every hwnd tool |
| Screenshot cleanup scan | ~5ms | Every 10th screenshot save |
| Focus retry (worst case) | ~150ms (3 retries × 50ms) | Only on focus failure |
