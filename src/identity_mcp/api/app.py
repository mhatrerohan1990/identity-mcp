"""The FastAPI application object.

Routes: ``/health``, ``POST /api/ask`` (the same pipeline as the MCP ``ask`` tool), ``/`` (chat UI), the REST routers
from ``routes.py``, and the MCP server mounted last at ``/`` (endpoint ``/mcp``)."""

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import pipeline
from ..context.conversation import Turn
from ..core.logs import configure
from ..mcp_server import mcp
from .routes import ALL

STATIC = Path(__file__).parent / "static"

# The MCP app is mounted last (below) at "/", serving its endpoint at /mcp. A mounted app's lifespan
# is not run by the parent, so start it here.
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Startup/shutdown for the whole app: configure logging, and run the mounted MCP app's own lifespan (a mounted app's lifespan is not started automatically by its parent).
    """
    configure()
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(
    title="identity-mcp API", version="0.1.0", description="Read-only access-governance demo data.", lifespan=lifespan
)


@app.get("/health", tags=["meta"])
def health():
    """Liveness probe. Does not touch the database or the model."""
    return {"status": "ok"}


class AskRequest(BaseModel):
    """Body of ``POST /api/ask``.

    Attributes:
        question: One non-empty question of at most 1000 characters.
        history: The earlier turns of this conversation, oldest first (at most 6 accepted; the last 3 are used).
            Empty for a first question.
    """
    question: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=6)


@app.post("/api/ask", tags=["chat"])
async def ask(body: AskRequest):
    """Same pipeline as the MCP `ask` tool; used by the chat page."""
    return await pipeline.ask(body.question.strip(), history=body.history)


@app.get("/", include_in_schema=False)
def chat_page():
    """Serve the single-file chat UI."""
    return FileResponse(STATIC / "index.html")


for router in ALL:
    app.include_router(router)

app.mount("/", mcp_app)  # keep last: FastAPI routes above take precedence
