from contextlib import asynccontextmanager
from typing import Annotated as A

from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, AnyUrl
from pydantic_ai import Agent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.mcp import MCPServer
from mcp import ReadResourceResult, Tool as MCPTool
from mcp.shared import exceptions as mcp_exceptions

from .agent import make_agent
from .config import settings
from .sandbox import DockerSandbox

agent: Agent
AgentDep = A[Agent, Depends(lambda: agent)]

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)

    global agent
    async with await DockerSandbox.spawn(
        volumes=[
            (settings.uploads_dir, "/mnt/user-data/uploads", 'r'),
            (settings.outputs_dir, "/mnt/user-data/outputs", 'w'),
        ],
    ) as sandbox:
        agent = make_agent(sandbox)
        async with agent:
            yield

app = FastAPI(title="Vista Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str

@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/chat")
async def chat(request: Request, agent: AgentDep) -> Response:
    return await VercelAIAdapter.dispatch_request(request, agent=agent)


class UploadResponse(BaseModel):
    filename: str

@app.post("/upload")
async def upload(file: UploadFile, filename: str|None = None) -> UploadResponse:
    filename = filename or file.filename
    if not filename:
        raise ValueError("No filename passed")
    dest = settings.uploads_dir / filename
    dest.write_bytes(await file.read())
    return UploadResponse(filename=filename)


class ListUploadsResponse(BaseModel):
    files: list[str]

@app.get("/uploads")
async def list_uploads() -> ListUploadsResponse:
    if not settings.uploads_dir.exists():
        files = []
    else:
        files = [
            str(f.relative_to(settings.uploads_dir))
            for f in settings.uploads_dir.rglob("*")
            if f.is_file()
        ]
    return ListUploadsResponse(files=files)


@app.get("/download/{path:path}")
async def download(path: str) -> FileResponse:
    file_path = settings.data_dir / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


@app.get("/mcp/tools")
async def list_tools() -> list[MCPTool]:
    """ List MCP tools with their UI resource URIs. """
    mcp_servers = [s for s in agent.toolsets if isinstance(s, MCPServer)]

    result: list[MCPTool] = []
    for server in mcp_servers:
        result += await server.list_tools()
    return result


@app.get("/mcp/resources")
async def read_mcp_resource(uri: str) -> ReadResourceResult:
    """ Proxy MCP resource reading """
    mcp_servers = [s for s in agent.toolsets if isinstance(s, MCPServer)]

    for server in mcp_servers:
        try:
            # Using ._client.read_resource instead of .read_resource as PydanticAI loses mime type of text fields
            return await server._client.read_resource(AnyUrl(uri))
        except mcp_exceptions.McpError:
            # TODO: Check for only missing resource error
            continue
    raise HTTPException(status_code=404, detail="Resource not found")


def main():
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
