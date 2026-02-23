# CV Plugin Setup

Verify and install the Computer Vision plugin dependencies.

1. Check that `uv` is installed: `uv --version`
2. Run `uv sync --directory "${CLAUDE_PLUGIN_ROOT}"` to install all dependencies
3. Verify the MCP server starts: `uv run --directory "${CLAUDE_PLUGIN_ROOT}" python -c "from src.server import mcp; print('Server OK:', len(mcp._tool_manager._tools), 'tools registered')"`
4. Report the result to the user
