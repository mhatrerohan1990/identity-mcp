from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..mcp_server import mcp
from .routes import ALL

# The MCP app is mounted last (below) at "/", serving its endpoint at /mcp. A mounted app's lifespan
# is not run by the parent, so start it here.
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp")


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp_app.router.lifespan_context(mcp_app):
        yield


app = FastAPI(
    title="GrantLens API", version="0.1.0", description="Read-only access-governance demo data.", lifespan=lifespan
)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


for router in ALL:
    app.include_router(router)

app.mount("/", mcp_app)  # keep last: FastAPI routes above take precedence
