"""pywin32 wrappers for window enumeration, focus, and management."""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path
from typing import Any

import win32api
import win32con
import win32gui
import win32process

from src.errors import WindowNotFoundError
from src.models import Rect, WindowInfo

logger = logging.getLogger(__name__)

MONITOR_DEFAULTTONEAREST = 2


def _get_process_name(pid: int) -> str:
    """Get the executable name (without extension) for a given PID."""
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            pid,
        )
        try:
            exe = win32process.GetModuleFileNameEx(handle, 0)
            return Path(exe).stem.lower()
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def _get_monitor_index(hwnd: int) -> int:
    """Get the 1-based monitor index for the monitor containing a window."""
    try:
        hmon = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        monitors = win32api.EnumDisplayMonitors(None, None)
        for i, (hm, _hdc, _rect) in enumerate(monitors):
            if int(hm) == hmon:
                return i
        return 0
    except Exception:
        return 0


def _build_window_info(hwnd: int) -> WindowInfo | None:
    """Build a WindowInfo from a window handle. Returns None if info cannot be gathered."""
    try:
        title = win32gui.GetWindowText(hwnd)
        rect_tuple = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        class_name = win32gui.GetClassName(hwnd)
        process_name = _get_process_name(pid)
        monitor_index = _get_monitor_index(hwnd)

        placement = win32gui.GetWindowPlacement(hwnd)
        show_cmd = placement[1]
        is_minimized = show_cmd == win32con.SW_SHOWMINIMIZED
        is_maximized = show_cmd == win32con.SW_SHOWMAXIMIZED

        foreground_hwnd = win32gui.GetForegroundWindow()

        return WindowInfo(
            hwnd=hwnd,
            title=title,
            process_name=process_name,
            class_name=class_name,
            pid=pid,
            rect=Rect(
                x=rect_tuple[0],
                y=rect_tuple[1],
                width=rect_tuple[2] - rect_tuple[0],
                height=rect_tuple[3] - rect_tuple[1],
            ),
            monitor_index=monitor_index,
            is_minimized=is_minimized,
            is_maximized=is_maximized,
            is_foreground=(hwnd == foreground_hwnd),
        )
    except Exception as exc:
        logger.debug("Failed to get info for HWND %s: %s", hwnd, exc)
        return None


def enum_windows(include_children: bool = False) -> list[WindowInfo]:
    """Enumerate all visible top-level windows.

    Args:
        include_children: If True, also enumerate child windows.

    Returns:
        List of WindowInfo for each visible window.
    """
    results: list[WindowInfo] = []

    def _callback(hwnd: int, _extra: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True

        info = _build_window_info(hwnd)
        if info is not None:
            results.append(info)

            if include_children:
                _enum_child_windows(hwnd, results)

        return True

    win32gui.EnumWindows(_callback, None)
    return results


def _enum_child_windows(parent_hwnd: int, results: list[WindowInfo]) -> None:
    """Enumerate visible child windows of a parent."""

    def _child_callback(hwnd: int, _extra: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        info = _build_window_info(hwnd)
        if info is not None:
            results.append(info)
        return True

    try:
        win32gui.EnumChildWindows(parent_hwnd, _child_callback, None)
    except Exception as exc:
        logger.debug("Failed to enumerate children of HWND %s: %s", parent_hwnd, exc)


def get_window_info(hwnd: int) -> WindowInfo:
    """Get detailed information about a specific window.

    Args:
        hwnd: Window handle.

    Returns:
        WindowInfo for the window.

    Raises:
        WindowNotFoundError: If the window is invalid.
    """
    if not is_window_valid(hwnd):
        raise WindowNotFoundError(hwnd)

    info = _build_window_info(hwnd)
    if info is None:
        raise WindowNotFoundError(hwnd)
    return info


def focus_window(hwnd: int) -> bool:
    """Bring a window to the foreground.

    Restores minimized windows before focusing. Uses AttachThreadInput
    workaround for cross-process activation.

    Args:
        hwnd: Window handle to focus.

    Returns:
        True if the window was successfully brought to the foreground.
    """
    if not is_window_valid(hwnd):
        return False

    try:
        # Restore if minimized
        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_SHOWMINIMIZED:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        # Use AttachThreadInput trick for cross-process focus
        foreground_hwnd = win32gui.GetForegroundWindow()
        if foreground_hwnd != hwnd:
            fg_thread, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

            if fg_thread != target_thread:
                ctypes.windll.user32.AttachThreadInput(fg_thread, target_thread, True)
                try:
                    win32gui.SetForegroundWindow(hwnd)
                finally:
                    ctypes.windll.user32.AttachThreadInput(fg_thread, target_thread, False)
            else:
                win32gui.SetForegroundWindow(hwnd)
        else:
            win32gui.SetForegroundWindow(hwnd)

        return True
    except Exception as exc:
        logger.warning("Failed to focus window HWND %s: %s", hwnd, exc)
        return False


def move_window(hwnd: int, x: int, y: int, width: int, height: int) -> Rect:
    """Move and resize a window.

    Args:
        hwnd: Window handle.
        x: New left position.
        y: New top position.
        width: New width.
        height: New height.

    Returns:
        The new Rect after moving.
    """
    if not is_window_valid(hwnd):
        raise WindowNotFoundError(hwnd)

    win32gui.MoveWindow(hwnd, x, y, width, height, True)

    # Read back the actual position
    rect_tuple = win32gui.GetWindowRect(hwnd)
    return Rect(
        x=rect_tuple[0],
        y=rect_tuple[1],
        width=rect_tuple[2] - rect_tuple[0],
        height=rect_tuple[3] - rect_tuple[1],
    )


def is_window_valid(hwnd: int) -> bool:
    """Check if a window handle is still valid."""
    return bool(ctypes.windll.user32.IsWindow(hwnd))
