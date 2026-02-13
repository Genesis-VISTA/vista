from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from .agent import get_agent

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
    agent = get_agent()
    return await VercelAIAdapter.dispatch_request(request, agent=agent)


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
