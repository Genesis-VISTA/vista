# VISTA (Visual Intelligence for Scientific & Tooling Assistant)

## MCP Server + Agent (Local Development)

This repository contains a minimal MCP server built with FastMCP and a simple client (“agent”) used to validate end-to-end connectivity.

---

## Running locally

### Prerequisites
- Python 3.12+
- `uv` installed
- A virtual environment activated (or let `uv` manage installs)

### Install dependencies

From the repository root:

```bash
uv pip install -e .
```

### Start the MCP server (HTTP mode)

Run the server as a module: 

```python -m project_name.app```

By default, the server starts at: [http://127.0.0.1:8000/mcp](http://127.0.0.1:8000/mcp)


### Test the smoke agent (client)

In a second terminal (with the server still running):

```python clients/smoke_agent.py```


Expected output is a successful response from the echo tool, confirming that:

- the MCP server is running
- the client can connect
- tools are registered and callable

## Repository structure

```
vista/
├─ clients/                      # MCP clients (“agents”) that call server tools
│  ├─ __init__.py                
│  └─ smoke_agent.py             # connectivity test client
├─ experiments/                  # hold scripts for capabilities/skills
│  └─ "analyze_<material>.py"
├─ scripts/                      # helper scripts (run server/clients, dev helpers)
├─ tests/                 
├─ src/
│  └─ vista/              
│     ├─ __init__.py
│     ├─ app.py                  # FastMCP server (registers tools/resources)
│     └─ tools/                  # server-side tool implementations
│        ├─ __init__.py
│        └─ echo.py                 
├─ pyproject.toml                
├─ uv.lock                       
├─ README.md                     
└─ .gitignore                    
```

References (temporary):

- [Agent skills doc](https://agentskills.io/home)
- [Integrating MCP tools with semantic kernel](https://devblogs.microsoft.com/semantic-kernel/integrating-model-context-protocol-tools-with-semantic-kernel-a-step-by-step-guide/?utm_source=chatgpt.com)
- [Goose for MCP](https://block.github.io/goose/docs/getting-started/installation)
- [FastMCP](https://block.github.io/goose/docs/getting-started/installation) 