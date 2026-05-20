"""
api.py - FastAPI: serve o painel e expoe a API REST + SSE
Com autenticacao JWT, gerenciamento de apps e config de IA por usuario.
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from utils.context_store import (
    get_client,
    K_RUN_STREAM,
    # user-namespaced
    save_user_instructions, load_user_instructions,
    get_user_apps, save_user_apps,
    get_user_ai_config, save_user_ai_config,
    get_user_history, save_user_run,
    get_user_context, update_user_context,
    get_user_failure_counts,
    # queue/status (global)
    enqueue_job, list_queue, queue_length,
    get_run_status, set_run_status,
)
from utils.auth import hash_password, verify_password, create_token, decode_token
from jose import JWTError

app = FastAPI(title="QA Automation Panel", version="2.0.0")
PANEL_PATH = Path(__file__).parent / "panel.html"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ─────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────

async def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    if not token:
        raise HTTPException(status_code=401, detail="Nao autenticado")
    try:
        return decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalido ou expirado")


def _get_user_profile(r, email: str) -> dict | None:
    raw = r.get(f"qa:auth:{email}")
    return json.loads(raw) if raw else None


def _save_user_profile(r, profile: dict):
    r.set(f"qa:auth:{profile['email']}", json.dumps(profile, ensure_ascii=False))


# ─────────────────────────────────────────────────────────────
# Modelos Pydantic
# ─────────────────────────────────────────────────────────────

class RegisterPayload(BaseModel):
    name: str
    email: str
    password: str

class LoginPayload(BaseModel):
    email: str
    password: str

class InstructionsPayload(BaseModel):
    general: list = []
    pages:   list = []
    flows:   list = []

class AppPayload(BaseModel):
    name:          str
    url:           str
    login_enabled: bool = False
    username:      str  = ""
    password:      str  = ""

class AiConfigPayload(BaseModel):
    provider: str = "openai"
    model:    str = "gpt-4o-mini"
    api_key:  str = ""

class QueueJobPayload(BaseModel):
    type:    str  = "full"
    label:   str  = "Run completo"
    app_id:  str  = ""
    config_override: dict = {}


# ─────────────────────────────────────────────────────────────
# Pagina principal
# ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_panel():
    if not PANEL_PATH.exists():
        raise HTTPException(status_code=404, detail="panel.html nao encontrado")
    return HTMLResponse(content=PANEL_PATH.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# Auth endpoints
# ─────────────────────────────────────────────────────────────

@app.post("/auth/register")
async def register(payload: RegisterPayload):
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter pelo menos 6 caracteres")
    r = get_client()
    if _get_user_profile(r, payload.email):
        raise HTTPException(status_code=400, detail="Email ja cadastrado")
    profile = {
        "email":           payload.email,
        "name":            payload.name,
        "hashed_password": hash_password(payload.password),
        "created_at":      time.time(),
    }
    _save_user_profile(r, profile)
    token = create_token(payload.email)
    return {"token": token, "email": payload.email, "name": payload.name}


@app.post("/auth/login")
async def login(payload: LoginPayload):
    r = get_client()
    profile = _get_user_profile(r, payload.email)
    if not profile or not verify_password(payload.password, profile["hashed_password"]):
        raise HTTPException(status_code=401, detail="Email ou senha incorretos")
    token = create_token(payload.email)
    return {"token": token, "email": payload.email, "name": profile.get("name", "")}


@app.get("/auth/me")
async def me(email: str = Depends(get_current_user)):
    r = get_client()
    profile = _get_user_profile(r, email)
    if not profile:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
    return {"email": profile["email"], "name": profile.get("name", "")}


# ─────────────────────────────────────────────────────────────
# Apps (por usuario)
# ─────────────────────────────────────────────────────────────

@app.get("/api/apps")
async def list_apps(email: str = Depends(get_current_user)):
    r = get_client()
    return get_user_apps(r, email)


@app.post("/api/apps")
async def create_app(payload: AppPayload, email: str = Depends(get_current_user)):
    r = get_client()
    apps = get_user_apps(r, email)
    app_obj = {
        "id":            str(uuid.uuid4())[:8],
        "name":          payload.name,
        "url":           payload.url.rstrip("/"),
        "login_enabled": payload.login_enabled,
        "username":      payload.username,
        "password":      payload.password,
        "created_at":    time.time(),
    }
    apps.append(app_obj)
    save_user_apps(r, email, apps)
    return app_obj


@app.put("/api/apps/{app_id}")
async def update_app(app_id: str, payload: AppPayload, email: str = Depends(get_current_user)):
    r = get_client()
    apps = get_user_apps(r, email)
    for i, a in enumerate(apps):
        if a["id"] == app_id:
            apps[i] = {**a, "name": payload.name, "url": payload.url.rstrip("/"),
                       "login_enabled": payload.login_enabled,
                       "username": payload.username, "password": payload.password}
            save_user_apps(r, email, apps)
            return apps[i]
    raise HTTPException(status_code=404, detail="App nao encontrada")


@app.delete("/api/apps/{app_id}")
async def delete_app(app_id: str, email: str = Depends(get_current_user)):
    r = get_client()
    apps = get_user_apps(r, email)
    apps = [a for a in apps if a["id"] != app_id]
    save_user_apps(r, email, apps)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# AI Config (por usuario)
# ─────────────────────────────────────────────────────────────

@app.get("/api/ai-config")
async def get_ai_config(email: str = Depends(get_current_user)):
    r = get_client()
    cfg = get_user_ai_config(r, email)
    # Nunca retorna a api_key completa — apenas mascara
    masked = {**cfg, "api_key": ("*" * 8 + cfg["api_key"][-4:]) if len(cfg.get("api_key","")) > 8 else ("*" * len(cfg.get("api_key","")))}
    return masked


@app.put("/api/ai-config")
async def update_ai_config(payload: AiConfigPayload, email: str = Depends(get_current_user)):
    r = get_client()
    # Se api_key for so asteriscos (mascara), mantem a anterior
    existing = get_user_ai_config(r, email)
    api_key = payload.api_key if not all(c == "*" for c in payload.api_key) else existing.get("api_key", "")
    cfg = {"provider": payload.provider, "model": payload.model, "api_key": api_key}
    save_user_ai_config(r, email, cfg)
    return {"ok": True, "provider": cfg["provider"], "model": cfg["model"]}


# ─────────────────────────────────────────────────────────────
# Instrucoes (por usuario)
# ─────────────────────────────────────────────────────────────

@app.get("/api/instructions")
async def get_instructions(email: str = Depends(get_current_user)):
    r = get_client()
    return load_user_instructions(r, email)


@app.post("/api/instructions")
async def post_instructions(payload: InstructionsPayload, email: str = Depends(get_current_user)):
    r = get_client()
    save_user_instructions(r, email, payload.general, payload.pages, payload.flows)
    return {"ok": True, "saved_at": time.time()}


# ─────────────────────────────────────────────────────────────
# Fila de testes
# ─────────────────────────────────────────────────────────────

@app.get("/api/queue")
async def get_queue(email: str = Depends(get_current_user)):
    r = get_client()
    return {"length": queue_length(r), "jobs": list_queue(r)}


@app.post("/api/queue")
async def add_to_queue(payload: QueueJobPayload, email: str = Depends(get_current_user)):
    r = get_client()
    job = {
        "type":            payload.type,
        "label":           payload.label,
        "app_id":          payload.app_id,
        "user_email":      email,
        "config_override": payload.config_override,
        "queued_at":       time.time(),
    }
    enqueue_job(r, job)
    return {"ok": True, "queue_length": queue_length(r)}


@app.delete("/api/queue")
async def clear_queue(email: str = Depends(get_current_user)):
    r = get_client()
    r.delete("qa:queue")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Historico (por usuario)
# ─────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history_endpoint(limit: int = 20, email: str = Depends(get_current_user)):
    r = get_client()
    runs = get_user_history(r, email, limit=limit)
    return {"runs": runs, "total": len(runs)}


# ─────────────────────────────────────────────────────────────
# Contexto acumulado (por usuario)
# ─────────────────────────────────────────────────────────────

@app.get("/api/context")
async def get_context(email: str = Depends(get_current_user)):
    r = get_client()
    ctx      = get_user_context(r, email)
    failures = get_user_failure_counts(r, email)
    return {
        "accumulated": ctx,
        "failure_counts": failures,
        "high_risk": sorted(failures.items(), key=lambda x: x[1], reverse=True)[:10],
    }


@app.delete("/api/context")
async def reset_context(email: str = Depends(get_current_user)):
    r = get_client()
    from utils.context_store import _uk
    r.delete(_uk(email, "context"), _uk(email, "failures"))
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
# SSE
# ─────────────────────────────────────────────────────────────

async def _sse_generator() -> AsyncGenerator[str, None]:
    import redis.asyncio as aioredis
    r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe(K_RUN_STREAM)
    last_hb = time.time()
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg["type"] == "message":
                yield f"data: {msg['data']}\n\n"
            if time.time() - last_hb > 15:
                yield f"data: {json.dumps({'type':'heartbeat'})}\n\n"
                last_hb = time.time()
            await asyncio.sleep(0.1)
    finally:
        await pubsub.unsubscribe(K_RUN_STREAM)
        await r.aclose()


@app.get("/api/events")
async def sse_events():
    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENV", "production") == "development",
    )
