from pathlib import Path

from fastapi import FastAPI

app = FastAPI(title="ERGOLIFE 商品全生命周期协同 MVP", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ergolife-feishu-workflow"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ergolife-feishu-workflow", "phase": "workflow-core"}

