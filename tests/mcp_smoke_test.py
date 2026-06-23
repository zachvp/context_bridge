#!/usr/bin/env python3
"""
Local MCP smoke test (beat 6). Spawns server.py as a real MCP server process
over stdio and talks to it with the official MCP client — verifying the
protocol round-trip (tool discovery, call, response shape) in isolation,
before involving Claude Code's own MCP client as a second variable.

Usage:
    python3 mcp_smoke_test.py
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_SCRIPT = Path(__file__).parent.parent / "server.py"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "ok" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


async def main() -> int:
    server_params = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            tool_names = {t.name for t in tools_result.tools}
            check(
                "both tools discovered",
                {"search_chat_history", "get_conversation"} <= tool_names,
                f"got {tool_names}",
            )

            search_result = await session.call_tool(
                "search_chat_history", {"query_text": "substrate dictionary semantic primitives", "top_k": 3}
            )
            check("search_chat_history call did not error", not search_result.isError)
            check("search_chat_history returned content", len(search_result.content) > 0)

            # tool results come back as a structured content block; pull the
            # parsed list back out to check it has the shape server.py promises
            hits = search_result.structuredContent.get("result") if search_result.structuredContent else None
            check("search result is a non-empty list", isinstance(hits, list) and len(hits) > 0)
            if hits:
                first = hits[0]
                check(
                    "hit has expected fields",
                    {"id", "title", "source_type", "timestamp", "score", "snippet"} <= set(first.keys()),
                    f"got {set(first.keys())}",
                )
                raw_id = first["id"]
                # id formats: "<uuid>:<n>" (conversation) or "code:<uuid>:<n>" (code_session)
                parts = raw_id.split(":")
                conversation_uuid = parts[1] if raw_id.startswith("code:") else parts[0]

                conv_result = await session.call_tool("get_conversation", {"conversation_uuid": conversation_uuid})
                check("get_conversation call did not error", not conv_result.isError)
                conv_text = conv_result.content[0].text if conv_result.content else ""
                check("get_conversation returned non-empty text", len(conv_text) > 0)

    print(f"\n{len(failures)} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))