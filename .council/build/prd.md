# PRD: Native Windows Control v1.6.0

## Problem Statement

The CV Plugin v1.5.0 can see the screen and inject input, but cannot reliably **control** applications. Three structural deficiencies prevent human-like automation:

1. **Focus loss between tool calls.** `cv_type_text` and `cv_send_keys` inject keystrokes into whatever window is foreground — which is the terminal, not the target app. The MCP host terminal reclaims focus between every tool call. `cv_mouse_click` already has `hwnd` for auto-focus; keyboard tools do not.

2. **Blind element location.** `cv_find` relies on UIA trees (sparse for Chrome/Electron) and OCR (requires en-US language pack). Claude is a multimodal LLM that can SEE screenshots, yet the plugin doesn't leverage this as the primary element finding strategy.

3. **No state verification after actions.** Every mutating tool returns minimal JSON (`{"success": true}`) with zero visual confirmation. Claude cannot confirm a click landed, text appeared, or a page loaded. Without see-act-verify, automation requires blind trust.

## Target Users

Claude Code users automating ANY Windows desktop application — browsers, IDEs, native Win32 apps, Electron apps, legacy enterprise software — via the MCP plugin. The plugin should enable a human-like see-act-verify loop.

## Success Metrics

| Metric | Target |
|--------|--------|
| Type into Chrome address bar | ONE tool call (atomic focus+type+screenshot) |
| Scroll any window | ONE tool call (new cv_scroll tool) |
| Post-action verification | Every mutating tool returns screenshot |
| Element finding on web pages | Claude's vision as primary fallback |
| Existing tests | Zero regressions on 272 tests |
| New test coverage | 100% on new code |

## Core Features (ALL MANDATORY)

### F1. Atomic Keyboard Operations with hwnd

Add optional `hwnd: int | None` parameter to `cv_type_text` and `cv_send_keys`.

When hwnd is provided:
1. Call `focus_window(hwnd)` to bring window to foreground
2. Verify `GetForegroundWindow() == hwnd` before injecting input
3. Inject keystrokes immediately (no yield between focus and input)
4. Verify focus was maintained after input
5. If focus lost between steps 1-3, retry entire atomic operation (max 3 retries)
6. Capture post-action screenshot of target window
7. Return screenshot path + action result

When hwnd is None: preserve current v1.5.0 behavior (backward compatible).

**Security**: When hwnd is provided, apply full 5-step security gate: `validate_hwnd_range` → `validate_hwnd_fresh` → `check_restricted(process_name)` → `check_rate_limit` → `guard_dry_run` → `log_action`. Mirror pattern from `cv_mouse_click`.

### F2. Post-Action Screenshot on All Mutating Tools

Every mutating tool (`cv_type_text`, `cv_send_keys`, `cv_mouse_click`, `cv_scroll`) returns an `image_path` field containing a screenshot of the target window AFTER the action completes.

Parameters:
- `screenshot: bool = True` — opt-out flag to disable screenshot capture
- `screenshot_delay_ms: int = 150` — rendering settle delay before capture (default 150ms, caller can increase to 500-1000ms for web pages)

Implementation: Create shared helper `_capture_post_action(hwnd: int | None, delay_ms: int) -> str | None` that handles the sleep + capture + save. Returns image path or None. All mutating tools call this after their action.

**Critical**: Tools must still return `dict` (via `make_success()`). The `image_path` field is added to the dict, NOT returned as ImageContent blocks. This preserves all internal consumers (cv_find, OCR tools). Claude reads the screenshot via the Read tool on the path.

### F3. cv_scroll — Dedicated Scroll Tool

New tool: `cv_scroll(hwnd, direction, amount, x, y, screenshot, screenshot_delay_ms)`

Parameters:
- `hwnd: int` — target window handle (required)
- `direction: str` — "up", "down", "left", "right"
- `amount: int = 3` — number of scroll notches (each = WHEEL_DELTA = 120)
- `x: int | None = None` — optional X position for scroll target (window-relative)
- `y: int | None = None` — optional Y position for scroll target (window-relative)
- `screenshot: bool = True` — capture after scroll
- `screenshot_delay_ms: int = 150` — settle delay

Implementation:
- Focus window via `focus_window(hwnd)`
- Move cursor to (x, y) within window, or window center if not specified
- Use `SendInput` with `MOUSEEVENTF_WHEEL` (vertical) or `MOUSEEVENTF_HWHEEL` (horizontal)
- Multiply `amount` by `WHEEL_DELTA` (120) for scroll distance
- Apply full mutating-tool security gate
- Return post-action screenshot

