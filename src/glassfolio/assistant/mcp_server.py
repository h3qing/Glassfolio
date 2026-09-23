"""A thin MCP server over the assistant's read tools (spec §8), for local MCP clients.

Red line (spec §7): real data may only reach a model running on this machine.
An MCP server can't see what model its client uses, so it starts only when the
person states the client is local (`--client-is-local`). Write tools are not
exposed: changes are confirmed in Glassfolio itself.

stdout carries the protocol; everything else goes to stderr.
"""

import asyncio
import json
import sys

from glassfolio.assistant.tools import TOOLS, Context, ToolError, run_tool
from glassfolio.lake import Lake

WARNING = ("Glassfolio MCP server: every tool result (your portfolio data) goes to the MCP client and the "
           "model behind it. Only connect a client that uses a model running on this Mac.")


def read_tools():
    return {name: t for name, t in TOOLS.items() if not t.writes and name != "request_user_file"}


def tool_schema(tool) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(tool.required),
            "properties": {k: {"type": "string", "description": v} for k, v in tool.params.items()}}


def call(ctx: Context, name: str, arguments: dict) -> str:
    """One tool call → JSON text (errors are returned as data, not raised)."""
    if name not in read_tools():
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        return json.dumps(run_tool(ctx, name, arguments or {}), default=str)
    except (ToolError, ValueError) as exc:
        return json.dumps({"error": str(exc)})


def serve_stdio(lake: Lake, today) -> None:
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=n, description=t.description, inputSchema=tool_schema(t))
            for n, t in read_tools().items()])

    lock = asyncio.Lock()  # one DuckDB connection: one call at a time

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        async with lock:
            text = await asyncio.to_thread(call, Context(lake, today()), params.name, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                    is_error='"error"' in text[:12])

    server = Server("glassfolio", instructions=WARNING, on_list_tools=list_tools, on_call_tool=call_tool)

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    print(WARNING, file=sys.stderr)
    asyncio.run(main())
