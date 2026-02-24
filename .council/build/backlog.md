# Implementation Backlog: CV Plugin v1.6.0 — Native Windows Control

## Foundation Tasks (Team Lead — before parallel work)

### F-1. Add `WindowState` model to `src/models.py` [S]
- Add Pydantic model: `WindowState(hwnd: int, title: str, is_foreground: bool, rect: Rect)`
- Uses existing `Rect` model
- All downstream workstreams depend on this existing

### F-2. Create `src/utils/action_helpers.py` with locked signatures [M]
- **CRITICAL**: Exact signatures must be frozen before parallel work starts. All workstreams import from this file.
- `_build_window_state(hwnd: int) -> dict | None` — calls `GetWindowText`, `GetForegroundWindow`, `GetWindowRect`. Returns `{"hwnd": int, "title": str, "is_foreground": bool, "rect": {"x": int, "y": int, "width": int, "height": int}}` or `None` on failure. NOTE: Do NOT duplicate `_build_window_info` from `win32_window.py` — either delegate or write minimal version with only the 3 required Win32 API calls.
- `_capture_post_action(hwnd: int, delay_ms: int = 150, max_width: int = 1280) -> str | None` — sleeps `delay_ms/1000`, calls `capture_window(hwnd, max_width=max_width)`, returns `result.image_path` or `None` on failure (never raises).
- `_get_hwnd_process_name(hwnd: int) -> str` — extracts PID via `GetWindowThreadProcessId`, delegates to `get_process_name_by_pid` from `security.py`. Returns `""` on failure.
- Include unit-testable error handling (try/except returning None/"" on failure)

### F-3. Modify `src/utils/win32_input.py` — scroll support + mouseData fix [M]
- **CRITICAL**: This is an atomic task — mouseData type change + new constants + send_mouse_scroll MUST be done together by one agent.
- Add constants: `MOUSEEVENTF_WHEEL = 0x0800`, `MOUSEEVENTF_HWHEEL = 0x1000`, `WHEEL_DELTA = 120`
- Change `MOUSEINPUT.mouseData` field from `c_ulong` to `c_long` (signed, required for negative scroll values)
- This change is backward-compatible: existing `send_mouse_click` and `send_mouse_drag` always pass mouseData=0, which is identical in signed and unsigned representation
- Add function: `send_mouse_scroll(x: int, y: int, direction: str, amount: int = 3) -> bool` — builds INPUT struct with `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_WHEEL` (or `MOUSEEVENTF_HWHEEL` for left/right), sets `mouseData = amount * WHEEL_DELTA` (positive for up/left, negative for down/right)

### F-4. Throttle screenshot cleanup in `src/utils/screenshot.py` [S]
- Add module-level counter: `_cleanup_call_count: int = 0`
- In `save_image()`: increment `_cleanup_call_count`, only call `_cleanup_old_screenshots()` when `_cleanup_call_count % 10 == 0`
- Reduces filesystem overhead with post-action screenshots on every mutating tool

**SYNC POINT SP-0:** All workstreams begin after F-1 through F-4 complete.

---

## Workstream A: Atomic Keyboard + Scroll Tool (dev-alpha)
**Features:** F1 (atomic keyboard with hwnd), F3 (cv_scroll), F2+F5 (screenshot + window_state on keyboard/scroll)
**Files:** `src/tools/input_keyboard.py`, `src/tools/scroll.py`

| # | Task | Size | Details |
|---|------|------|---------|
| A.1 | Modify `cv_type_text` and `cv_send_keys` with hwnd support | L | In `input_keyboard.py`. Add params: `hwnd: int \| None = None`, `screenshot: bool = True`, `screenshot_delay_ms: int = 150`. **When hwnd provided**: (1) `validate_hwnd_range(hwnd)`, (2) `validate_hwnd_fresh(hwnd)`, (3) `_get_hwnd_process_name(hwnd)` — if empty string, return `make_error(ACCESS_DENIED, "Cannot determine process")`, (4) `check_restricted(process_name)`, (5) `guard_dry_run(...)`, (6) `log_action(...)`. Then atomic retry loop (max 3): `focus_window(hwnd)` → verify `GetForegroundWindow() == hwnd` → `check_rate_limit()` (inside loop per security audit) → inject (`type_unicode_string` or `send_key_combo`) → break on success. On exhausted retries: `make_error(INPUT_FAILED, "Could not acquire focus after 3 attempts")`. After success: `_capture_post_action(hwnd, screenshot_delay_ms)` if screenshot=True, `_build_window_state(hwnd)`, merge into `make_success()`. **When hwnd=None**: preserve exact v1.5.0 behavior (existing security: `check_rate_limit` + `guard_dry_run` + `log_action`, no screenshot, no window_state). |
| A.2 | Create `cv_scroll` tool | L | New file `src/tools/scroll.py`. Tool: `cv_scroll(hwnd: int, direction: str = "down", amount: int = 3, x: int \| None = None, y: int \| None = None, screenshot: bool = True, screenshot_delay_ms: int = 150)`. (1) Validate direction in `("up", "down", "left", "right")` — return `make_error(INVALID_PARAMETER)` otherwise. (2) Clamp amount to `[1, 20]`. (3) Full security gate: `validate_hwnd_range` → `validate_hwnd_fresh` → `_get_hwnd_process_name` (empty = ACCESS_DENIED) → `check_restricted` → `check_rate_limit` → `guard_dry_run` → `log_action`. (4) `focus_window(hwnd)`. (5) If x/y provided: `validate_coordinates(x, y, hwnd)`, convert to screen absolute. If not provided: use window center from `GetWindowRect`. (6) `normalize_for_sendinput(screen_x, screen_y)`. (7) `send_mouse_scroll(norm_x, norm_y, direction, amount)`. (8) `_capture_post_action` + `_build_window_state`. (9) Return `make_success(action="scroll", direction=direction, amount=amount, image_path=..., window_state=...)`. Import `mcp` from `src.server`. |

