"""Windows UI Automation tree walker via comtypes."""

from __future__ import annotations

import logging
import threading
from typing import Any

import comtypes
import comtypes.client

from src import config
from src.models import Rect, UiaElement

logger = logging.getLogger(__name__)

# COM CLSIDs and IIDs for UI Automation
CLSID_CUIAutomation = comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
IID_IUIAutomation = comtypes.GUID("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")

# Control type IDs for interactive elements
INTERACTIVE_CONTROL_TYPES: set[int] = {
    50000,  # Button
    50002,  # CheckBox
    50003,  # ComboBox
    50004,  # Edit
    50005,  # Hyperlink
    50011,  # MenuItem
    50015,  # Slider
    50019,  # Tab
}

# Human-readable control type names
CONTROL_TYPE_NAMES: dict[int, str] = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
}

# Cached CUIAutomation instance
_uia_instance: Any = None


def init_uia() -> Any:
    """Initialize and cache the CUIAutomation COM object.

    Returns the IUIAutomation interface.
    """
    global _uia_instance
    if _uia_instance is not None:
        return _uia_instance

    _uia_instance = comtypes.CoCreateInstance(
        CLSID_CUIAutomation,
        interface=comtypes.gen.UIAutomationClient.IUIAutomation,
        clsctx=comtypes.CLSCTX_INPROC_SERVER,
    )
    logger.info("CUIAutomation COM object initialized")
    return _uia_instance


def _init_uia_raw() -> Any:
    """Initialize UIA using raw COM creation (fallback if type library not available)."""
    global _uia_instance
    if _uia_instance is not None:
        return _uia_instance

    try:
        _uia_instance = comtypes.CoCreateInstance(
            CLSID_CUIAutomation,
            interface=comtypes.gen.UIAutomationClient.IUIAutomation,
            clsctx=comtypes.CLSCTX_INPROC_SERVER,
        )
    except (AttributeError, ImportError):
        # Fallback: generate type library from UIAutomationCore.dll
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import IUIAutomation

        _uia_instance = comtypes.CoCreateInstance(
            CLSID_CUIAutomation,
            interface=IUIAutomation,
            clsctx=comtypes.CLSCTX_INPROC_SERVER,
        )

    logger.info("CUIAutomation COM object initialized")
    return _uia_instance


def _safe_init_uia() -> Any:
    """Try multiple initialization strategies."""
    global _uia_instance
    if _uia_instance is not None:
        return _uia_instance

    try:
        return init_uia()
    except (AttributeError, ImportError, OSError):
        return _init_uia_raw()


def get_ui_tree(
    hwnd: int,
    depth: int = 5,
    filter: str = "all",
) -> list[UiaElement]:
    """Walk the UI Automation tree for a window.

    Args:
        hwnd: Window handle to inspect.
        depth: Maximum tree depth to traverse. Default 5.
        filter: "all" for all elements, "interactive" for only
                Button/Edit/ComboBox/CheckBox/MenuItem/Link/Slider/Tab.

    Returns:
        List of UiaElement trees.
    """
    uia = _safe_init_uia()

    # Counter for generating ref_ids (mutable container for closure)
    counter = [0]
    interactive_only = filter == "interactive"

    # Use a thread with timeout to prevent hangs on unresponsive apps
    result_container: list[list[UiaElement]] = []
    error_container: list[Exception] = []

    def _walk_tree() -> None:
        try:
            root_element = uia.ElementFromHandle(hwnd)
            condition = uia.CreateTrueCondition()
            walker = uia.CreateTreeWalker(condition)

            elements = _walk_children(walker, root_element, depth, counter, interactive_only)
            result_container.append(elements)
        except Exception as exc:
            error_container.append(exc)

    thread = threading.Thread(target=_walk_tree, daemon=True)
    thread.start()
    thread.join(timeout=config.UIA_TIMEOUT)

    if thread.is_alive():
        logger.warning("UIA tree walk timed out after %.1fs for HWND %d", config.UIA_TIMEOUT, hwnd)
        raise TimeoutError(
            f"UI Automation tree walk timed out after {config.UIA_TIMEOUT}s for HWND {hwnd}"
        )

    if error_container:
        raise error_container[0]

    if result_container:
        return result_container[0]

    return []


def _walk_children(
    walker: Any,
    parent: Any,
    remaining_depth: int,
    counter: list[int],
    interactive_only: bool,
) -> list[UiaElement]:
    """Recursively walk child elements of a UIA parent node.

    Args:
        walker: IUIAutomationTreeWalker instance.
        parent: Parent IUIAutomationElement.
        remaining_depth: How many more levels to descend.
        counter: Mutable counter list for ref_id generation.
        interactive_only: If True, only include interactive control types.

    Returns:
        List of UiaElement for the children.
    """
    if remaining_depth <= 0:
        return []

    elements: list[UiaElement] = []

    try:
        child = walker.GetFirstChildElement(parent)
    except Exception:
        return elements

    while child is not None:
        try:
            control_type_id = child.CurrentControlType
            name = child.CurrentName or ""
            is_enabled = bool(child.CurrentIsEnabled)

            # Get bounding rectangle
            try:
                rect_val = child.CurrentBoundingRectangle
                rect = Rect(
                    x=int(rect_val.left),
                    y=int(rect_val.top),
                    width=int(rect_val.right - rect_val.left),
                    height=int(rect_val.bottom - rect_val.top),
                )
            except Exception:
                rect = Rect(x=0, y=0, width=0, height=0)

            is_interactive = control_type_id in INTERACTIVE_CONTROL_TYPES
            control_type_name = CONTROL_TYPE_NAMES.get(control_type_id, f"Unknown({control_type_id})")

            # Recurse into children regardless of filter
            children = _walk_children(
                walker, child, remaining_depth - 1, counter, interactive_only
            )

            # Include this element if filter allows, or if it has interactive descendants
            if not interactive_only or is_interactive or children:
                counter[0] += 1
                ref_id = f"ref_{counter[0]}"

                element = UiaElement(
                    ref_id=ref_id,
                    name=name,
                    control_type=control_type_name,
                    rect=rect,
                    value=None,
                    is_enabled=is_enabled,
                    is_interactive=is_interactive,
                    children=children,
                )
                elements.append(element)

            # Move to next sibling
            child = walker.GetNextSiblingElement(child)

        except Exception as exc:
            logger.debug("Error walking UIA element: %s", exc)
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                break

    return elements
