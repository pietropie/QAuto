"""
worker.py - Consumidor da fila Redis (multi-usuario)

Loop:
  1. BLPOP em qa:queue
  2. Le user_email do job e carrega config do Redis (apps + ai-config)
  3. Executa suites via Playwright
  4. Salva resultado no historico do usuario
  5. Publica eventos SSE
"""

import json
import os
import sys
import time
import traceback
import yaml
from pathlib import Path
from datetime import datetime

from utils.context_store import (
    get_client,
    dequeue_job, queue_length,
    set_run_status, clear_run_status, get_run_status,
    # per-user
    load_user_instructions, build_user_context_prompt,
    get_user_apps, get_user_ai_config,
    save_user_run, update_user_context, get_user_context,
    record_user_failure, record_user_success,
    get_user_failure_counts,
)


def load_config(path: str = "config.yaml") -> dict:
    """Carrega config.yaml como fallback para jobs sem user_email."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.getenv("APP_BASE_URL"):
        cfg.setdefault("app", {})["base_url"] = os.getenv("APP_BASE_URL")
    if os.getenv("APP_USERNAME"):
        cfg.setdefault("app", {}).setdefault("login", {})["username"] = os.getenv("APP_USERNAME")
    if os.getenv("APP_PASSWORD"):
        cfg["app"]["login"]["password"] = os.getenv("APP_PASSWORD")
    if os.getenv("OPENAI_API_KEY"):
        cfg.setdefault("ai", {})["api_key"] = os.getenv("OPENAI_API_KEY")
    return cfg


def _build_config_from_redis(app: dict, ai_cfg: dict, base_cfg: dict) -> dict:
    """Monta o dict de config a partir dos dados do Redis (por usuario)."""
    cfg = {
        "app": {
            "base_url": app.get("url", ""),
            "login": {
                "enabled":            app.get("login_enabled", False),
                "url_path":           "/login",
                "username_selector":  "input[name='email']",
                "password_selector":  "input[name='password']",
                "submit_selector":    "button[type='submit']",
                "success_indicator":  ".dashboard",
                "username":           app.get("username", ""),
                "password":           app.get("password", ""),
            },
        },
        "ai": {
            "enabled":  True,
            "provider": ai_cfg.get("provider", "openai"),
            "model":    ai_cfg.get("model", "gpt-4o-mini"),
            "api_key":  ai_cfg.get("api_key", ""),
            "visual_checks": base_cfg.get("ai", {}).get("visual_checks", [
                "elementos sobrepostos ou cortados",
                "textos ilegíveis ou truncados",
                "botões sem texto ou ícones quebrados",
                "imagens não carregadas",
                "layout desalinhado ou quebrado",
            ]),
        },
        "browser": base_cfg.get("browser", {"headless": True, "slow_mo": 0, "timeout": 30000}),
        "report":  base_cfg.get("report",  {"output_dir": "./reports", "screenshots_dir": "./screenshots"}),
        "flows":   base_cfg.get("flows",   {}),
    }
    return cfg


class WorkerReporter:
    def __init__(self, run_id: str, r, config: dict, user_email: str = ""):
        self.run_id     = run_id
        self.r          = r
        self.config     = config
        self.user_email = user_email
        self.suites     = []
        self.started    = time.time()

    def add_suite(self, name: str) -> dict:
        suite = {"name": name, "tests": []}
        self.suites.append(suite)
        return suite

    def add_result(self, suite: dict, name: str, status: str,
                   message: str = "", screenshot: str = "",
                   ai_analysis: str = "", duration: float = 0.0):
        suite["tests"].append({
            "name": name, "status": status, "message": message,
            "screenshot": screenshot, "ai_analysis": ai_analysis,
            "duration": round(duration, 2),
        })
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "WARN": "[WARN]"}.get(status, "[?]")
        print(f"    {icon} {name}" + (f"\n       {message}" if message and status != "PASS" else ""))

        if screenshot and self.user_email:
            path_guess = _guess_path(name)
            if status == "FAIL":
                record_user_failure(self.r, self.user_email, path_guess)
            elif status == "PASS":
                record_user_success(self.r, self.user_email, path_guess)

        all_tests = [t for s in self.suites for t in s["tests"]]
        set_run_status(self.r, {
            "type": "progress", "run_id": self.run_id, "suite": suite["name"],
            "test": name, "status": status,
            "passed":  sum(1 for t in all_tests if t["status"] == "PASS"),
            "failed":  sum(1 for t in all_tests if t["status"] == "FAIL"),
            "warned":  sum(1 for t in all_tests if t["status"] == "WARN"),
            "total_so_far": len(all_tests),
        })

    def finish(self) -> dict:
        all_tests = [t for s in self.suites for t in s["tests"]]
        run = {
            "id": self.run_id, "started_at": self.started,
            "finished_at": time.time(),
            "duration": round(time.time() - self.started, 1),
            "total": len(all_tests),
            "passed": sum(1 for t in all_tests if t["status"] == "PASS"),
            "failed": sum(1 for t in all_tests if t["status"] == "FAIL"),
            "warned": sum(1 for t in all_tests if t["status"] == "WARN"),
            "suites": self.suites,
            "label":  self.config.get("_job_label", "Run"),
            "app_name": self.config.get("_app_name", ""),
        }
        if self.user_email:
            save_user_run(self.r, self.user_email, run)
        set_run_status(self.r, {"type": "finished", "run_id": self.run_id, **run})
        return run


def _guess_path(test_name: str) -> str:
    import re
    m = re.search(r'(/[\w\-/]+)', test_name)
    return m.group(1) if m else "/unknown"


def _update_ai_context(r, reporter: WorkerReporter, config: dict):
    ai_cfg  = config.get("ai", {})
    api_key = ai_cfg.get("api_key", "")
    if not api_key or api_key.startswith("TODO"):
        return

    all_tests = [t for s in reporter.suites for t in s["tests"]]
    failures  = [t for t in all_tests if t["status"] == "FAIL"]
    warns     = [t for t in all_tests if t["status"] == "WARN"]
    if not (failures or warns):
        return

    email    = reporter.user_email
    prev_ctx = get_user_context(r, email) if email else {}
    failures_text = "\n".join(f"- {t['name']}: {t['message'][:200]}" for t in failures[:10])
    warns_text    = "\n".join(f"- {t['name']}: {t['message'][:200]}" for t in warns[:5])

    prompt = (
        f"Voce e um sistema de QA. Analise os resultados e atualize o contexto acumulado.\n\n"
        f"CONTEXTO ATUAL:\n{json.dumps(prev_ctx, ensure_ascii=False)}\n\n"
        f"FALHAS NESTE RUN:\n{failures_text or 'nenhuma'}\n\n"
        f"AVISOS:\n{warns_text or 'nenhum'}\n\n"
        f"Responda em JSON puro:\n"
        f'{{"summary":"resumo geral","known_issues":["..."],"stable_areas":["..."],"high_risk_pages":["..."]}}'
    )

    try:
        from openai import OpenAI
        base_url = None
        if ai_cfg.get("provider") == "deepseek":
            base_url = "https://api.deepseek.com/v1"
        client = OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))
        msg = client.chat.completions.create(
            model=ai_cfg.get("model", "gpt-4o-mini"),
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.choices[0].message.content.strip()
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m and email:
            update_user_context(r, email, json.loads(m.group()))
            print(f"  [IA] Contexto atualizado.")
    except Exception as e:
        print(f"  [WARN] Nao foi possivel atualizar contexto: {e}")


def run_job(job: dict, r):
    from utils.browser import create_browser, login
    from tests import test_navigation, test_forms, test_orders, test_visual, test_custom

    run_id     = f"run_{int(time.time())}"
    user_email = job.get("user_email", "")

    # --- Monta config ---
    base_cfg = load_config()
    if user_email:
        apps   = get_user_apps(r, user_email)
        app_id = job.get("app_id", "")
        app    = next((a for a in apps if a["id"] == app_id), None)
        if not app and apps:
            app = apps[0]  # usa o primeiro se nao encontrou pelo id
        if not app:
            app = {"url": base_cfg.get("app", {}).get("base_url", ""), "login_enabled": False}
        ai_cfg = get_user_ai_config(r, user_email)
        config = _build_config_from_redis(app, ai_cfg, base_cfg)
        config["_app_name"] = app.get("name", "")

        # Instrucoes do painel
        redis_instr = load_user_instructions(r, user_email)
        if any(redis_instr.values()):
            config["_redis_instructions"] = redis_instr

        # Contexto acumulado
        ctx_prompt = build_user_context_prompt(r, user_email)
        if ctx_prompt:
            config.setdefault("ai", {})["accumulated_context"] = ctx_prompt
    else:
        config = base_cfg

    config["_job_label"] = job.get("label", "Run")

    # Overrides pontuais
    for k, v in job.get("config_override", {}).items():
        keys = k.split(".")
        d = config
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = v

    reporter = WorkerReporter(run_id, r, config, user_email)

    set_run_status(r, {
        "type": "started", "run_id": run_id,
        "label": job.get("label", "Run"),
        "job_type": job.get("type", "full"),
        "app_name": config.get("_app_name", ""),
        "started_at": time.time(),
    })

    job_type  = job.get("type", "full")
    suite_map = {
        "navegacao":   ("Navegacao",   lambda p, ctx: test_navigation.run(p, config, reporter)),
        "formularios": ("Formularios", lambda p, ctx: test_forms.run(p, config, reporter)),
        "pedidos":     ("Pedidos",     lambda p, ctx: test_orders.run(p, config, reporter)),
        "visual":      ("Visual IA",   lambda p, ctx: test_visual.run(p, ctx, config, reporter)),
        "custom":      ("Instrucoes",  lambda p, ctx: test_custom.run(p, config, reporter)),
    }
    suites_to_run = list(suite_map.keys()) if job_type == "full" else [job_type]

    print(f"\n{'='*50}")
    print(f"  Job: {job.get('label')} | App: {config.get('_app_name', 'N/A')} | User: {user_email or 'legacy'}")
    print(f"  Suites: {suites_to_run} | Run ID: {run_id}")
    print(f"{'='*50}")

    pw, browser, context = create_browser(config)
    page = context.new_page()
    try:
        login(page, config)
        for key in suites_to_run:
            if key not in suite_map:
                continue
            name, fn = suite_map[key]
            print(f"\n  >> Suite: {name}")
            try:
                fn(page, context)
            except Exception as e:
                print(f"  ERRO critico na suite {name}: {e}")
                traceback.print_exc()
    finally:
        context.close()
        browser.close()
        pw.stop()

    run_result = reporter.finish()
    _update_ai_context(r, reporter, config)
    clear_run_status(r)

    print(f"\n  Finalizado: {run_result['passed']} OK / {run_result['failed']} falhas / {run_result['warned']} avisos")
    return run_result


def main():
    print("QA Worker iniciado. Aguardando jobs na fila...")
    print(f"Redis: {os.getenv('REDIS_URL', 'redis://localhost:6379/0')}")
    r = get_client()
    try:
        r.ping()
        print("Redis: conectado OK")
    except Exception as e:
        print(f"ERRO: nao foi possivel conectar ao Redis: {e}")
        sys.exit(1)

    backoff = 1
    while True:
        try:
            pending = queue_length(r)
            if pending:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] {pending} job(s) na fila")
            job = dequeue_job(r, timeout=30)
            if job is None:
                backoff = 1
                continue
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Processando: {job.get('label', job.get('type'))}")
            run_job(job, r)
            backoff = 1
        except KeyboardInterrupt:
            print("\nWorker encerrado pelo usuario.")
            break
        except Exception as e:
            print(f"\nERRO no worker: {e}")
            traceback.print_exc()
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
