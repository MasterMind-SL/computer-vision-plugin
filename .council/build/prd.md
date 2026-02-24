# PRD: Computer Vision Plugin v1.5.0 — Critical Regression Fixes

## 1. Problem Statement

The Computer Vision plugin v1.4.0 has three critical regressions discovered during live testing that make it unreliable for real-world desktop automation:

1. **cv_focus_window silently fails** — Uses only `AttachThreadInput` + `SetForegroundWindow`, which Windows blocks from background processes. The MCP server runs as a background process, so this fails in the most common scenario. The function returns success even when the window never reaches the foreground, causing downstream click/type operations to hit the wrong window.

2. **cv_screenshot_window captures wrong pixels** — Uses MSS (screen pixel grab) as primary capture method. MSS copies whatever is visually on top at the window's screen coordinates. If another window overlaps, the screenshot shows the wrong application. The `PrintWindow` fallback exists but is only tried after MSS fails, not when MSS returns incorrect content. **Additionally, `capture_window_raw()` has the identical MSS-first ordering** and is used internally by `cv_find` (OCR path), `cv_ocr`, and `cv_get_text` — meaning three downstream tools also capture wrong content silently.

3. **cv_find returns zero results for Chrome/Electron** — Chrome does not expose its UIA accessibility tree unless explicitly activated by an assistive technology signal. Since Chrome/Electron powers VS Code, Slack, Discord, Teams, Spotify, and hundreds of modern apps, cv_find is broken for the majority of Windows applications. The fix is well-known: send `WM_GETOBJECT` to `Chrome_RenderWidgetHostHWND` child windows.

**Additionally**, when cv_find fails to match elements, it returns an error with no visual context. Since Claude is a multimodal LLM that can SEE screenshots natively, the plugin should return a screenshot on failure so Claude can use its vision capabilities.

**Also**, the existing `_capture_with_printwindow()` function has a GDI handle leak (`hdc_compat` not cleaned up in finally block). Promoting PrintWindow to primary capture will leak on every screenshot call, eventually exhausting the Windows GDI handle pool (10,000 limit).

## 2. Target Users

- Claude Code users automating desktop workflows across arbitrary Windows applications
- Developers using Claude Code to interact with IDEs, browsers, design tools, and enterprise software
- QA/testing scenarios where Claude Code drives application UI

## 3. Success Metrics

| Metric | Target |
|--------|--------|
| `cv_focus_window` foreground success rate | ≥95% from background process context |
| `cv_screenshot_window` correct content when occluded | 100% for non-minimized windows |
| `cv_find` UIA results for Chrome/Electron apps | >90% of DOM elements exposed |
| `cv_find` vision fallback on no-match | Returns `image_path` 100% of the time |
| Existing unit tests passing | 218/218 (100%) |
| GDI handle leaks | Zero (verified cleanup in finally blocks) |
| New external dependencies | Zero |

## 4. Core Features (ALL MANDATORY)

### F1: Robust Window Focus (`cv_focus_window` rewrite)

Implement AutoHotkey-grade foreground activation with multi-strategy escalation:

**Strategy order:**
1. Direct `SetForegroundWindow` (works when caller already owns foreground)
2. `SendInput` ALT key injection (`VK_MENU` down+up via `ctypes.windll.user32.SendInput`) to satisfy Windows' "received last input event" condition, then `SetForegroundWindow`
3. `AttachThreadInput` to cross-thread input queue attachment + `BringWindowToTop` + `SetForegroundWindow`
4. `SPI_SETFOREGROUNDLOCKTIMEOUT` bypass via `SystemParametersInfoW` — temporarily sets foreground lock timeout to 0, calls `SetForegroundWindow`, restores original value. **Wrapped in try/except**: if non-elevated process lacks privileges, log debug message and skip.

**Retry behavior:**
- Up to 6 attempts with escalating strategies, 50ms sleep between attempts
- **Verification predicate after each attempt**: `GetForegroundWindow() == hwnd`. Return success immediately upon verification. Only retry if verification fails.
- Restore minimized windows via `SW_RESTORE` before first attempt
- `BringWindowToTop` as supplementary call on each attempt
- Return `focused: True` ONLY when verification confirms foreground ownership
- On final failure, return `focused: False` with descriptive error

**Implementation scope:** Rewrite `focus_window()` in `src/utils/win32_window.py`

### F2: Correct Window Screenshots (PrintWindow-first capture)

Reverse the capture order in BOTH `capture_window()` AND `capture_window_raw()`:

**3-tier fallback chain:**
1. **Primary**: `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)` (0x02) — captures DWM-composed content even when occluded, works for Chrome/Electron/DirectX
2. **Secondary**: `PrintWindow(hwnd, hdc, 0)` — standard PrintWindow without DWM compositing, for legacy apps where PW_RENDERFULLCONTENT returns failure
3. **Tertiary**: MSS screen pixel grab — fast path fallback for cases where both PrintWindow calls fail

**Validation:** After each PrintWindow call, check if result is not all-black by sampling a center 10x10 pixel region (mean value > 5). If all-black, proceed to next tier.

