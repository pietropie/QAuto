"""
api.py - FastAPI: serve o painel e expoe a API REST + SSE

Endpoints:
  POST /auth/register          -> cadastro de usuario
  POST /auth/login             -> login, retorna JWT
  GET  /auth/me                -> dados do usuario logado
  GET  /                       -> panel.html
  GET  /api/instructions       -> le instrucoes do Redis (por usuario)
  POST /api/instructions       -> salva instrucoes no Redis (por usuario)
  GET  /api/queue              -> lista a fila atual
  POST /api/queue              -> adiciona job na fila
  GET  /api/history            -> historico de runs (por usuario)
  GET  /api/context            -> contexto acumulado da IA
  GET  /api/status             -> status do run atual
  GET  /api/events             -> SSE stream de updates em tempo real
  GET  /api/apps               -> apps do usuario
  POST /api/apps               -> cria app
  PUT  /api/apps/{id}          -> atualiza app
  DELETE /api/apps/{id}        -> remove app
  GET  /api/ai-config          -> config de IA do usuario
  PUT  /api/ai-config          -> salva config de IA do usuario
"""

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
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
    # funções por usuario
    get_user_apps,
    save_user_apps,
    get_user_ai_config,
    save_user_ai_config,
    save_user_instructions,
    load_user_instructions,
    get_user_history,
)
from utils.auth import hash_password, verify_password, create_token, decode_token

app = FastAPI(title="QA Automation Panel", version="2.0.0")

PANEL_PATH = Path(__file__).parent / "panel.html"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ─────────────────────────────────────────────────────────────
# Helpers de autenticacao
# ─────────────────────────────────────────────────────────────

