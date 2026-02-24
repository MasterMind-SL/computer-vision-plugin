# Tech Deck: Computer Vision Plugin v1.5.0 — Critical Regression Fixes

## 1. Technology Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| MCP Framework | `mcp>=1.26.0` (FastMCP) | Standard Claude Code plugin protocol, stdio transport |
| Win32 APIs | `pywin32` (win32gui, win32ui, win32con, win32process) | Window management, PrintWindow, GDI |
| Low-level Win32 | `ctypes` (user32.dll, kernel32.dll) | SendInput, SystemParametersInfoW, SendMessageW |
| COM/UIA | `comtypes` + UIAutomationCore | IUIAutomation for accessibility trees |
| Screen Capture | `mss` | Fallback screen pixel grab (tertiary) |
| Imaging | `Pillow` (PIL) | Image processing, all-black validation |
| OCR | `winocr` | Windows.Media.Ocr via WinRT (existing) |
| Models | `pydantic>=2.0.0` | Strict type validation, BaseModel throughout |
| **Zero new dependencies** | All features use existing deps | pywin32, ctypes, comtypes, mss, Pillow |

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                       Claude Code                         │
│                (stdio JSON-RPC over MCP)                  │
└──────────────────┬───────────────────────────────────────┘
                   │ stdin/stdout