**Dependencies:** Both tasks depend on F-2 (action_helpers.py) and A.2 depends on F-3 (win32_input.py scroll support).

---

## Workstream B: Mouse + Find Enhancements + Polish (dev-beta)
**Features:** F2 (post-action screenshot on mouse), F4 (vision-enhanced cv_find), F5 (window_state verification), F6 (version bump)
**Files:** `src/tools/input_mouse.py`, `src/tools/find.py`, `.claude-plugin/plugin.json`, `pyproject.toml`

| # | Task | Size | Details |
|---|------|------|---------|
| B.1 | Modify `cv_mouse_click` with screenshot + window_state | M | In `input_mouse.py`. Add params: `screenshot: bool = True`, `screenshot_delay_ms: int = 150`. After successful click/drag: if `screenshot=True` and hwnd was provided, call `_capture_post_action(hwnd, screenshot_delay_ms)` and add `image_path` to response dict. Call `_build_window_state(hwnd)` and add `window_state` to response. Backward compatible: `screenshot=False` restores v1.5.0 response shape. No changes to existing focus/security logic. |
| B.2 | Modify `cv_find` with vision enhancement | M | In `find.py`. **Four explicit additions**: (1) On SUCCESS path: always call `capture_window(hwnd, max_width=1280)` — NO cooldown for success (remove/bypass existing cooldown check on success branch). (2) Add `image_path` from capture result to success response dict. (3) Compute `image_scale = saved_image_width / physical_window_width` (from `GetWindowRect`) and add to response. (4) Add `window_origin: {"x": rect[0], "y": rect[1]}` to response. Keep existing no-match screenshot path with its 5s cooldown UNCHANGED. Add `_build_window_state(hwnd)` to success response. Update tool docstring with coordinate mapping formula: `screen_x = window_origin.x + (image_x / image_scale)`. |
| B.3 | F5 verification sweep — window_state in all tools | S | Audit all mutating tools after A.1, A.2, B.1, B.2 are complete. Verify `window_state` is present in every response where hwnd is available. Check both success and error paths. Fix any gaps. |
| B.4 | Version bump to 1.6.0 | S | `pyproject.toml`: version = "1.6.0". `.claude-plugin/plugin.json`: version = "1.6.0". |

**Dependencies:** B.1 and B.2 depend on F-2 (action_helpers.py) and F-4 (screenshot throttle). B.3 depends on all implementation tasks. B.4 is independent.

---

## Workstream C: Tests (dev-gamma)
**Features:** 100% test coverage on all new code
**Files:** `tests/unit/test_post_action.py`, `tests/unit/test_keyboard_hwnd.py`, `tests/unit/test_scroll.py`, `tests/unit/test_find_vision.py`

| # | Task | Size | Details |
|---|------|------|---------|
| C.1 | Create `tests/unit/test_post_action.py` (~10 tests) | M | Tests for shared helpers: `_capture_post_action` returns image_path on success, returns None on capture failure, honors delay_ms (mock time.sleep), respects max_width. `_build_window_state` returns correct 4-key dict, returns None for invalid hwnd. `_get_hwnd_process_name` returns process name, returns "" on failure. Screenshot cleanup counter increments and triggers every 10th call. |
| C.2 | Create `tests/unit/test_keyboard_hwnd.py` (~15 tests) | L | Tests: hwnd=None preserves exact v1.5.0 behavior (no security gate additions). hwnd provided triggers full security gate (validate_hwnd_range, validate_hwnd_fresh, check_restricted, guard_dry_run, log_action all called). Atomic focus retry: success on first try, success on retry #2, exhausted 3 retries returns INPUT_FAILED. check_rate_limit called inside retry loop (once per attempt). Empty process name returns ACCESS_DENIED. screenshot=True includes image_path in response. screenshot=False excludes image_path. window_state included when hwnd provided. Restricted process blocked. cv_send_keys with hwnd follows same pattern. dry_run returns planned action. |
| C.3 | Create `tests/unit/test_scroll.py` (~15 tests) | L | Tests: valid directions accepted (up/down/left/right). Invalid direction returns INVALID_PARAMETER. Amount clamping: 0→1, 25→20, 3→3. Full security gate called in order. `test_scroll_down_positive_mousedata` — mouseData > 0 for "down". `test_scroll_up_negative_mousedata` — mouseData < 0 for "up" (CRITICAL: validates c_long change). `test_scroll_left_right_uses_hwheel` — MOUSEEVENTF_HWHEEL flag used. Default coords = window center when x/y omitted. Custom x/y converted to screen absolute. screenshot after scroll included. screenshot=False suppresses capture. window_state in response. Empty process name returns ACCESS_DENIED. hwnd required (no default). |
| C.4 | Create `tests/unit/test_find_vision.py` (~10 tests) | M | Tests: success response includes image_path (always, no cooldown). Success response includes image_scale (correct ratio). Success response includes window_origin (matches GetWindowRect). window_state in success response. No-match still has vision fallback with 5s cooldown (existing behavior preserved). image_scale calculation: `saved_width / physical_width`. Coordinate mapping metadata correct. max_results still respected. method_used still in response. |
| C.5 | Regression verification | S | Run `uv run pytest tests/unit/ -v`. All 272 existing tests + ~50 new tests must pass. Verify server starts with 17 tools (16 existing + cv_scroll). Zero failures. |

