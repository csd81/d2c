# Phase 33: Built-in MCP Server Mode (Universal IDE Integration)

**Paper Reference:** Section 3.2, 6.1 — "MCP servers configure from multiple scopes... each connected server contributes tool definitions as MCPTool objects... MCP's donation to the Linux Foundation reflects the ecosystem dimension."

**Priority:** HIGH (Interoperability & IDE Integration)

## Rationale

Currently, `d2c` only operates as an MCP *client* (it can consume external tools) and is otherwise confined to the terminal as a CLI or REPL. 

To expand its utility, we will implement a built-in **MCP Server Mode** (`python -m d2c mcp`). Exposing `d2c`'s advanced, safety-gated, and compaction-wrapped tools (like the custom `FileEdit`, `Bash` with job sandboxing, and `Agent` subagent delegation) over a stdio-based MCP transport allows developers to connect `d2c` directly to visual environments like Cursor, Zed, or VS Code (via the Cline extension).

---

## Files to Create/Modify

1. CREATE `src/d2c/mcp/server.py` — stdio-based MCP server implementation
2. MODIFY `src/d2c/main.py` — add `--mcp` command line flag to launch the server
3. CREATE `tests/test_mcp_server.py` — verify stdio handshake, tool listing, and tool execution protocol

---

## Key Design

The MCP Server implements the standard Model Context Protocol over stdio transport:

```
[ IDE (Client: Cline/Cursor) ] ◄─── stdio json-rpc ───► [ d2c MCP Server ]
                                                           │
                                                           ▼
                                                    [ d2c Tool Pool ]
                                                (Bash, Edit, Agent, etc.)
```

### 1. JSON-RPC Protocol Message Handlers
The server must read from `stdin` and write to `stdout` using JSON-RPC 2.0 messages:
* **Initialize**: Handles protocol version handshake and capability negotiations.
* **List Tools** (`tools/list`): Translates `d2c`'s built-in tool definitions (`to_api_format()`) into MCP tool definitions.
* **Call Tool** (`tools/call`): Executes a specific tool by name with arguments, returning the output as an MCP content block.

### 2. Implementation: `src/d2c/mcp/server.py`

```python
import sys
import json
import asyncio
from d2c.config import Config
from d2c.tools.pool import assembleToolPool

class MCPServer:
    """Stdio JSON-RPC server for Model Context Protocol."""

    def __init__(self, config: Config):
        self.config = config
        self._loop = asyncio.get_event_loop()

    async def start(self) -> None:
        """Read loop from stdin, execute tools, write to stdout."""
        # Initialize tool pool
        self.tools = await assembleToolPool(self.config)
        self.tools_map = {t.name: t for t in self.tools}

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await self._loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            await self._handle_message(line)

    async def _handle_message(self, line: bytes) -> None:
        try:
            request = json.loads(line)
        except Exception:
            return

        method = request.get("method")
        msg_id = request.get("id")

        if method == "initialize":
            response = self._handle_initialize(msg_id)
        elif method == "tools/list":
            response = self._handle_list_tools(msg_id)
        elif method == "tools/call":
            response = await self._handle_call_tool(msg_id, request.get("params", {}))
        else:
            response = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
```

### 3. Safety and Sandbox Integration
When running in MCP server mode:
* The server must load the `WorkspaceTrustManager` (from Phase 32). If the IDE connects to the server in an untrusted directory, the server must automatically restrict command executions or refuse to list dangerous tools.
* Stdout redirecting is critical: Since the MCP communication happens over `stdout`, any tools (like `BashTool`) that output directly to stdout or launch subprocesses must be redirected cleanly so they do not pollute the JSON-RPC pipe.

---

## Edge Cases

* **Subprocess Output Leakage**: Subprocesses started by `BashTool` might write directly to the parent process's file descriptors. We must redirect `sys.stdout` internally to `sys.stderr` when the server is active, ensuring that only valid JSON-RPC frames are printed to the stdout descriptor.
* **Concurrency**: IDEs might send multiple `tools/call` requests concurrently. The server must route read-only tools concurrently, while serializing write operations, matching the execution design in `loop.py`.

---

## Tests

Verify the following:
* `test_mcp_handshake`: Verifies client can connect and negotiate protocol features.
* `test_mcp_list_tools`: Verifies all registered tools (including Glob/Grep/FileEdit) are listed correctly.
* `test_mcp_call_tool_read`: Verifies calling `Read` returns file contents inside the MCP text block.
* `test_mcp_call_tool_bash_redirection`: Verifies calling `Bash` executes commands correctly and subprocess output does not contaminate the JSON-RPC stdout channel.
