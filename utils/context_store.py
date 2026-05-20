"""
context_store.py - Camada Redis para o QA Automation

Chaves utilizadas:
  qa:instructions:general   -> JSON list  - instrucoes gerais do painel
  qa:instructions:pages     -> JSON list  - instrucoes por pagina
  qa:instructions:flows     -> JSON list  - fluxos/jornadas
  qa:queue                  -> Redis List - fila de jobs (RPUSH/BLPOP)
  qa:history                -> Redis ZSet - historico de runs (score=timestamp)
  qa:context:accumulated    -> JSON str   - contexto acumulado da IA
  qa:context:failures       -> Redis Hash - page_path -> contagem de falhas
  qa:run:status             -> JSON str   - status do run atual (para SSE)
"""

import json
import time
import os
import redis


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Prefixo de todas as chaves
K_INSTR_GENERAL   = "qa:instructions:general"
K_INSTR_PAGES     = "qa:instructions:pages"
K_INSTR_FLOWS     = "qa:instructions:flows"
K_QUEUE           = "qa:queue"
K_HISTORY         = "qa:history"
K_CTX_ACCUMULATED = "qa:context:accumulated"
K_CTX_FAILURES    = "qa:context:failures"
K_RUN_STATUS      = "qa:run:status"
K_RUN_STREAM      = "qa:run:stream"   # pub/sub channel


def get_client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


# ─────────────────────────────────────────────────────────────
# INSTRUCOES (painel)
# ─────────────────────────────────────────────────────────────

def save_instructions(r: redis.Redis, general: list, pages: list, flows: list):
    pipe = r.pipeline()
    pipe.set(K_INSTR_GENERAL, json.dumps(general, ensure_ascii=False))
    pipe.set(K_INSTR_PAGES,   json.dumps(pages,   ensure_ascii=False))
    pipe.set(K_INSTR_FLOWS,   json.dumps(flows,   ensure_ascii=False))
    pipe.execute()


def load_instructions(r: redis.Redis) -> dict:
    general = r.get(K_INSTR_GENERAL)
    pages   = r.get(K_INSTR_PAGES)
    flows   = r.get(K_INSTR_FLOWS)
    return {
        "general": json.loads(general) if general else [],
        "pages":   json.loads(pages)   if pages   else [],
        "flows":   json.loads(flows)   if flows   else [],
    }


# ─────────────────────────────────────────────────────────────
# FILA DE TESTES
# ─────────────────────────────────────────────────────────────

def enqueue_job(r: redis.Redis, job: dict):
    """Adiciona um job na fila. job deve ter pelo menos {'type': '...'}"""
    job.setdefault("queued_at", time.time())
    r.rpush(K_QUEUE, json.dumps(job, ensure_ascii=False))


def dequeue_job(r: redis.Redis, timeout: int = 30) -> dict | None:
    """Bloqueia ate receber um job. Retorna None se timeout."""
    result = r.blpop(K_QUEUE, timeout=timeout)
    if result:
        _, raw = result
        return json.loads(raw)
    return None


def queue_length(r: redis.Redis) -> int:
    return r.llen(K_QUEUE)


def list_queue(r: redis.Redis) -> list:
    items = r.lrange(K_QUEUE, 0, -1)
    return [json.loads(i) for i in items]


# ─────────────────────────────────────────────────────────────
# HISTORICO DE EXECUCOES
# ─────────────────────────────────────────────────────────────

def save_run_result(r: redis.Redis, run: dict):
    """
    Salva resultado de um run no historico.
    run deve ter: {id, started_at, finished_at, total, passed, failed, warned, suites:[...]}
    """
    run.setdefault("id", str(int(time.time())))
    score = run.get("started_at", time.time())
    r.zadd(K_HISTORY, {json.dumps(run, ensure_ascii=False): score})
    # Mantem apenas os ultimos 50 runs
    r.zremrangebyrank(K_HISTORY, 0, -51)


