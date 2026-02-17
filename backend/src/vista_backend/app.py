from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from .agent import agent
from .config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
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
async def chat(request: Request) -> Response:
    return await VercelAIAdapter.dispatch_request(request, agent=agent)


class UploadResponse(BaseModel):
    filename: str

@app.post("/upload")
async def upload(file: UploadFile, filename: str|None = None) -> UploadResponse:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    filename = filename or file.filename
    if not filename:
        raise ValueError("No filename passed")
    dest = settings.data_dir / filename
    dest.write_bytes(await file.read())
    return UploadResponse(filename=filename)


class ListUploadsResponse(BaseModel):
    files: list[str]

@app.get("/uploads")
async def list_uploads() -> ListUploadsResponse:
    if not settings.data_dir.exists():
        files = []
    else:
        files = [
            str(f.relative_to(settings.data_dir))
            for f in settings.data_dir.rglob("*")
            if f.is_file()
        ]
    return ListUploadsResponse(files=files)


def main():
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
