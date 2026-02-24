# Vista MCP Server

FastMCP server that provides tools to the Vista backend agent.

## MCP Apps

This MCP server provides some [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview) to provide
interactive tool result widgets, under ./mcp-server/mcp-apps.


## Setup

Build MCP Apps
```bash
cd mcp-apps
npm install
npm run build
cd ..
```

Run in stdio transport
```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e .[dev]
uvx --env-file ../.env --refresh .
```

To run in http transport mode
```bash
uvx --env-file ../.env --refresh . --transport http
```

## Configuration

| Env variable             | Description                                                                    |
| ------------------------ | ------------------------------------------------------------------------------ |
| `VISTA_MCP_ALLOWED_URIS` | JSON array of regex parters for permitted URIS in the display_file tool        |
| `VISTA_MCP_URI_MAP`      | JSON object mapping URI prefixes to replacement URLs for the display_file tool |
