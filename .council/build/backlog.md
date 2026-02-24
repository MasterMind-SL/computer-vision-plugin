# Implementation Backlog: CV Plugin v1.5.0 — Critical Regression Fixes

## Foundation Tasks (Team Lead — before parallel work)

### F-1. Fix GDI handle leak in `_capture_with_printwindow` [S]
- In `src/utils/screenshot.py`, add `hdc_compat.DeleteDC()` to the finally block before `hdc_mem.DeleteDC()`
- Initialize `hdc_compat = None` before try block
- **MUST be done FIRST before any other capture changes** — promoting PrintWindow to primary without this fix leaks one GDI handle per screenshot call (10,000 handle limit crash)

### F-2. No model/error/config changes needed for v1.5.0 [S]
- All existing models (FindMatch, OcrWord, OcrRegion, ScreenshotResult, Rect, UiaElement) are sufficient
- Existing error codes (FIND_NO_MATCH) already defined
- No new config entries needed (cooldown constants are module-level)

**SYNC POINT SP-0:** All workstreams begin after F-1 completes (GDI leak fixed).

---

## Workstream 1: Window Focus + Screenshot Capture (dev-alpha)
**Features: F1 (Robust Focus) + F2 (PrintWindow-First Capture)**
**Files:** `src/utils/win32_window.py`, `src/utils/screenshot.py`, `tests/unit/test_focus.py`, `tests/unit/test_capture_printwindow.py`

| # | Task | Size | Details |
|---|------|------|---------|
| 1.1 | Rewrite `focus_window()` with 4-strategy escalation | L | In `win32_window.py`. Strategy 1: Direct `SetForegroundWindow`. Strategy 2: `SendInput` ALT key (VK_MENU=0x12 down+up via ctypes, PAIRED keyup critical). Strategy 3: `AttachThreadInput` + `BringWindowToTop` + `SetForegroundWindow` (detach in finally). Strategy 4: SPI bypass (`SystemParametersInfoW(SPI_GETFOREGROUNDLOCKTIMEOUT)` save, set to 0, restore in finally, try/except for non-elevated). Pre-step: `ShowWindow(hwnd, SW_RESTORE)` if `IsIconic(hwnd)`. 6 retries, 50ms sleep. **Verification predicate**: `GetForegroundWindow() == hwnd` after each attempt. Return True only on verified success. |
| 1.2 | Extract `_capture_window_impl(hwnd) -> Image` for DRY | M | In `screenshot.py`, factor shared logic from `capture_window()` and `capture_window_raw()` into shared implementation. Both callers use `_capture_window_impl`. |
| 1.3 | Implement PrintWindow-first 3-tier fallback | L | In `_capture_window_impl`: (1) `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=0x02)` → validate not all-black via `img.getextrema()` (all channels min==max==0 → black). (2) `PrintWindow(hwnd, hdc, 0)` → validate. (3) MSS fallback. Add `flag` parameter to `_capture_with_printwindow(hwnd, w, h, flag=0x02)`. |
| 1.4 | Handle minimized windows in capture | S | In `_capture_window_impl`: detect via `IsIconic(hwnd)`, use `ShowWindow(hwnd, SW_SHOWNOACTIVATE)` before PrintWindow (avoids focus theft), re-minimize after capture only if foreground unchanged. |
| 1.5 | Write `tests/unit/test_focus.py` (~15 tests) | M | Test: each strategy success/failure, escalation order, retry count, verification predicate, SPI save/restore in finally, paired ALT keyup even on exception, minimized restore, all-fail returns False. Mock `ctypes.windll.user32`, `win32gui`, `win32process`. |
| 1.6 | Write `tests/unit/test_capture_printwindow.py` (~12 tests) | M | Test: PW_RENDERFULLCONTENT success, all-black triggers fallback to PW(0), all-black triggers MSS, GDI cleanup (verify DeleteDC calls), minimized handling with SW_SHOWNOACTIVATE, `_capture_window_impl` shared path, getextrema validation (normal image passes, black image fails, mostly-black-with-content passes). |

