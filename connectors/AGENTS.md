# connectors/ — MCP wrappers · WF4

Your brief is `../docs/agents/WF4-data.md`.

## Your training data is wrong about this SDK

In `mcp` v2 the main class was renamed: **`FastMCP` became `MCPServer`**, and
`mcp.server.fastmcp` now raises `ModuleNotFoundError`. Pin `mcp>=2.3.0,<3`. Read
`../docs/research/mcp.md` before writing anything; nearly every snippet you
recall from training is v1.

```python
from mcp.server import MCPServer

mcp = MCPServer("github-connector")

@mcp.tool()
def pulls_comment(repo: str, number: int, body: str) -> str:
    """Post a comment on a pull request."""
    ...

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=7102)
```

## Tool names cannot contain dots

The pattern is `^[a-zA-Z0-9_-]{1,64}$`, and Claude Code **silently rewrites** a
dot to an underscore rather than erroring, which breaks round-tripping without
telling you. So `pulls.comment` is not a legal tool name.

| Layer | Value |
|---|---|
| Permission string (YAML, resolver, database) | `github:pulls.comment` |
| MCP tool name here | `pulls_comment` |
| What a client sees | `mcp__github__pulls_comment` |

**Read every name from `../contracts/connector_actions.json`. Never recompute
the transform.** 18 of our 30 action names differ from their wire name.

## Ports come from the registry YAML and nowhere else

memory **7100**, slack **7101**, github **7102**, platform **8000**. These are
read from `transport.url` in each connector file, which is the single source.
A collision means the second wrapper to start fails to bind, and it presents as
a routing bug for an hour.

## Rules

- Expose **only** the actions listed in the connector file. Never the whole
  GitHub or Slack API.
- Credentials are injected here and **never** returned to the caller or placed
  in model context.
- **Validate `Origin` and return 403** on a foreign one. The MCP spec requires
  it, and without it a browser-reachable local server is a DNS-rebinding hole.
  Cheap to add, easy to forget, and a security-minded judge may ask.
- A granted action missing from the server is `E_CONNECTOR_DRIFT` and must fail
  **closed and loudly**, never silently grant nothing.

## Transport

Streamable HTTP. The 2026-07-28 revision removed the `initialize` handshake,
protocol sessions, `Mcp-Session-Id`, the standalone `GET /mcp`, and
`Last-Event-ID` resume. Required headers on every POST:
`MCP-Protocol-Version: 2026-07-28`, `Mcp-Method`, `Mcp-Name` for tool calls, and
`Accept: application/json, text/event-stream`.
