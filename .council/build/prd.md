# PRD: Desktop Computer Vision Plugin for Claude Code

## 1. Problem Statement

Claude Code can only "see" browser content via the Claude-in-Chrome extension. Everything else on the user's Windows desktop — IDEs, terminal emulators, design tools, game engines, CAD software, system dialogs — is invisible. Users must manually describe what's on screen or paste screenshots, breaking the agentic workflow. A local MCP plugin that grants Claude Code full computer vision and input control across any Windows window closes this gap entirely, enabling true desktop-level AI assistance equivalent to (and beyond) what Claude-in-Chrome provides for the browser.

## 2. Target Users

- **Claude Code power users** who work across multiple Windows applications and want Claude to see, understand, and interact with their full desktop environment.
- **Developers and engineers** using native IDEs (Visual Studio, IntelliJ, VS Code), terminal emulators, database GUIs, and other non-browser tools.
- **Automation enthusiasts** who want Claude Code to drive UI workflows across arbitrary Windows apps.
- **QA/testing professionals** who need Claude to visually inspect and interact with desktop applications.

## 3. Success Metrics

| Metric | Target |
|--------|--------|
| Screenshot capture latency (single window) | < 500ms |
| Window enumeration latency | < 100ms |
| Mouse/keyboard action latency | < 50ms |
| Mouse click placement accuracy | Within 2px of target |
| OCR accuracy on standard UI text | > 90% |
| Multi-monitor discovery | Automatic, zero config |
| Plugin install complexity | `uv sync` only |
| Windows compatibility | Windows 10 21H2+ and Windows 11 |

## 4. Core Features (ALL MANDATORY)

### F1 — Window Enumeration
List all visible top-level windows. Returns: HWND (as integer), title, process name, window class, PID, bounding rect (x, y, w, h) in screen-absolute coordinates, monitor index, is_minimized, is_maximized, is_foreground. Optional `include_children` parameter for child window enumeration. Handles UWP/modern apps via appropriate window class detection.

**Implementation:** `pywin32` `EnumWindows` + `GetWindowText` + `GetWindowRect` + `GetWindowThreadProcessId` + `GetClassName`.

### F2 — Screenshot Capture (Window)
Capture a specific window by HWND. Returns base64-encoded PNG with metadata including bounding rect (so Claude can map visual positions to coordinates), physical resolution, and logical resolution. Supports `max_width` parameter for downscaling (default: 1280px) to manage context window size.

**Implementation:** `mss` with per-window rect cropping. Fallback: `win32gui.PrintWindow` for off-screen/occluded windows. DPI-aware via `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`.

### F3 — Screenshot Capture (Full Desktop)
Capture the entire virtual desktop across all monitors. Returns base64-encoded PNG with metadata. Supports `max_width` downscaling and optional `quality` parameter for JPEG encoding to reduce size.

**Implementation:** `mss` grab of the full virtual screen rect.

### F4 — Screen Region Capture / Zoom
Capture an arbitrary rectangular region (x0, y0, x1, y1) in screen-absolute coordinates. Used for close inspection of small UI elements. Returns base64-encoded PNG with region metadata.

**Implementation:** `mss` with explicit region coordinates.

### F5 — Window Focus / Activation
Bring a window to the foreground by HWND. Auto-restores minimized windows via `ShowWindow(SW_RESTORE)` before activation. Handles cross-process activation via `AttachThreadInput` workaround.

**Implementation:** `win32gui.SetForegroundWindow` with `AttachThreadInput` pattern.

### F6 — Mouse Click
Click at (x, y) screen-absolute coordinates. Supports: `left_click`, `right_click`, `double_click`, `middle_click`. Optional `coordinate_space` parameter accepting `screen_absolute` (default) or `window_relative` (with HWND). Supports click-and-drag via start/end coordinates.

**Implementation:** `ctypes` `SendInput` with `MOUSEINPUT` — lower-level, more reliable than pyautogui, no extra dependency.

### F7 — Keyboard Input (Type Text)
Type a text string to the foreground window. Handles arbitrary Unicode characters via `KEYEVENTF_UNICODE` flag. Separate from key combinations.

**Implementation:** `ctypes` `SendInput` with `KEYBDINPUT` using `KEYEVENTF_UNICODE`.

### F8 — Keyboard Input (Send Keys)
Send key combinations and special keys to the foreground window. Supports: modifier combos (Ctrl+C, Alt+Tab, Win+R), special keys (Enter, Tab, Escape, arrow keys, F1-F12). Keys specified as space-separated strings (e.g., "ctrl+c", "alt+tab").

**Implementation:** `ctypes` `SendInput` with `KEYBDINPUT` using virtual key codes and `VkKeyScanW`.

### F9 — Window Resize / Move
Move or resize a window by HWND. Accept target rect (x, y, w, h). Support maximize, minimize, and restore commands. Validate against monitor bounds but allow off-screen if explicitly requested.

**Implementation:** `win32gui.MoveWindow` + `ShowWindow` for maximize/minimize/restore.

### F10 — OCR / Text Extraction
Extract text from a screenshot image (base64 input) or directly from a window/region. Returns structured JSON with text content and bounding boxes per text region so Claude can correlate text to screen positions.

**Implementation:** Windows native `Windows.Media.Ocr` (WinRT API) via `winocr` package — zero additional binaries, ships with Windows 10/11, GPU-accelerated. Fallback to `pytesseract` if WinRT is unavailable.

