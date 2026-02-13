# VISTA MCP Server

A minimal MCP server built with FastMCP and a simple client (“agent”) used to validate end-to-end connectivity.

## Running locally

### Prerequisites
- Python 3.12+
- `uv` installed
- A virtual environment activated (or let `uv` manage installs)

### Install dependencies

From the repository root:

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e .[dev]
```

### Build MCP Apps
```bash
cd mcp-apps
npm install
npm run build
```

### Start the MCP server (HTTP mode)

Run the server as a module: 

```bash
python -m vista.app --transport http
```

By default, the server starts at: [http://127.0.0.1:8000/mcp](http://127.0.0.1:8000/mcp)

To use stdio transport:

```bash
python -m vista.app --transport stdio
```


### Test the smoke agent (client)

In a second terminal (with the server still running):

```bash
python clients/smoke_agent.py
```

Expected output is a successful response from the echo tool, confirming that:

- the MCP server is running
- the client can connect
- tools are registered and callable