┌──────────────────▼───────────────────────────────────────┐
│              FastMCP Server (server.py)                    │
│     Auto-discovery: @mcp.tool() from src/tools/*.py       │
├──────────────────────────────────────────────────────────┤
│                    SECURITY GATE                          │
│  security.py: validate_hwnd + check_restricted + log      │
│  (+ rate_limit + dry_run for mutating tools only)         │
├──────────┬──────────┬──────────┬─────────┬──────────────┤
│ tools/   │ tools/   │ tools/   │ tools/  │ tools/       │
│windows.py│capture.py│input_*.py│ ocr.py  │find.py       │
│ (F1)     │ (F2)     │          │         │ (F4)         │
├──────────┴──────────┴──────────┴─────────┴──────────────┤
│                  UTILITY LAYER (utils/)                    │
│  screenshot.py (F2) | win32_window.py (F1)                │
│  security.py | uia.py (F3) | ocr_engine.py               │
├──────────────────────────────────────────────────────────┤
│               CROSS-CUTTING (src/ root)                   │
│  dpi.py | coordinates.py | errors.py | models.py | config │
├──────────────────────────────────────────────────────────┤
│          Win32 API / mss / winocr / comtypes              │
└──────────────────────────────────────────────────────────┘
```

## 3. Component Design

### 3a. F1: Robust Window Focus — `src/utils/win32_window.py`

**Rewrite `focus_window(hwnd: int) -> bool`** with 4-strategy escalation:

**Strategy 1 — Direct SetForegroundWindow:**
```python
win32gui.SetForegroundWindow(hwnd)
```
Works when caller already owns foreground. Cheapest attempt.

**Strategy 2 — SendInput ALT key injection:**
```python
# Inject VK_MENU down+up via ctypes SendInput (not keybd_event)
# Must be PAIRED: keydown + keyup in same SendInput call
inputs = (INPUT * 2)()
inputs[0].type = INPUT_KEYBOARD; inputs[0].ki.wVk = VK_MENU  # 0x12
inputs[1].type = INPUT_KEYBOARD; inputs[1].ki.wVk = VK_MENU; inputs[1].ki.dwFlags = KEYEVENTF_KEYUP
ctypes.windll.user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))
win32gui.SetForegroundWindow(hwnd)
```
Satisfies Windows' "received last input event" condition. Verify `GetForegroundWindow() == hwnd` after. Bound to 1 SendInput call per attempt.

**Strategy 3 — AttachThreadInput:**
```python
fg_thread = GetWindowThreadProcessId(GetForegroundWindow())
target_thread = GetWindowThreadProcessId(hwnd)
AttachThreadInput(target_thread, fg_thread, True)
BringWindowToTop(hwnd)
SetForegroundWindow(hwnd)
AttachThreadInput(target_thread, fg_thread, False)  # always detach
```
Each strategy self-contained — detach in finally block.

**Strategy 4 — SPI_SETFOREGROUNDLOCKTIMEOUT bypass:**
```python
try:
    timeout = ctypes.c_uint()
    SystemParametersInfoW(SPI_GETFOREGROUNDLOCKTIMEOUT, 0, byref(timeout), 0)
    SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, byref(ctypes.c_uint(0)), SPIF_SENDCHANGE)
    BringWindowToTop(hwnd)
    SetForegroundWindow(hwnd)
finally:
    SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, byref(timeout), SPIF_SENDCHANGE)
```
Wrapped in try/except: non-elevated processes may lack privileges → skip silently. Original value always restored in finally.

**Retry loop:**
- Pre-step: restore minimized via `ShowWindow(hwnd, SW_RESTORE)` if `IsIconic(hwnd)`
- Up to 6 attempts cycling through strategies, 50ms sleep between
- **Verification predicate** after each: `GetForegroundWindow() == hwnd` → return True immediately
- On final failure: return False

**Performance:** < 100ms common case (strategy 1-2), < 2s worst case (6 retries).

**Consumer:** `cv_focus_window` in `src/tools/windows.py` — no API change, just improved underlying reliability.

### 3b. F2: PrintWindow-First Capture — `src/utils/screenshot.py`

**Modify BOTH `capture_window()` AND `capture_window_raw()`** to use shared implementation:

**Extract `_capture_window_impl(hwnd: int) -> Image`:**
```
1. PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=0x02)  → validate not all-black
2. PrintWindow(hwnd, hdc, 0)                           → validate not all-black
3. MSS region capture from GetWindowRect               → last resort
```

**All-black validation:**
```python
from PIL import ImageStat
extrema = img.getextrema()
is_black = all(mn == mx == 0 for mn, mx in extrema)
```
If all-black after tier 1, escalate to tier 2, etc.

**Minimized window handling:**
```python
was_minimized = ctypes.windll.user32.IsIconic(hwnd)
if was_minimized:
    win32gui.ShowWindow(hwnd, SW_SHOWNOACTIVATE)  # restore without stealing focus
    time.sleep(0.05)  # let DWM compose
# ... capture ...
if was_minimized:
    win32gui.ShowWindow(hwnd, SW_MINIMIZE)  # re-minimize
```
Use `SW_SHOWNOACTIVATE` (not `SW_RESTORE`) to avoid bringing to foreground. Only re-minimize if user didn't interact (foreground unchanged).

**GDI handle leak fix in `_capture_with_printwindow()`:**
```python
hdc_compat = None
try:
    hdc_window = win32gui.GetWindowDC(hwnd)
    hdc_mem = win32ui.CreateDCFromHandle(hdc_window)
    hdc_compat = hdc_mem.CreateCompatibleDC()  # THIS was leaking
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(hdc_mem, width, height)
    hdc_compat.SelectObject(bitmap)
    ctypes.windll.user32.PrintWindow(hwnd, hdc_compat.GetSafeHdc(), flag)
    # ... extract bits ...
finally:
    if bitmap: win32gui.DeleteObject(bitmap.GetHandle())
    if hdc_compat: hdc_compat.DeleteDC()  # NEW: was missing
    if hdc_mem: hdc_mem.DeleteDC()
    if hdc_window: win32gui.ReleaseDC(hwnd, hdc_window)
```

**Add `flag` parameter** to `_capture_with_printwindow(hwnd, w, h, flag=0x02)` to avoid duplicating logic.

**Performance:** < 50ms for PrintWindow primary. < 150ms worst case (all 3 tiers). All-black check via `getextrema()` is < 5ms.

### 3c. F3: Chrome/Electron Accessibility — `src/utils/uia.py`

**New function `_ensure_chromium_accessibility(hwnd: int)`:**

```python
_CHROMIUM_PROCESSES = frozenset({
    'chrome', 'msedge', 'electron', 'code', 'slack',
    'discord', 'teams', 'spotify', 'notion', 'figma', 'postman'
})
_activated_hwnds: set[int] = set()
WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC
SMTO_ABORTIFHUNG = 0x0002

def _ensure_chromium_accessibility(hwnd: int) -> None:
    if hwnd in _activated_hwnds:
        return  # cached, skip

    # Detect Chromium: process name OR window class
    process_name = _get_process_name(hwnd).lower()
    class_name = win32gui.GetClassName(hwnd)

    if process_name not in _CHROMIUM_PROCESSES and class_name != "Chrome_WidgetWin_1":
        return  # not Chromium

    # Find renderer child windows
    renderer_hwnds = []
    def _enum_callback(child_hwnd, results):
        if win32gui.GetClassName(child_hwnd) == "Chrome_RenderWidgetHostHWND":  # exact match
            results.append(child_hwnd)
        return True
    win32gui.EnumChildWindows(hwnd, _enum_callback, renderer_hwnds)

    # Activate accessibility on each renderer
    for child_hwnd in renderer_hwnds:
        ctypes.windll.user32.SendMessageTimeoutW(
            child_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT,
            SMTO_ABORTIFHUNG, 2000, None  # 2s timeout, abort if hung
        )

    if renderer_hwnds:
        time.sleep(0.2)  # let Chrome populate accessibility tree

    _activated_hwnds.add(hwnd)
```

**Integration:** Called at top of `get_ui_tree()` before `uia.ElementFromHandle(hwnd)`.

**Import:** `_get_process_name` from `src/utils/win32_window.py`.

**Performance:** < 5ms cached (set lookup). < 300ms first activation (EnumChildWindows + SendMessageTimeout + sleep).

### 3d. F4: Vision Fallback in cv_find — `src/tools/find.py`

**Modify the no-match branch:**
```python
if not matches:
    image_path = None
    try:
        from src.utils.screenshot import capture_window
        result = capture_window(hwnd, max_width=1280)
        image_path = result.image_path
    except Exception:
        pass  # best-effort, don't break error response

    error = make_error(FIND_NO_MATCH, f"No elements matching '{query}' found. Use Read tool on image_path to visually inspect.")
    if image_path:
        error["image_path"] = image_path
    return error
```

**Per-HWND cooldown:** Track `{hwnd: last_screenshot_time}` in module-level dict. Only capture if > 5 seconds since last screenshot for same HWND. Prevents burst disk writes during polling loops.

**Performance:** +50-100ms on failure path only. No impact on success path.

### 3e. F5: Version Bump — 2 files

- `.claude-plugin/plugin.json`: `"version": "1.4.0"` → `"1.5.0"`
- `pyproject.toml`: `version = "1.4.0"` → `"1.5.0"`

## 4. Data Models

No new Pydantic models required for v1.5.0. Existing models from v1.4.0 (`FindMatch`, `OcrWord`, `OcrRegion`, `ScreenshotResult`, `UiaElement`, `Rect`) cover all needs.

The only data change: `cv_find` FIND_NO_MATCH error dict gets an optional `image_path: str` field injected at runtime (not modeled — it's an error response enhancement).

## 5. API Contracts

All tool signatures remain **unchanged**. Changes are internal behavioral fixes:

| Tool | Visible Change |
|------|---------------|
| `cv_focus_window` | Now actually works from background processes. Returns `focused: False` on verified failure instead of false success. |
| `cv_screenshot_window` | Returns correct window content when occluded (was returning overlapping window pixels). |
| `cv_screenshot_desktop` | Unchanged (MSS is correct for desktop). |
| `cv_screenshot_region` | Unchanged (MSS is correct for screen regions). |
| `cv_find` | Now returns UIA results for Chrome/Electron. FIND_NO_MATCH includes `image_path` for vision fallback. |
| `cv_read_ui` | Now returns populated tree for Chrome/Electron (automatic accessibility activation). |
| `cv_get_text` | Benefits from Chrome accessibility (UIA path now works for Chrome). |
| `cv_ocr` | Benefits from correct PrintWindow capture (OCR on actual window content). |

## 6. Security Architecture

### Security Gate Matrix (unchanged from v1.4.0)

| Tool Type | validate_hwnd | check_restricted | check_rate_limit | guard_dry_run | log_action |
|-----------|:---:|:---:|:---:|:---:|:---:|
| Mutating (click, type, keys, focus, move) | Y | Y | Y | Y | Y |
| Read-only (ocr, find, get_text, read_ui) | Y | Y | N | N | Y |
| Passive (list_windows, list_monitors, wait) | N | N | N | N | N |

### New Security Measures for v1.5.0

1. **SPI_SETFOREGROUNDLOCKTIMEOUT safety (F1):** Save original value before modification, restore in finally block. try/except for non-elevated processes. Never leave system in modified state.
2. **SendInput bounded (F1):** Maximum 1 ALT injection per focus attempt. Paired keydown+keyup in same SendInput call to prevent stuck modifier keys.
3. **AttachThreadInput isolation (F1):** Each strategy self-contained with detach in finally. No cross-strategy thread state leakage.
4. **GDI resource safety (F2):** Fix hdc_compat leak in _capture_with_printwindow. All DC/bitmap handles tracked and cleaned in finally blocks.
5. **Chrome WM_GETOBJECT safety (F3):** Use `SendMessageTimeoutW` with `SMTO_ABORTIFHUNG` (2s timeout) instead of blocking `SendMessage`. Exact class name matching for `Chrome_RenderWidgetHostHWND`. Cache prevents repeat activation.
6. **Screenshot burst prevention (F4):** Per-HWND cooldown (5s) for fallback screenshots. Prevents disk exhaustion during polling loops.
7. **Minimized window safety (F2):** Use `SW_SHOWNOACTIVATE` to avoid focus theft during capture. Conditional re-minimize only if foreground unchanged.

## 7. Testing Strategy

### Existing Tests
- 218 unit tests in `tests/unit/` — ALL must pass unchanged
- Mocked Win32 APIs via `conftest.py` fixtures

### New Tests Required

| Feature | Test File | Tests | Key Scenarios |
|---------|-----------|-------|---------------|
| F1 Focus | `tests/unit/test_focus.py` | ~15 | Each strategy, retry count, verification, SPI save/restore, minimized restore |
| F2 Capture | `tests/unit/test_capture_printwindow.py` | ~12 | 3-tier fallback, all-black detection, GDI cleanup (verify DeleteDC calls), minimized handling |
| F3 Chrome a11y | `tests/unit/test_chromium_accessibility.py` | ~10 | Process name detection, EnumChildWindows callback, WM_GETOBJECT sending, caching, non-Chrome skip |
| F4 Find fallback | `tests/unit/test_find_fallback.py` | ~5 | image_path on no-match, screenshot failure doesn't break error, cooldown |
| **Total new** | | **~42** | |
| **Total (new + existing)** | | **~260** | |

### Integration Tests (manual, not in CI)
- `tests/integration/test_focus_real.py` — Focus Chrome from background
- `tests/integration/test_capture_occluded.py` — Screenshot window behind VS Code

## 8. File Change Summary

| File | Change Type | Features |
|------|------------|----------|
| `src/utils/win32_window.py` | **Rewrite** `focus_window()` | F1 |
| `src/utils/screenshot.py` | **Rewrite** `capture_window()`, `capture_window_raw()`, fix `_capture_with_printwindow()` | F2 |
| `src/utils/uia.py` | **Add** `_ensure_chromium_accessibility()`, modify `get_ui_tree()` | F3 |
| `src/tools/find.py` | **Modify** no-match branch, add screenshot fallback + cooldown | F4 |
| `.claude-plugin/plugin.json` | Version bump 1.4.0 → 1.5.0 | F5 |
| `pyproject.toml` | Version bump 1.4.0 → 1.5.0 | F5 |
| `tests/unit/test_focus.py` | **New** | F1 tests |
| `tests/unit/test_capture_printwindow.py` | **New** | F2 tests |
| `tests/unit/test_chromium_accessibility.py` | **New** | F3 tests |
| `tests/unit/test_find_fallback.py` | **New** | F4 tests |

## 9. Performance Budget

| Feature | Common case | Worst case | Acceptable? |
|---------|------------|------------|-------------|
| F1 Focus | < 100ms (strategy 1-2) | < 2s (6 retries) | Yes — interactive |
| F2 Capture | < 50ms (PrintWindow) | < 150ms (3 tiers) | Yes — faster than OCR |
| F3 Chrome a11y | < 5ms (cached) | < 300ms (first activation) | Yes — one-time |
| F4 Find fallback | +0ms (match found) | +100ms (no-match + screenshot) | Yes — failure path only |