### F11 — Multi-Monitor Support
Enumerate all monitors with: index, name, resolution (physical and logical), position in virtual desktop, scale factor (DPI), and primary flag. Used by Claude to understand the display topology.

**Implementation:** `win32api.EnumDisplayMonitors` + `GetMonitorInfo` + `GetDpiForMonitor`. Supplemented by `mss` monitor geometry.

### F12 — UI Accessibility Tree (Read UI)
Read the UI Automation accessibility tree for a window, returning structured element data (name, type, bounding rect, interactive state, value). Analogous to Claude-in-Chrome's `read_page` tool — gives Claude structured element data without relying on vision alone. Supports depth limiting and filtering for interactive elements only.

**Implementation:** Windows UI Automation API via `comtypes` or `pywinauto`'s UIA backend.

### F13 — Wait / Synchronization
Wait for a condition: `wait_for_window(title_pattern, timeout)` — waits for a window matching the pattern to appear. `wait(seconds)` — simple delay. Used to handle loading states, window transitions, and app startup.

**Implementation:** Polling loop with `EnumWindows` and configurable timeout.

## 5. User Stories

- As a developer, I want Claude to screenshot my IDE so it can see my code layout and suggest improvements.
- As a user, I want Claude to list all open windows so it can find the one I'm referring to by name.
- As a tester, I want Claude to click buttons in a desktop app to automate a test flow.
- As a power user, I want Claude to type text into a form field in any Windows application.
- As a multi-monitor user, I want Claude to capture any screen across my display setup without configuration.
- As an automation user, I want Claude to read text from any window via OCR for data extraction.
- As a user, I want Claude to resize and arrange my windows programmatically.
- As a developer, I want Claude to read the UI tree of an app so it can identify elements without relying on screenshots alone.
- As a user, I want Claude to wait for an app to finish loading before interacting with it.
- As a user, I want Claude to send keyboard shortcuts (Ctrl+S, Alt+Tab) to any application.

## 6. Non-Functional Requirements

### Architecture
- Follow the-council plugin pattern: `.claude-plugin/plugin.json`, `.mcp.json`, `src/server.py` with `@mcp.tool()` (FastMCP), `src/__main__.py` with `mcp.run(transport="stdio")`, `pyproject.toml` with `uv` + `hatchling`.
- Skills directory for slash commands (e.g., `/cv:setup`, `/cv:help`).
- All tools return structured JSON with consistent `success`/`error` fields.

### Performance
- Screenshot capture: < 500ms for any single window
- Window enumeration: < 100ms
- Mouse/keyboard actions: < 50ms
- OCR extraction: < 2s for a full window capture

### Coordinate System
- All coordinates use **screen-absolute virtual desktop coordinates** (matching Windows `GetCursorPos` / `SetCursorPos`) by default.
- Click and input tools accept an optional `coordinate_space` parameter: `screen_absolute` (default) or `window_relative` (requires HWND).
- All screenshot responses include bounding rect metadata so Claude can map visual positions to coordinates.
- All coordinates are in **physical pixels** (DPI-aware).

### DPI / Scaling Awareness
- Plugin calls `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` at startup.
- All coordinate transformations account for per-monitor DPI scaling.
- Screenshot metadata includes both physical resolution and logical resolution.
- Monitor enumeration includes scale factor per monitor.

### Error Handling
Structured error responses for all failure modes:
- `WINDOW_NOT_FOUND` — stale HWND or window closed
- `WINDOW_MINIMIZED` — target window is minimized (auto-restore attempted)
- `ACCESS_DENIED` — elevated/UAC-protected process
- `SCREEN_LOCKED` — screen is locked
- `TIMEOUT` — operation exceeded timeout
- `INVALID_COORDINATES` — coordinates outside all monitor bounds
- `OCR_UNAVAILABLE` — no OCR engine available

### Security
- Plugin runs at user privilege level — no elevation.
- Configurable `restricted_processes` list to block interaction with sensitive apps (default: empty).
- All input injection actions (click, type, send_keys) are logged.
- Optional `dry_run` mode that returns planned actions without executing.
- Cannot interact with UAC prompts or credential dialogs (reports `ACCESS_DENIED`).

### Dependencies (Minimal)
- `mcp>=1.26.0` — MCP protocol
- `mss` — fast screen capture
- `pywin32` — Windows API (window management)
- `Pillow` — image processing/encoding
- `winocr` — Windows native OCR (zero external binaries)
- `comtypes` or `pywinauto` — UI Automation accessibility tree
- No heavy ML frameworks. No network calls. All processing local.

### Image Format
- Screenshots returned as base64-encoded PNG in tool responses.
- Default `max_width: 1280` downscaling to manage context size.
- Optional JPEG encoding with `quality` parameter for further size reduction.

## 7. Assumptions & Constraints

- **Windows only** — this plugin uses Win32 API and is not cross-platform.
- **Python 3.11+** required (matching pyproject.toml standard).
- **Claude Code can interpret images** — base64 PNG in MCP tool responses is rendered by Claude's vision model.
- **User-level permissions** — the plugin cannot interact with admin-elevated windows or system-protected UI.
- **Foreground requirement for input** — mouse clicks and keyboard input require the target window to be in the foreground (the plugin will auto-focus before input).
- **No video/streaming** — the plugin captures static screenshots, not live video feeds.
- **MCP stdio transport** — all communication is via stdin/stdout JSON-RPC, no HTTP server needed.
