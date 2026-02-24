# Vista Backend

Vista agentic AI backend.

Uses [Pydantic AI](https://ai.pydantic.dev/) and FastAPI. Streams chat responses to the frontend via
the [Vercel AI SDK protocol](https://ai.pydantic.dev/ui/vercel-ai/).

Uses the MCP server from [mcp-server](../mcp-server/).

## Setup
Build [mcp-server](../mcp-server/README.md) first. Then

```bash
uv venv --python=3.12 .venv
source .venv/bin/activate
uv pip install -e .[dev]
```

## Running
Make sure you've setup `../.env` (see [README](../README.md#running-locally))

```bash
python -m vista_backend.app
```

Use `MODEL=dummy` to run without an API key (returns canned responses).
