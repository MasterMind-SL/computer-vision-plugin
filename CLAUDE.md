# Computer Vision Plugin for Claude Code

## Overview
This is an MCP plugin that gives Claude Code full computer vision and input control across any Windows application. It provides 14 tools for screenshots, window management, mouse/keyboard input, OCR, UI accessibility, and multi-monitor support.

## Architecture
- **MCP server**: FastMCP over stdio transport (never HTTP/SSE)
- **Entry point**: `python -m src` → `src/__main__.py` → DPI init → server start
- **Tool registration**: Auto-discovered from `src/tools/` — never edit `server.py` to add tools
- **Tool prefix**: All tools start with `cv_`

## Coding Conventions
- **Models**: Use Pydantic `BaseModel` for all data models (not dataclasses)
- **Errors**: Use `make_error(code, message)` and `make_success(**payload)` from `src/errors.py`
- **Security**: All mutating tools (F5-F9) must call security gate before execution:
  1. `validate_hwnd_fresh(hwnd)` — check window still exists
  2. `check_restricted(process_name)` — block restricted processes
  3. `check_rate_limit()` — enforce rate limit
  4. `guard_dry_run(tool, params)` — return early if dry-run
  5. `log_action(tool, params, status)` — audit log
- **Coordinates**: Screen-absolute physical pixels by default. DPI awareness set at startup.
- **Screenshots**: Return base64 PNG via `encode_image()`. Use `capture_window_raw()` internally for OCR.
- **Imports**: Tool files import `mcp` from `src.server`, NOT create their own FastMCP instance.

## Testing
- Unit tests in `tests/unit/` with mocked Win32 APIs
- Integration tests in `tests/integration/` require real Windows desktop
- Run: `uv run pytest tests/unit/ -v`

## OCR
- `cv_ocr` auto-detects installed Windows OCR languages — does NOT require `en-US`.
- Language fallback order: `en`, `es`, `es-MX`, `pt`, `fr`, `de`, `it`, `ja`, `zh-Hans`, `ko`.
- Pytesseract is a secondary fallback if `winocr` is unavailable.

## Dependencies
mcp, mss, pywin32, Pillow, winocr, comtypes, pydantic — all installed via `uv sync`.

## Distribution

The plugin marketplace repo is `MasterMind-SL/Marketplace`. Install command:
```
/plugin marketplace add MasterMind-SL/Marketplace
/plugin install computer-vision@mastermind-marketplace
```

For development:
```bash
claude --plugin-dir /path/to/computer-vision-plugin
```