def get_history(r: redis.Redis, limit: int = 20) -> list:
    """Retorna os runs mais recentes (do mais novo para o mais antigo)."""
    items = r.zrevrange(K_HISTORY, 0, limit - 1)
    return [json.loads(i) for i in items]


# ─────────────────────────────────────────────────────────────
# CONTEXTO ACUMULADO DA IA
# ─────────────────────────────────────────────────────────────

def get_accumulated_context(r: redis.Redis) -> dict:
    raw = r.get(K_CTX_ACCUMULATED)
    if not raw:
        return {
            "summary": "",
            "known_issues": [],
            "stable_areas": [],
            "high_risk_pages": [],
            "last_updated": None,
        }
    return json.loads(raw)


def update_accumulated_context(r: redis.Redis, new_context: dict):
    new_context["last_updated"] = time.time()
    r.set(K_CTX_ACCUMULATED, json.dumps(new_context, ensure_ascii=False))


def record_failure(r: redis.Redis, page_path: str):
    r.hincrby(K_CTX_FAILURES, page_path, 1)


def record_success(r: redis.Redis, page_path: str):
    current = int(r.hget(K_CTX_FAILURES, page_path) or 0)
    if current > 0:
        r.hset(K_CTX_FAILURES, page_path, max(0, current - 1))


def get_failure_counts(r: redis.Redis) -> dict:
    return {k: int(v) for k, v in (r.hgetall(K_CTX_FAILURES) or {}).items()}


def build_context_prompt(r: redis.Redis) -> str:
    """
    Monta um bloco de contexto para incluir nos prompts da IA,
    informando o que ela ja sabe sobre a aplicacao.
    """
    ctx      = get_accumulated_context(r)
    failures = get_failure_counts(r)

    lines = ["=== CONTEXTO ACUMULADO DE EXECUCOES ANTERIORES ==="]

    if ctx.get("summary"):
        lines.append(f"Resumo: {ctx['summary']}")

    if ctx.get("known_issues"):
        lines.append("Problemas conhecidos (requerem atencao extra):")
        for issue in ctx["known_issues"][:5]:
            lines.append(f"  - {issue}")

    if ctx.get("stable_areas"):
        lines.append("Areas estaveis (historicamente sem problemas):")
        for area in ctx["stable_areas"][:3]:
            lines.append(f"  - {area}")

    high_risk = sorted(failures.items(), key=lambda x: x[1], reverse=True)[:5]
    if high_risk:
        lines.append("Paginas com mais falhas historicas (prioridade alta):")
        for path, count in high_risk:
            lines.append(f"  - {path}: {count} falha(s)")

    if ctx.get("high_risk_pages"):
        lines.append("Paginas criticas identificadas:")
        for p in ctx["high_risk_pages"][:3]:
            lines.append(f"  - {p}")

    return "\n".join(lines) if len(lines) > 1 else ""


# ─────────────────────────────────────────────────────────────
# STATUS DO RUN ATUAL (para SSE / painel)
# ─────────────────────────────────────────────────────────────

def set_run_status(r: redis.Redis, status: dict):
    r.set(K_RUN_STATUS, json.dumps(status, ensure_ascii=False))
    r.publish(K_RUN_STREAM, json.dumps(status, ensure_ascii=False))


def get_run_status(r: redis.Redis) -> dict | None:
    raw = r.get(K_RUN_STATUS)
    return json.loads(raw) if raw else None


def clear_run_status(r: redis.Redis):
    r.delete(K_RUN_STATUS)


# ─────────────────────────────────────────────────────────────
# DADOS POR USUARIO (apps, ai-config, instrucoes, historico)
# ─────────────────────────────────────────────────────────────

def _uk(email: str, suffix: str) -> str:
    """Retorna chave Redis namespaced por usuario."""
    return f"qa:u:{email}:{suffix}"


# --- Apps ---

def get_user_apps(r: redis.Redis, email: str) -> list:
    raw = r.get(_uk(email, "apps"))
    return json.loads(raw) if raw else []