def _user_key(email: str) -> str:
    return f"qa:auth:{email}"


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """Retorna o perfil do usuario ou levanta 401."""
    if not token:
        raise HTTPException(status_code=401, detail="Nao autenticado")
    try:
        email = decode_token(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalido ou expirado")
    r = get_client()
    raw = r.get(_user_key(email))
    if not raw:
        raise HTTPException(status_code=401, detail="Usuario nao encontrado")
    return json.loads(raw)


# ─────────────────────────────────────────────────────────────
# Modelos Pydantic
# ─────────────────────────────────────────────────────────────

class RegisterPayload(BaseModel):
    email:    str
    password: str
    name:     Optional[str] = ""


class LoginPayload(BaseModel):
    email:    str
    password: str


class InstructionsPayload(BaseModel):
    general: list = []
    pages:   list = []
    flows:   list = []


class QueueJobPayload(BaseModel):
    type:            str  = "full"
    label:           str  = "Run completo"
    product_filter:  str  = "all"
    app_id:          Optional[str] = None
    config_override: dict = {}


class AppPayload(BaseModel):
    name:     str
    base_url: str
    login_enabled: bool = False
    login_url:  str = "/login"
    username:   str = ""
    password:   str = ""
    username_selector: str = ""
    password_selector: str = ""
    submit_selector:   str = ""
    success_indicator: str = ""


class AiConfigPayload(BaseModel):
    provider:  str = "openai"
    model:     str = "gpt-4o-mini"
    api_key:   str = ""


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────

@app.post("/auth/register")
async def register(payload: RegisterPayload):
    try:
        r = get_client()
        key = _user_key(payload.email)
        if r.exists(key):
            raise HTTPException(status_code=409, detail="Email ja cadastrado")
        user = {
            "email":      payload.email,
            "name":       payload.name or payload.email.split("@")[0],
            "password":   hash_password(payload.password),
            "created_at": time.time(),
        }
        r.set(key, json.dumps(user))
        token = create_token(payload.email)
        return {"token": token, "email": payload.email, "name": user["name"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")


@app.post("/auth/login")
async def login(payload: LoginPayload):
    try:
        r = get_client()
        raw = r.get(_user_key(payload.email))
        if not raw:
            raise HTTPException(status_code=401, detail="Email ou senha incorretos")
        user = json.loads(raw)
        if not verify_password(payload.password, user["password"]):
            raise HTTPException(status_code=401, detail="Email ou senha incorretos")
        token = create_token(payload.email)
        return {"token": token, "email": payload.email, "name": user.get("name", "")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")


@app.get("/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {
        "email": current_user["email"],
        "name":  current_user.get("name", ""),
    }




@app.put('/auth/password')
async def change_password(payload: dict, current_user: dict = Depends(get_current_user)):
    """Altera a senha do usuario autenticado (sem exigir senha antiga)."""
    try:
        new_pw = payload.get('new_password', '')
        if not new_pw:
            raise HTTPException(status_code=400, detail='Informe a nova senha')
        if len(new_pw) < 6:
            raise HTTPException(status_code=400, detail='Nova senha deve ter pelo menos 6 caracteres')
        r = get_client()
        current_user['password'] = hash_password(new_pw)
        r.set(_user_key(current_user['email']), json.dumps(current_user))
        return {'ok': True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'{type(e).__name__}: {str(e)}')

# ─────────────────────────────────────────────────────────────
# Pagina principal
# ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_panel():
    if not PANEL_PATH.exists():
        raise HTTPException(status_code=404, detail="panel.html nao encontrado")
    return HTMLResponse(content=PANEL_PATH.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────
# Apps (por usuario)
# ─────────────────────────────────────────────────────────────

@app.get("/api/apps")
async def get_apps(current_user: dict = Depends(get_current_user)):
    r = get_client()
    return get_user_apps(r, current_user["email"])


@app.post("/api/apps")
async def create_app(payload: AppPayload, current_user: dict = Depends(get_current_user)):
    r = get_client()
    apps = get_user_apps(r, current_user["email"])
    new_app = {"id": str(uuid.uuid4()), **payload.dict()}
    apps.append(new_app)
    save_user_apps(r, current_user["email"], apps)
    return new_app


@app.put("/api/apps/{app_id}")
async def update_app(app_id: str, payload: AppPayload, current_user: dict = Depends(get_current_user)):
    r = get_client()
    apps = get_user_apps(r, current_user["email"])
    for i, a in enumerate(apps):
        if a["id"] == app_id:
            apps[i] = {"id": app_id, **payload.dict()}
            save_user_apps(r, current_user["email"], apps)
            return apps[i]
    raise HTTPException(status_code=404, detail="App nao encontrado")


@app.delete("/api/apps/{app_id}")
async def delete_app(app_id: str, current_user: dict = Depends(get_current_user)):
    r = get_client()
    apps = get_user_apps(r, current_user["email"])
    apps = [a for a in apps if a["id"] != app_id]
    save_user_apps(r, current_user["email"], apps)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Config IA (por usuario)
# ─────────────────────────────────────────────────────────────

@app.get("/api/ai-config")
async def get_ai_config(current_user: dict = Depends(get_current_user)):
    r = get_client()
    cfg = get_user_ai_config(r, current_user["email"])
    if "api_key" in cfg and cfg["api_key"]:
        cfg["api_key"] = "***"  # nunca retorna a chave real
    return cfg


@app.put("/api/ai-config")
async def update_ai_config(payload: AiConfigPayload, current_user: dict = Depends(get_current_user)):
    r = get_client()
    existing = get_user_ai_config(r, current_user["email"])
    data = payload.dict()
    # Preserva a chave existente se o cliente enviar "***"
    if data.get("api_key") == "***":
        data["api_key"] = existing.get("api_key", "")
    save_user_ai_config(r, current_user["email"], data)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# Instrucoes (por usuario quando autenticado, global como fallback)
# ─────────────────────────────────────────────────────────────

@app.get("/api/instructions")
async def get_instructions(token: str = Depends(oauth2_scheme)):
    r = get_client()
    if token:
        try:
            email = decode_token(token)
            return load_user_instructions(r, email)
        except JWTError:
            pass
    return load_instructions(r)


@app.post("/api/instructions")
async def post_instructions(payload: InstructionsPayload, token: str = Depends(oauth2_scheme)):
    r = get_client()
    if token:
        try:
            email = decode_token(token)
            save_user_instructions(r, email, payload.general, payload.pages, payload.flows)
            return {"ok": True, "saved_at": time.time()}
        except JWTError:
            pass
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
async def add_to_queue(payload: QueueJobPayload, token: str = Depends(oauth2_scheme)):
    r = get_client()
    user_email = None
    if token:
        try:
            user_email = decode_token(token)
        except JWTError:
            pass
    job = {
        "type":            payload.type,
        "label":           payload.label,
        "product_filter":  payload.product_filter,
        "app_id":          payload.app_id,
        "config_override": payload.config_override,
        "user_email":      user_email,
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
# Historico (por usuario quando autenticado)
# ─────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history_endpoint(limit: int = 20, token: str = Depends(oauth2_scheme)):
    r = get_client()
    if token:
        try:
            email = decode_token(token)
            runs = get_user_history(r, email, limit=limit)
            return {"runs": runs, "total": len(runs)}
        except JWTError:
            pass
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
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────
# Entrypoint local
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
