# VISTA - Visual Intelligence for Scientific & Tooling Assistant

## Architecture

- ./frontend: React based frontend
- ./backend: Pydantic AI based backend
- ./mcp-server: MCP server providing custom tools
- ./skills: Custom skills

## Running locally

Copy `.env.sample` to `.env` and set up your AI provider. You can use any provider Pydantic AI
supports, see https://ai.pydantic.dev/api/providers for the env vars to use for each.

Run:
```bash
./launch.sh
```

It can take a minute for the backend to fully boot.

The frontend will be hosted on http://localhost:5173
The backend at http://localhost:8000

## Running with Docker Compose

There's also a docker compose file. Set up your `.env` file as above. Then

```bash
docker compose up --build
```
