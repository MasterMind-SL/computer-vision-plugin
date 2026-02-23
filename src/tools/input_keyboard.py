"""MCP tools for keyboard input: text typing and key combinations."""

from __future__ import annotations

import logging

from src.server import mcp
from src import config
from src.errors import make_error, make_success, INPUT_FAILED, INVALID_INPUT
from src.utils.security import check_rate_limit, guard_dry_run, log_action
from src.utils.win32_input import type_unicode_string, send_key_combo

logger = logging.getLogger(__name__)


@mcp.tool()
def cv_type_text(text: str) -> dict:
    """Type a string of text at the current cursor position using Unicode input.

    Args:
        text: The text to type. Maximum length is controlled by CV_MAX_TEXT_LENGTH (default 1000).
    """
    try:
        if not text:
            return make_error(INVALID_INPUT, "Text must not be empty.")

        if len(text) > config.MAX_TEXT_LENGTH:
            return make_error(
                INVALID_INPUT,
                f"Text length {len(text)} exceeds maximum {config.MAX_TEXT_LENGTH}.",
            )

        check_rate_limit()

        params = {"text": text}
        dry = guard_dry_run("cv_type_text", params)
        if dry is not None:
            return dry

        log_action("cv_type_text", params, "start")

        ok = type_unicode_string(text)
        log_action("cv_type_text", params, "ok" if ok else "fail")

        if not ok:
            return make_error(INPUT_FAILED, "SendInput failed for text typing.")

        return make_success(action="type_text", length=len(text))

    except Exception as e:
        return make_error(INPUT_FAILED, str(e))


@mcp.tool()
def cv_send_keys(keys: str) -> dict:
    """Send a keyboard shortcut or key combination (e.g., "ctrl+c", "alt+tab", "ctrl+shift+s").

    Args:
        keys: Key combination string with parts separated by "+".
              Supported modifiers: ctrl, shift, alt, win/meta/cmd.
              Supported keys: a-z, 0-9, f1-f12, enter, tab, escape, backspace, delete,
              space, up, down, left, right, home, end, pageup, pagedown, insert.
    """
    try:
        if not keys or not keys.strip():
            return make_error(INVALID_INPUT, "Keys must not be empty.")

        check_rate_limit()

        params = {"keys": keys}
        dry = guard_dry_run("cv_send_keys", params)
        if dry is not None:
            return dry

        log_action("cv_send_keys", params, "start")

        ok = send_key_combo(keys)
        log_action("cv_send_keys", params, "ok" if ok else "fail")

        if not ok:
            return make_error(INPUT_FAILED, f"SendInput failed for key combo: {keys!r}")

        return make_success(action="send_keys", keys=keys)

    except Exception as e:
        return make_error(INPUT_FAILED, str(e))