**SYNC POINT SP-1:** After task 1.3 complete, notify Workstream 3 that `capture_window()` now uses PrintWindow-first (F4 vision fallback benefits from correct screenshots).

---

## Workstream 2: Chrome/Electron Accessibility Activation (dev-beta)
**Feature: F3 (Chrome UIA Activation)**
**Files:** `src/utils/uia.py`, `tests/unit/test_chromium_accessibility.py`

| # | Task | Size | Details |
|---|------|------|---------|
| 2.1 | Define Chromium detection constants | S | Module-level in `uia.py`: `_CHROMIUM_PROCESSES = frozenset({'chrome', 'msedge', 'electron', 'code', 'slack', 'discord', 'teams', 'spotify', 'notion', 'figma', 'postman', 'brave', 'vivaldi', 'opera'})`, `WM_GETOBJECT = 0x003D`, `OBJID_CLIENT = 0xFFFFFFFC`, `SMTO_ABORTIFHUNG = 0x0002`. |
| 2.2 | Implement `_ensure_chromium_accessibility(hwnd)` | L | New function in `uia.py`. (1) Check cache `_activated_hwnds: set[int]` — skip if already activated. (2) Get process name via `_get_process_name` imported from `win32_window.py`. (3) Get class via `win32gui.GetClassName(hwnd)`. (4) If process in set OR class == `"Chrome_WidgetWin_1"`: enumerate children via `EnumChildWindows`. (5) For each child with class EXACTLY `"Chrome_RenderWidgetHostHWND"`: call `ctypes.windll.user32.SendMessageTimeoutW(child_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT, SMTO_ABORTIFHUNG, 2000, None)`. (6) Sleep 200ms after activation. (7) Add hwnd to cache set. |
| 2.3 | Integrate into `get_ui_tree()` | S | Call `_ensure_chromium_accessibility(hwnd)` at top of `get_ui_tree()`, after `_safe_init_uia()`, before `ElementFromHandle(hwnd)`. Wrap in try/except — activation failure should not block UIA tree walk. |
| 2.4 | Write `tests/unit/test_chromium_accessibility.py` (~10 tests) | M | Test: detection by process name (chrome), detection by class name (Chrome_WidgetWin_1), non-Chromium skipped, EnumChildWindows finds Chrome_RenderWidgetHostHWND, SendMessageTimeoutW called with correct params (WM_GETOBJECT, OBJID_CLIENT, SMTO_ABORTIFHUNG, 2000ms), caching prevents re-trigger on same hwnd, activation failure doesn't break get_ui_tree, exact class name match (not substring). Mock `ctypes.windll.user32`, `win32gui`, `win32process`. |

**SYNC POINT SP-2:** After task 2.3 complete, all UIA consumers (`cv_find`, `cv_read_ui`, `cv_get_text`) automatically benefit from Chrome accessibility.

---

## Workstream 3: Vision Fallback + Version Bump (dev-gamma)
**Features: F4 (cv_find Vision Fallback) + F5 (Version Bump)**
**Files:** `src/tools/find.py`, `tests/unit/test_find_fallback.py`, `.claude-plugin/plugin.json`, `pyproject.toml`

| # | Task | Size | Details |
|---|------|------|---------|
| 3.1 | Add per-HWND screenshot cooldown state | S | In `find.py`, add `_screenshot_cooldowns: dict[int, float] = {}` and `_SCREENSHOT_COOLDOWN = 5.0` at module level. Helper `_can_screenshot(hwnd) -> bool`: returns True if hwnd not in dict or `time.monotonic() - _screenshot_cooldowns[hwnd] >= _SCREENSHOT_COOLDOWN`. |
| 3.2 | Implement vision fallback on FIND_NO_MATCH | M | In `cv_find()`, in the `if not matches:` block: check `_can_screenshot(hwnd)`, if yes: try `capture_window(hwnd, max_width=1280)`, update cooldown, include `image_path` in error response. Error becomes: `{**make_error(FIND_NO_MATCH, "No elements matching '...' found. Use Read tool on image_path to visually inspect."), "image_path": result.image_path}`. Wrap in try/except — capture failure returns normal error without image_path. |
| 3.3 | Write `tests/unit/test_find_fallback.py` (~8 tests) | M | Test: no-match returns image_path, cooldown prevents second screenshot within 5s, cooldown allows after 5s, different HWNDs independent cooldowns, capture failure returns FIND_NO_MATCH without image_path, successful matches do NOT include image_path, cooldown uses time.monotonic. Mock `capture_window`, `time.monotonic`. |
| 3.4 | Version bump to 1.5.0 | S | `.claude-plugin/plugin.json`: `"version": "1.4.0"` → `"1.5.0"`. `pyproject.toml`: `version = "1.4.0"` → `"1.5.0"`. |

