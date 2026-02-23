# CV Plugin Help

Show available Computer Vision tools and usage examples.

## Available Tools

| Tool | Description |
|------|-------------|
| `cv_list_windows` | List all visible windows with HWND, title, process, rect |
| `cv_screenshot_window` | Capture a specific window by HWND |
| `cv_screenshot_desktop` | Capture the entire desktop (all monitors) |
| `cv_screenshot_region` | Capture a rectangular region of the screen |
| `cv_focus_window` | Bring a window to the foreground |
| `cv_mouse_click` | Click at screen coordinates (left/right/double/middle/drag) |
| `cv_type_text` | Type text into the foreground window |
| `cv_send_keys` | Send key combinations (Ctrl+S, Alt+Tab, etc.) |
| `cv_move_window` | Move/resize a window or maximize/minimize/restore |
| `cv_ocr` | Extract text from a window or region with bounding boxes |
| `cv_list_monitors` | List all monitors with resolution, DPI, and position |
| `cv_read_ui` | Read the UI accessibility tree of a window |
| `cv_wait_for_window` | Wait for a window matching a title pattern to appear |
| `cv_wait` | Simple delay (max 30 seconds) |

## Quick Start Examples

**List windows and take a screenshot:**
1. Call `cv_list_windows` to see all open windows
2. Find the HWND of the window you want
3. Call `cv_screenshot_window` with that HWND

**Click a button in an app:**
1. `cv_screenshot_window` to see the current state
2. Identify the button coordinates from the screenshot
3. `cv_mouse_click` at those coordinates

**Read text from any app:**
1. `cv_ocr` with the window's HWND to extract all visible text