**Dependencies:** C.1 can start immediately after F-2 (tests shared helpers). C.2 depends on A.1. C.3 depends on A.2. C.4 depends on B.2. C.5 depends on all.

---

## Cross-Workstream Dependency Graph

```
Foundation (team-lead):
  F-1 (models.py)  ──┐
  F-2 (action_helpers) ──┤── SP-0: all workstreams start
  F-3 (win32_input)  ──┤
  F-4 (screenshot)   ──┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
  WS-A (keyboard+scroll) WS-B (mouse+find)  WS-C (tests)
  dev-alpha           dev-beta          dev-gamma
  A.1: keyboard hwnd  B.1: mouse screenshot C.1: test_post_action
  A.2: cv_scroll       B.2: cv_find vision   C.2: test_keyboard (after A.1)
                       B.3: F5 sweep (after A+B) C.3: test_scroll (after A.2)
                       B.4: version bump      C.4: test_find (after B.2)
                                              C.5: regression (last)
```

## Integration Checkpoints

**Checkpoint 1** (after A.1 + A.2 + B.1 + B.2 complete):
- Verify all 4 tool files import from `action_helpers.py` correctly
- Verify `scroll.py` auto-discovered by FastMCP (imports `mcp` from `src.server`)
- Verify no circular imports
- dev-beta runs B.3 (F5 verification sweep)

**Checkpoint 2** (after all tests written):
- Run full test suite: `uv run pytest tests/unit/ -v`
- Verify 272 existing + ~50 new = ~322 tests passing
- Verify server reports 17 tools

---

## Post-Integration Tasks (Team Lead)

### P-1. Full test suite run [M]
- `uv run pytest tests/unit/ -v` — all tests must pass
- Verify: `python -c "from src.server import mcp; print(len(mcp._tool_manager._tools), 'tools')"` → 17 tools

### P-2. Behavioral verification [M]
- cv_type_text with hwnd: returns image_path + window_state
- cv_send_keys with hwnd: returns image_path + window_state
- cv_mouse_click: returns image_path + window_state
- cv_scroll: returns image_path + window_state
- cv_find success: returns image_path + image_scale + window_origin
- Existing cv_mouse_click without screenshot param: still works (backward compat)

---

## Complexity Summary

| Size | Count | Tasks |
|------|-------|-------|
| Small (S) | 4 | F-1, F-4, B.3, B.4, C.5 |
| Medium (M) | 7 | F-2, F-3, B.1, B.2, C.1, C.4 |
| Large (L) | 4 | A.1, A.2, C.2, C.3 |
| **Total** | **15 tasks** + 2 post-integration | |

## Feature Coverage Verification

| PRD Feature | Workstream | Task(s) |
|-------------|-----------|---------|
| F1: Atomic Keyboard with hwnd | WS-A | A.1 (cv_type_text + cv_send_keys) |
| F2: Post-Action Screenshot | WS-A + WS-B | A.1 (keyboard), A.2 (scroll), B.1 (mouse) |
| F3: cv_scroll | Foundation + WS-A | F-3 (win32_input), A.2 (scroll tool) |
| F4: Vision-Enhanced cv_find | WS-B | B.2 (screenshot on success + metadata) |
| F5: Window State in Every Response | All | A.1, A.2, B.1, B.2 (add window_state), B.3 (verification sweep) |
| F6: Version Bump 1.6.0 | WS-B | B.4 (plugin.json + pyproject.toml) |
| Security: Full gate on keyboard+scroll | WS-A | A.1, A.2 |
| Security: Empty process = ACCESS_DENIED | WS-A | A.1, A.2 |
| Security: Rate limit in retry loop | WS-A | A.1 |
| Security: Scroll amount clamped [1,20] | WS-A | A.2 |
| Testing: ~50 new tests | WS-C | C.1, C.2, C.3, C.4 |
| Testing: Zero regressions on 272 | WS-C | C.5 |

**All 6 PRD features + all security requirements + all testing requirements assigned. Zero deferrals.**
