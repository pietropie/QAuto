"""
api.py - FastAPI: serve o painel e expoe a API REST + SSE

Endpoints:
  GET  /                       -> panel.html
  GET  /api/instructions       -> le instrucoes do Redis
  POST /api/instructions       -> salva instrucoes no Redis
  GET  /api/queue              -> lista a fila atual
  POST /api/queue              -> adiciona job na fila
  GET  /api/history            -> historico de runs
  GET  /api/context            -> contexto acumulado da IA
  GET  /api/status             -> status do run atual
  GET  /api/events             -> SSE stream de updates em tempo real
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from utils.context_store import (
    get_client,
    save_instructions,
    load_instructions,
    enqueue_job,
    list_queue,
    queue_length,
    get_history,
    get_accumulated_context,
    get_failure_counts,
    get_run_status,
    K_RUN_STREAM,
)

app = FastAPI(title="QA Automation Panel", version="1.0.0")

PANEL_PATH = Path(__file__).parent / "panel.html"


# ─────────────────────────────────────────────────────────────
# Modelos Pydantic
# ─────────────────────────────────────────────────────────────

class InstructionsPayload(BaseModel):
    general: list = []
    pages:   list = []
    flows:   list = []


class QueueJobPayload(BaseModel):
    type:    str = "full"       # "full" | "custom" | "navegacao" | "visual" | "pedidos" | "formularios"
    label:   str = "Run completo"
    config_override: dict = {}  # overrides pontuais de config


# ─────────────────────────────────────────────────────────────
# Pagina principal
# ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_panel():
    if not PANEL_PATH.exists():
        raise HTTPException(status_code=404, detail="panel.html nao encontrado")
    return HTMLResponse(content=PANEL_PATH.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# Instrucoes
# ─────────────────────────────────────────────────────────────

@app.get("/api/instructions")
async def get_instructions():
    r = get_client()
    return load_instructions(r)


@app.post("/api/instructions")
async def post_instructions(payload: InstructionsPayload):
    r = get_client()
    save_instructions(r, payload.general, payload.pages, payload.flows)
    return {"ok": True, "saved_at": time.time()}


# ─────────────────────────────────────────────────────────────
# Fila de testes
# ─────────────────────────────────────────────────────────────

@app.get("/api/queue")
async def get_queue():
    r = get_client()
    return {
        "length": queue_length(r),
        "jobs":   list_queue(r),
    }


@app.post("/api/queue")
async def add_to_queue(payload: QueueJobPayload):
    r = get_client()
    job = {
        "type":            payload.type,
        "label":           payload.label,
        "config_override": payload.config_override,
        "queued_at":       time.time(),
    }
    enqueue_job(r, job)
    return {"ok": True, "queue_length": queue_length(r)}


@app.delete("/api/queue")
async def clear_queue():
    r = get_client()
    r.delete("qa:queue")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Historico
# ─────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history_endpoint(limit: int = 20):
    r = get_client()
    runs = get_history(r, limit=limit)
    return {"runs": runs, "total": len(runs)}


# ─────────────────────────────────────────────────────────────
# Contexto acumulado
# ─────────────────────────────────────────────────────────────

@app.get("/api/context")
async def get_context():
    r = get_client()
    ctx      = get_accumulated_context(r)
    failures = get_failure_counts(r)
    return {
        "accumulated": ctx,
        "failure_counts": failures,
        "high_risk": sorted(failures.items(), key=lambda x: x[1], reverse=True)[:10],
    }


@app.delete("/api/context")
async def reset_context():
    """Limpa todo o contexto acumulado (util para comecar do zero)."""
    r = get_client()
    r.delete("qa:context:accumulated", "qa:context:failures")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Status do run atual
# ─────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    r = get_client()
    status = get_run_status(r)
    return status or {"state": "idle"}


# ─────────────────────────────────────────────────────────────
# SSE - eventos em tempo real para o painel
# ─────────────────────────────────────────────────────────────

async def _sse_generator() -> AsyncGenerator[str, None]:
    """
    Assina o canal pub/sub do Redis e repassa cada mensagem como evento SSE.
    Tambem envia um heartbeat a cada 15s para manter a conexao aberta.
    """
    import redis.asyncio as aioredis

    r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                          decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(K_RUN_STREAM)

    last_heartbeat = time.time()
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg["type"] == "message":
                yield f"data: {msg['data']}\n\n"

            if time.time() - last_heartbeat > 15:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
                last_heartbeat = time.time()

            await asyncio.sleep(0.1)
    finally:
        await pubsub.unsubscribe(K_RUN_STREAM)
        await r.aclose()


@app.get("/api/events")
async def sse_events():
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":   "no-cache",
            "X-Accel-Buffering": "no",   # necessario para Nginx/Coolify
        },
    )


# ─────────────────────────────────────────────────────────────
# Entrypoint local
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENV", "production") == "development",
    )