def save_user_apps(r: redis.Redis, email: str, apps: list):
    r.set(_uk(email, "apps"), json.dumps(apps, ensure_ascii=False))


# --- AI Config ---

def get_user_ai_config(r: redis.Redis, email: str) -> dict:
    raw = r.get(_uk(email, "ai"))
    if not raw:
        return {"provider": "openai", "model": "gpt-4o-mini", "api_key": ""}
    return json.loads(raw)


def save_user_ai_config(r: redis.Redis, email: str, cfg: dict):
    r.set(_uk(email, "ai"), json.dumps(cfg, ensure_ascii=False))


# --- Instrucoes por usuario ---

def save_user_instructions(r: redis.Redis, email: str, general: list, pages: list, flows: list):
    pipe = r.pipeline()
    pipe.set(_uk(email, "instr:general"), json.dumps(general, ensure_ascii=False))
    pipe.set(_uk(email, "instr:pages"),   json.dumps(pages,   ensure_ascii=False))
    pipe.set(_uk(email, "instr:flows"),   json.dumps(flows,   ensure_ascii=False))
    pipe.execute()


def load_user_instructions(r: redis.Redis, email: str) -> dict:
    g = r.get(_uk(email, "instr:general"))
    p = r.get(_uk(email, "instr:pages"))
    f = r.get(_uk(email, "instr:flows"))
    return {
        "general": json.loads(g) if g else [],
        "pages":   json.loads(p) if p else [],
        "flows":   json.loads(f) if f else [],
    }


# --- Historico por usuario ---

def save_user_run(r: redis.Redis, email: str, run: dict):
    run.setdefault("id", str(int(time.time())))
    score = run.get("started_at", time.time())
    r.zadd(_uk(email, "history"), {json.dumps(run, ensure_ascii=False): score})
    r.zremrangebyrank(_uk(email, "history"), 0, -51)


def get_user_history(r: redis.Redis, email: str, limit: int = 20) -> list:
    items = r.zrevrange(_uk(email, "history"), 0, limit - 1)
    return [json.loads(i) for i in items]


# --- Contexto acumulado por usuario ---

def get_user_context(r: redis.Redis, email: str) -> dict:
    raw = r.get(_uk(email, "context"))
    if not raw:
        return {"summary": "", "known_issues": [], "stable_areas": [], "high_risk_pages": [], "last_updated": None}
    return json.loads(raw)


def update_user_context(r: redis.Redis, email: str, ctx: dict):
    ctx["last_updated"] = time.time()
    r.set(_uk(email, "context"), json.dumps(ctx, ensure_ascii=False))


def get_user_failure_counts(r: redis.Redis, email: str) -> dict:
    return {k: int(v) for k, v in (r.hgetall(_uk(email, "failures")) or {}).items()}


def record_user_failure(r: redis.Redis, email: str, page_path: str):
    r.hincrby(_uk(email, "failures"), page_path, 1)


def record_user_success(r: redis.Redis, email: str, page_path: str):
    cur = int(r.hget(_uk(email, "failures"), page_path) or 0)
    if cur > 0:
        r.hset(_uk(email, "failures"), page_path, max(0, cur - 1))


def build_user_context_prompt(r: redis.Redis, email: str) -> str:
    ctx      = get_user_context(r, email)
    failures = get_user_failure_counts(r, email)
    lines = ["=== CONTEXTO ACUMULADO DE EXECUCOES ANTERIORES ==="]
    if ctx.get("summary"):
        lines.append(f"Resumo: {ctx['summary']}")
    if ctx.get("known_issues"):
        lines.append("Problemas conhecidos:")
        for i in ctx["known_issues"][:5]:
            lines.append(f"  - {i}")
    high = sorted(failures.items(), key=lambda x: x[1], reverse=True)[:5]
    if high:
        lines.append("Paginas com mais falhas:")
        for path, cnt in high:
            lines.append(f"  - {path}: {cnt} falha(s)")
    return "\n".join(lines) if len(lines) > 1 else ""