**Minimized window handling:** Restore temporarily before PrintWindow (PrintWindow returns blank for minimized windows), capture, then re-minimize.

**GDI handle leak fix:** Track `hdc_compat` in the finally block of `_capture_with_printwindow()` and call `hdc_compat.DeleteDC()` before `hdc_mem.DeleteDC()`.

**Implementation scope:** Modify `capture_window()`, `capture_window_raw()`, and `_capture_with_printwindow()` in `src/utils/screenshot.py`

### F3: Chrome/Electron Accessibility Activation

Automatically activate Chrome/Electron accessibility trees before any UIA tree walk:

**Detection:** Check if the target window's process name matches known Chromium-based apps: `{'chrome', 'msedge', 'electron', 'code', 'slack', 'discord', 'teams', 'spotify', 'notion', 'figma', 'postman'}`. Also detect by window class `Chrome_WidgetWin_1` as a catch-all.

**Activation:**
1. Use `win32gui.EnumChildWindows(hwnd, callback, results)` to find ALL child windows with class `Chrome_RenderWidgetHostHWND`
2. For each found child: `win32gui.SendMessage(child_hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)` where `WM_GETOBJECT = 0x003D` and `OBJID_CLIENT = 0xFFFFFFFC`
3. Sleep 200ms after activation to let Chrome populate the accessibility tree

**Caching:** Module-level `set()` of activated top-level HWNDs. Skip activation for already-activated windows. Thread-safe since MCP tools run sequentially.

**Integration point:** Insert activation check at the top of `get_ui_tree()` in `src/utils/uia.py`, so ALL UIA consumers (`cv_find`, `cv_read_ui`, `cv_get_text`) benefit automatically.

**Implementation scope:** New helper function `_ensure_chromium_accessibility(hwnd)` in `src/utils/uia.py`, called from `get_ui_tree()`

### F4: Vision Fallback in `cv_find`

When `cv_find` fails to match elements (both UIA and OCR return empty in auto mode):
- Capture a screenshot of the window using `capture_window(hwnd)`
- Include `image_path` in the FIND_NO_MATCH error response
- Claude will use its `Read` tool on the image_path to visually inspect the window and retry with a better query or use coordinates from what it sees

**Scope:** Only `cv_find` on FIND_NO_MATCH. Other tools (`cv_ocr`, `cv_get_text`) already operate on visual content and don't need this. The screenshot on failure costs one PrintWindow call, which is acceptable for an already-failed search.

**Implementation scope:** Modify `cv_find` in `src/tools/find.py`

### F5: Version Bump to 1.5.0

Bump version in ALL version-bearing files:
- `.claude-plugin/plugin.json`
- `pyproject.toml`

## 5. User Stories

- **US1**: As a Claude Code user, when I call `cv_focus_window` on a background window, the window reliably comes to the foreground so my subsequent click/type operations hit the correct target.
- **US2**: As a Claude Code user, when I call `cv_screenshot_window` on a window behind other windows, I get that window's actual content, not whatever is visually on top.
- **US3**: As a Claude Code user, when I call `cv_find("search bar", hwnd)` on Chrome or VS Code, I get UIA matches for input fields, buttons, and links from the DOM.
- **US4**: As a Claude Code user, when `cv_find` can't match elements, I get a screenshot so Claude can visually find what I need.
- **US5**: As a Claude Code user, I never need to manually pass `--force-renderer-accessibility` to Chrome; the plugin activates it automatically.

## 6. Non-Functional Requirements

- **No new dependencies**: All fixes use existing pywin32, ctypes, comtypes, mss, Pillow
- **Backward compatibility**: All tool signatures unchanged; new fields (like `image_path` in cv_find error) are additive
- **Security gates preserved**: All existing checks remain intact. Chrome accessibility activation is read-only (no rate limit or dry-run needed)
- **Screen-absolute physical pixel coordinates**: No coordinate system changes
- **Performance**: PrintWindow adds ~20-50ms per capture vs MSS; acceptable tradeoff for correctness. Chrome accessibility activation is one-time ~200ms per window.
- **GDI resource safety**: All DC/bitmap handles cleaned up in finally blocks. Zero handle leaks.
- **Existing test suite**: All 218 unit tests must pass. New tests added for focus retries, PrintWindow capture, Chrome activation, and vision fallback.
- **Thread safety**: Chrome activation cache is a module-level set; safe since MCP tools run sequentially.

## 7. Assumptions & Constraints

- Windows 10 21H2+ or Windows 11 (PW_RENDERFULLCONTENT requires Windows 8.1+)
- Plugin runs as a background process (not foreground) — this is the primary failure mode for focus
- Chrome/Electron apps use `Chrome_WidgetWin_1` window class and `Chrome_RenderWidgetHostHWND` renderer child
- DPI awareness is set at process startup (already handled by `src/dpi.py`)
- No new dependencies allowed — all via existing pywin32, ctypes, comtypes
- Token cost is NOT a concern — speed and accuracy are the priorities
