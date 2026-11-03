from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import pipeline
from ..mcp_server import mcp
from .routes import ALL

STATIC = Path(__file__).parent / "static"

# The MCP app is mounted last (below) at "/", serving its endpoint at /mcp. A mounted app's lifespan
# is not run by the parent, so start it here.
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp")


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(
    title="identity-mcp API", version="0.1.0", description="Read-only access-governance demo data.", lifespan=lifespan
)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@app.post("/api/ask", tags=["chat"])
async def ask(body: AskRequest):
    """Same pipeline as the MCP `ask` tool; used by the chat page."""
    return await pipeline.ask(body.question.strip())


@app.get("/", include_in_schema=False)
def chat_page():
    return FileResponse(STATIC / "index.html")


for router in ALL:
    app.include_router(router)

app.mount("/", mcp_app)  # keep last: FastAPI routes above take precedence
