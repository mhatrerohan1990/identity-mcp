from fastapi import FastAPI

from .routes import ALL

app = FastAPI(title="GrantLens API", version="0.1.0", description="Read-only access-governance demo data.")


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


for router in ALL:
    app.include_router(router)