**NOTE:** Task 3.2 works with existing `capture_window()` but benefits from Workstream 1's PrintWindow-first fix. Not blocked — can start immediately.

---

## Dependency Graph

```
F-1 (GDI fix) ────────────────────────────────────────> All workstreams start
                                                         │
Workstream 1 (F1+F2): 1.1→1.2→1.3→1.4→1.5→1.6          │ parallel
Workstream 2 (F3):    2.1→2.2→2.3→2.4                   │ parallel
Workstream 3 (F4+F5): 3.1→3.2→3.3→3.4                   │ parallel
                                                         │
                       ──────── SP-1 (capture fixed) ────┤── improves F4 screenshots
                       ──────── SP-2 (Chrome a11y) ──────┤── improves cv_find UIA
                                                         │
                       ──────── All complete ─────────────> Integration testing
```

---

## Post-Integration Tasks (Team Lead)

### P-1. Run full test suite [M]
- `uv run pytest tests/unit/ -v` — all 218 existing + ~45 new tests must pass
- Verify server starts: `python -c "from src.server import mcp; print(len(mcp._tool_manager._tools), 'tools')"`
- Should still be 16 tools (no new tools added in v1.5.0)

### P-2. Verify behavioral fixes [M]
- cv_focus_window: returns `focused: False` on verified failure (not false success)
- cv_screenshot_window: returns correct content for occluded windows
- cv_find: returns UIA elements for Chrome windows
- cv_find: returns image_path on FIND_NO_MATCH

---

## Complexity Summary

| Size | Count | Tasks |
|------|-------|-------|
| Small (S) | 5 | F-1, F-2, 2.1, 3.1, 3.4 |
| Medium (M) | 7 | 1.2, 1.5, 1.6, 2.4, 3.2, 3.3, P-1 |
| Large (L) | 3 | 1.1, 1.3, 2.2 |
| **Total** | **15 tasks** + 2 post-integration | |

## Feature Coverage Verification

| PRD Feature | Workstream | Task(s) |
|-------------|-----------|---------|
| F1: Robust Window Focus | WS1 | 1.1 (4-strategy rewrite) |
| F2: PrintWindow-First Capture | WS1 | F-1 (GDI fix), 1.2 (_capture_window_impl), 1.3 (3-tier fallback), 1.4 (minimized handling) |
| F3: Chrome/Electron Accessibility | WS2 | 2.1 (constants), 2.2 (activation function), 2.3 (integration) |
| F4: Vision Fallback in cv_find | WS3 | 3.1 (cooldown), 3.2 (screenshot on no-match) |
| F5: Version Bump 1.5.0 | WS3 | 3.4 (plugin.json + pyproject.toml) |
| Security: SPI save/restore | WS1 | 1.1 (strategy 4 in finally block) |
| Security: SendInput safety | WS1 | 1.1 (paired keyup, bound to 1 call) |
| Security: GDI leak fix | Foundation | F-1 (hdc_compat cleanup) |
| Security: SendMessageTimeoutW | WS2 | 2.2 (SMTO_ABORTIFHUNG, 2s timeout) |
| Security: Screenshot cooldown | WS3 | 3.1 (per-HWND 5s cooldown) |

**All 5 PRD features + all security requirements assigned. Zero deferrals.**