File: `src/tools/scroll.py`

### F4. Vision-Enhanced cv_find

Enhance `cv_find` to leverage Claude's multimodal vision:

1. **Always include screenshot on success**: When matches are found, include `image_path` in the success response so Claude can visually verify the match makes sense.
2. **Always include screenshot on failure**: Already implemented in v1.5.0 (with 5s cooldown). Keep failure-path cooldown to prevent spam, but remove cooldown for success-path screenshots.
3. **Coordinate mapping in tool description**: Document the formula for mapping downscaled image coordinates to screen coordinates: `screen_x = rect.x + (image_x / scale_factor)`, `screen_y = rect.y + (image_y / scale_factor)` where `scale_factor = image_width / physical_width`.
4. **Include scale metadata**: Return `image_scale` and `window_origin` in responses so Claude can compute click targets from visual inspection.

### F5. Window State in Every Response

Every tool that accepts `hwnd` includes `window_state` metadata in its response:

```json
{
  "window_state": {
    "hwnd": 198902,
    "title": "Google - Google Chrome",
    "is_foreground": true,
    "rect": {"x": 0, "y": 0, "width": 1920, "height": 1040}
  }
}
```

Implementation: Shared helper `_build_window_state(hwnd: int) -> dict` that calls `GetWindowText`, `GetForegroundWindow`, `GetWindowRect`. Called at the end of each tool after the action completes. Lightweight — no screenshot, just metadata.

### F6. Version Bump to 1.6.0

Update version in `pyproject.toml` and `.claude-plugin/plugin.json`.

## User Stories

1. "As Claude, I need to type a URL into Chrome's address bar in a single tool call, so I can navigate without losing focus to the terminal." → F1
2. "As Claude, I need to see what happened after I clicked a button, so I can verify the action succeeded and decide what to do next." → F2
3. "As Claude, I need to scroll down a web page to find content below the fold, so I can read or interact with the full page." → F3
4. "As Claude, I need to find the 'Submit' button on a complex web form by looking at a screenshot, because UIA doesn't expose it and OCR might miss styled text." → F4
5. "As Claude, I need to know which window received my action and whether it's still focused, so I can recover from unexpected state changes." → F5

## Non-Functional Requirements

### Backward Compatibility
- All new parameters default to preserving v1.5.0 behavior
- `hwnd=None` on keyboard tools means old behavior (no focus, no security gate)
- `screenshot=True` by default but can be disabled
- No breaking changes to existing tool signatures

### Performance
- Post-action screenshots add ~100-200ms per action (PrintWindow + PNG save + settle delay)
- Window state metadata adds <5ms (Win32 API calls)
- Acceptable for interactive automation where reliability > speed

### Security
- cv_scroll: Full mutating-tool security gate (F5-F9 pattern from CLAUDE.md)
- cv_type_text/cv_send_keys with hwnd: Full security gate when hwnd provided, existing minimal gate when hwnd=None
- All tools validate hwnd before focus attempt

### Testing
- Every new tool and every modified tool gets unit tests with mocked Win32 APIs
- Test focus-before-type, screenshot-in-response, scroll directions, backward compatibility (hwnd=None)
- Target: 100% coverage on new code, zero regressions on 272 existing tests

### Resource Management
- Screenshots go to existing temp dir with 5-minute auto-cleanup
- No new dependencies
- No new temp dirs or persistent state

## Assumptions & Constraints

- MCP server runs as stdio transport (never HTTP/SSE)
- The terminal process reclaims focus between tool calls (fundamental constraint)
- UAC dialogs on the secure desktop are unreachable by SendInput (explicit limitation)
- Chrome/Electron UIA trees remain sparse for web content (design for this)
- OCR en-US language pack may not be installed (don't depend on it)
- Modal child dialogs may have different HWNDs from parent (cv_list_windows with include_children=True already handles this)
- File dialogs use Win32 controls (standard UIA should work)

## Edge Cases

- **Modal dialogs**: cv_list_windows returns child windows. cv_find walks the modal's UIA tree. Document that modal dialogs block parent window input.
- **Minimized windows**: Existing PrintWindow capture + SW_SHOWNOACTIVATE handles this.
- **Multi-monitor**: Existing coordinate system is screen-absolute physical pixels across all monitors.
- **DPI scaling**: Existing DPI awareness set at startup. Coordinates are physical pixels.
