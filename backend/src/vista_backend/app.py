from fastapi import FastAPI, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from .agent import agent
from .config import settings

app = FastAPI(title="Vista Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: Request) -> Response:
    return await VercelAIAdapter.dispatch_request(request, agent=agent)


@app.post("/upload")
async def upload(file: UploadFile, filename: str|None):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    filename = filename or file.filename
    if not filename:
        raise ValueError("No filename passed")
    dest = settings.data_dir / filename
    dest.write_bytes(await file.read())
    return {"filename": filename}


def main():
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
