# Computer Vision Plugin for Claude Code

Desktop computer vision and input control for Claude Code on Windows. Like Claude-in-Chrome, but for **any Windows application**.

## What It Does

This MCP plugin gives Claude Code the ability to see and interact with any window on your Windows desktop:

- **Screenshot** any window, the full desktop, or a specific screen region
- **List windows** with title, process, position, and monitor info
- **Click** anywhere on screen (left/right/double/middle/drag)
- **Type text** and **send keyboard shortcuts** to any application
- **OCR** — extract text from any window with bounding boxes
- **Read UI trees** via Windows UI Automation (like `read_page` for desktop apps)
- **Multi-monitor** support with DPI awareness
- **Wait** for windows to appear before interacting

## Installation

Inside Claude Code:

```
/plugin marketplace add MasterMind-SL/Marketplace
/plugin install computer-vision@mastermind-marketplace
```

Then restart Claude Code and run `/cv-setup` to verify dependencies.

### Manual (development)

```bash
git clone https://github.com/MasterMind-SL/computer-vision-plugin
cd computer-vision-plugin
uv sync
claude --plugin-dir .
```

## Requirements

- Windows 10 21H2+ or Windows 11
- Python 3.11+
- `uv` package manager

## Tools Reference

| Tool | Description |
|------|-------------|
| `cv_list_windows` | List all visible windows with HWND, title, process, rect |
| `cv_screenshot_window` | Capture a specific window by HWND (base64 PNG) |
| `cv_screenshot_desktop` | Capture the entire desktop (all monitors) |
| `cv_screenshot_region` | Capture a rectangular region of the screen |
| `cv_focus_window` | Bring a window to the foreground |
| `cv_mouse_click` | Click at screen coordinates (left/right/double/middle/drag) |
| `cv_type_text` | Type text into the foreground window (Unicode) |
| `cv_send_keys` | Send key combinations (Ctrl+S, Alt+Tab, etc.) |
| `cv_move_window` | Move/resize a window or maximize/minimize/restore |
| `cv_ocr` | Extract text from a window or region with bounding boxes |
| `cv_list_monitors` | List all monitors with resolution, DPI, and position |
| `cv_read_ui` | Read the UI accessibility tree of a window |
| `cv_wait_for_window` | Wait for a window matching a title pattern to appear |
| `cv_wait` | Simple delay (max 30 seconds) |

## Quick Start

**List windows and take a screenshot:**
1. `cv_list_windows` — see all open windows
2. Find the HWND of your target window
3. `cv_screenshot_window(hwnd=<HWND>)` — Claude sees the window

**Click a button in an app:**
1. `cv_screenshot_window` — see current state
2. Identify button coordinates from the screenshot
3. `cv_mouse_click(x=<X>, y=<Y>)` — click it

**Read text from any app:**
1. `cv_ocr(hwnd=<HWND>)` — extract all visible text with positions

**Automate a workflow:**
1. `cv_list_windows` — find target app
2. `cv_focus_window` — bring it to front
3. `cv_type_text` / `cv_send_keys` — interact
4. `cv_screenshot_window` — verify result

## Configuration

Environment variables (all optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `CV_RESTRICTED_PROCESSES` | `credential manager,keepass,1password,bitwarden,windows security` | Comma-separated process names blocked from input |
| `CV_DRY_RUN` | `false` | Return planned actions without executing |
| `CV_DEFAULT_MAX_WIDTH` | `1280` | Default screenshot downscale width |
| `CV_MAX_TEXT_LENGTH` | `1000` | Max characters for `cv_type_text` |
| `CV_RATE_LIMIT` | `20` | Max input actions per second |
| `CV_AUDIT_LOG_PATH` | `%LOCALAPPDATA%/claude-cv-plugin/audit.jsonl` | Audit log location |
| `CV_OCR_REDACTION_PATTERNS` | (empty) | Regex patterns to redact from OCR output |

## Security

- Runs at **user privilege level** only — no elevation
- **Restricted processes** blocklist prevents interaction with password managers and sensitive apps
- **Rate limiting** (20 actions/sec) prevents runaway automation
- **HWND freshness validation** prevents acting on stale window handles
- **Audit logging** records all input actions to structured JSON log
- **Dry-run mode** lets you preview actions without executing
- **OCR redaction** masks sensitive text patterns in OCR output
- Cannot interact with UAC prompts or credential dialogs

## Architecture

```
src/
├── __main__.py          # Entry point (DPI init + server start)
├── server.py            # FastMCP with auto-registration
├── config.py            # Settings from environment variables
├── errors.py            # Structured error types
├── models.py            # Pydantic models
├── dpi.py               # DPI awareness helpers
├── coordinates.py       # Coordinate transforms
├── tools/               # MCP tool definitions (14 tools)
│   ├── windows.py       # F1, F5, F9
│   ├── capture.py       # F2, F3, F4
│   ├── input_mouse.py   # F6
│   ├── input_keyboard.py # F7, F8
│   ├── ocr.py           # F10
│   ├── monitors.py      # F11
│   ├── accessibility.py # F12
│   └── synchronization.py # F13
└── utils/               # Shared utilities
    ├── screenshot.py     # mss + PrintWindow capture
    ├── win32_input.py    # ctypes SendInput wrappers
    ├── win32_window.py   # pywin32 window management
    ├── security.py       # Security gate + audit log
    └── uia.py            # UI Automation tree walker
```

## Dependencies

- `mcp` — MCP protocol
- `mss` — Fast screen capture (DXGI)
- `pywin32` — Windows API
- `Pillow` — Image processing
- `winocr` — Windows native OCR
- `comtypes` — UI Automation
- `pydantic` — Input validation

## License

MIT
