"""
worker.py - Consumidor da fila Redis

Loop:
  1. BLPOP em qa:queue
  2. Carrega config.yaml + instrucoes do Redis
  3. Injeta contexto acumulado nos prompts da IA
  4. Executa as suites solicitadas via Playwright
  5. Salva resultado no historico
  6. Atualiza contexto acumulado com o que a IA aprendeu
  7. Publica eventos SSE para o painel

Rodar: python worker.py
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
    dequeue_job,
    load_instructions,
    save_run_result,
    update_accumulated_context,
    get_accumulated_context,
    record_failure,
    record_success,
    build_context_prompt,
    set_run_status,
    clear_run_status,
    queue_length,
    get_user_apps,
    get_user_ai_config,
    load_user_instructions,
    save_user_run_result,
)


def load_config(path: str = "config.yaml") -> dict:
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"  [WARN] {path} nao encontrado — usando config vazia")
    except Exception as e:
        print(f"  [WARN] Erro ao ler {path}: {e} — usando config vazia")
    # Permite override de variaveis de ambiente
    if os.getenv("APP_BASE_URL"):
        cfg.setdefault("app", {})["base_url"] = os.getenv("APP_BASE_URL")
    if os.getenv("APP_USERNAME"):
        cfg.setdefault("app", {}).setdefault("login", {})["username"] = os.getenv("APP_USERNAME")
    if os.getenv("APP_PASSWORD"):
        cfg.setdefault("app", {}).setdefault("login", {})["password"] = os.getenv("APP_PASSWORD")
    if os.getenv("OPENAI_API_KEY"):
        cfg.setdefault("ai", {})["api_key"] = os.getenv("OPENAI_API_KEY")
    return cfg


class WorkerReporter:
    """
    Reporter leve que coleta resultados em memoria e depois
    salva no Redis (sem gerar arquivo HTML — o historico fica no Redis).
    """
    def __init__(self, run_id: str, r, config: dict):
        self.run_id    = run_id
        self.r         = r
        self.config    = config
        self.suites    = []
        self.started   = time.time()

    def add_suite(self, name: str) -> dict:
        suite = {"name": name, "tests": []}
        self.suites.append(suite)
        return suite

    def add_result(self, suite: dict, name: str, status: str,
                   message: str = "", screenshot: str = "",
                   ai_analysis: str = "", duration: float = 0.0):
        suite["tests"].append({
            "name":        name,
            "status":      status,
            "message":     message,
            "screenshot":  screenshot,
            "ai_analysis": ai_analysis,
            "duration":    round(duration, 2),
        })
        icon = {"PASS": "[OK]", "FAIL": "[FAIL]", "WARN": "[WARN]"}.get(status, "[?]")
        print(f"    {icon} {name}" + (f"\n       {message}" if message and status != "PASS" else ""))

        # Atualiza contadores de falha por pagina no Redis
        if screenshot:
            path_guess = _guess_path(name)
            if status == "FAIL":
                record_failure(self.r, path_guess)
            elif status == "PASS":
                record_success(self.r, path_guess)

        # Publica update em tempo real via SSE
        all_tests = [t for s in self.suites for t in s["tests"]]
        set_run_status(self.r, {
            "type":        "progress",
            "run_id":      self.run_id,
            "suite":       suite["name"],
            "test":        name,
            "status":      status,
            "passed":      sum(1 for t in all_tests if t["status"] == "PASS"),
            "failed":      sum(1 for t in all_tests if t["status"] == "FAIL"),
            "warned":      sum(1 for t in all_tests if t["status"] == "WARN"),
            "total_so_far": len(all_tests),
        })

    def finish(self) -> dict:
        all_tests = [t for s in self.suites for t in s["tests"]]
        run = {
            "id":          self.run_id,
            "started_at":  self.started,
            "finished_at": time.time(),
            "duration":    round(time.time() - self.started, 1),
            "total":       len(all_tests),
            "passed":      sum(1 for t in all_tests if t["status"] == "PASS"),
            "failed":      sum(1 for t in all_tests if t["status"] == "FAIL"),
            "warned":      sum(1 for t in all_tests if t["status"] == "WARN"),
            "suites":      self.suites,
            "label":       self.config.get("_job_label", "Run"),
        }
        save_run_result(self.r, run)
        set_run_status(self.r, {"type": "finished", "run_id": self.run_id, **run})
        return run


def _guess_path(test_name: str) -> str:
    import re
    m = re.search(r'(/[\w\-/]+)', test_name)
    return m.group(1) if m else "/unknown"


def _update_ai_context(r, reporter: WorkerReporter, config: dict):
    """
    Chama a IA para sumarizar os resultados e atualizar o contexto acumulado.
    Isso permite que runs futuros saibam o que ja foi encontrado antes.
    """
    ai_cfg  = config.get("ai", {})
    api_key = ai_cfg.get("api_key", os.getenv("OPENAI_API_KEY", ""))
    if not api_key or api_key.startswith("TODO"):
        return

    all_tests = [t for s in reporter.suites for t in s["tests"]]
    failures  = [t for t in all_tests if t["status"] == "FAIL"]
    warns     = [t for t in all_tests if t["status"] == "WARN"]

    if not (failures or warns):
        return  # Nada a aprender de um run perfeito

    prev_ctx = get_accumulated_context(r)

    failures_text = "\n".join(f"- {t['name']}: {t['message'][:200]}" for t in failures[:10])
    warns_text    = "\n".join(f"- {t['name']}: {t['message'][:200]}" for t in warns[:5])

    prompt = (
        f"Voce e um sistema de QA. Analise os resultados abaixo e atualize o contexto acumulado.\n\n"
        f"CONTEXTO ATUAL:\n{json.dumps(prev_ctx, ensure_ascii=False)}\n\n"
        f"FALHAS NESTE RUN:\n{failures_text or 'nenhuma'}\n\n"
        f"AVISOS:\n{warns_text or 'nenhum'}\n\n"
        f"Responda em JSON puro:\n"
        f'{{"summary":"resumo geral do estado da app",'
        f'"known_issues":["lista de problemas conhecidos"],'
        f'"stable_areas":["areas sem problemas historicos"],'
        f'"high_risk_pages":["paginas mais problematicas"]}}'
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        msg = client.chat.completions.create(
            model=ai_cfg.get("model", "gpt-4o-mini"),
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.choices[0].message.content.strip()
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            new_ctx = json.loads(m.group())
            update_accumulated_context(r, new_ctx)
            print(f"  [IA] Contexto atualizado: {new_ctx.get('summary', '')[:100]}")
    except Exception as e:
        print(f"  [WARN] Nao foi possivel atualizar contexto: {e}")


def run_job(job: dict, r):
    """Executa um job da fila."""
    run_id = f"run_{int(time.time())}"
    user_email = job.get("user_email")
    reporter = None

    def _save_critical_error(err_msg: str):
        try:
            r.set("qa:worker:last_error", json.dumps({
                "error": err_msg, "run_id": run_id, "ts": time.time(), "job": str(job)[:200]
            }))
        except Exception:
            pass

    try:
        from utils.browser import create_browser, login
        from tests import test_navigation, test_forms, test_orders, test_visual, test_custom
    except Exception as e:
        _save_critical_error(f"ImportError: {e}")
        raise

    config = load_config()
    config["_job_label"] = job.get("label", "Run")

    # Carrega app do Redis se app_id estiver definido no job
    app_id = job.get("app_id")
    if app_id and user_email:
        user_apps = get_user_apps(r, user_email)
        app = next((a for a in user_apps if a.get("id") == app_id), None)
        if app:
            config.setdefault("app", {})["base_url"] = app.get("base_url") or app.get("url", "")
            login_cfg = config["app"].setdefault("login", {})
            login_cfg["enabled"]           = app.get("login_enabled", False)
            login_cfg["url_path"]          = app.get("login_url", "/login")
            login_cfg["username"]          = app.get("username", "")
            login_cfg["password"]          = app.get("password", "")
            login_cfg["username_selector"] = app.get("username_selector", "input[name='email']")
            login_cfg["password_selector"] = app.get("password_selector", "input[name='password']")
            login_cfg["submit_selector"]   = app.get("submit_selector", "button[type='submit']")
            login_cfg["success_indicator"] = app.get("success_indicator", "")
            print(f"  [INFO] App carregado do Redis: {app.get('name')} -> {app.get('base_url')}")
        else:
            print(f"  [WARN] app_id {app_id} nao encontrado para {user_email}")

    # Carrega config de IA do usuario no Redis
    if user_email:
        ai_cfg = get_user_ai_config(r, user_email)
        if ai_cfg.get("api_key") and not ai_cfg["api_key"].startswith("TODO"):
            config.setdefault("ai", {}).update(ai_cfg)
            print(f"  [INFO] Config IA carregada do Redis: provider={ai_cfg.get('provider')} model={ai_cfg.get('model')}")

    # Aplica overrides do job
    for k, v in job.get("config_override", {}).items():
        keys = k.split(".")
        d = config
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = v

    # Injeta contexto acumulado do Redis nos prompts
    ctx_prompt = build_context_prompt(r)
    if ctx_prompt:
        config.setdefault("ai", {})["accumulated_context"] = ctx_prompt

    # Injeta instrucoes do painel (por usuario se disponivel, global como fallback)
    if user_email:
        redis_instructions = load_user_instructions(r, user_email)
        if not any(redis_instructions.values()):
            redis_instructions = load_instructions(r)
    else:
        redis_instructions = load_instructions(r)

    # Aplica filtro de produto nas paginas, se solicitado
    product_filter = job.get("product_filter", "all")
    if product_filter and product_filter != "all":
        paginas = redis_instructions.get("pages", [])
        redis_instructions["pages"] = [
            pg for pg in paginas
            if pg.get("produto", "").strip().lower() == product_filter.strip().lower()
        ]
        print(f"  [INFO] Filtro de produto '{product_filter}': "
              f"{len(redis_instructions['pages'])} pagina(s) selecionada(s)")

    if any(redis_instructions.values()):
        config["_redis_instructions"] = redis_instructions

    reporter = WorkerReporter(run_id, r, config)

    try:
        set_run_status(r, {
            "type":      "started",
            "run_id":    run_id,
            "label":     job.get("label", "Run"),
            "job_type":  job.get("type", "full"),
            "started_at": time.time(),
        })

        job_type = job.get("type", "full")
        suite_map = {
            "navegacao":   ("Navegacao",   lambda p, ctx: test_navigation.run(p, config, reporter)),
            "formularios": ("Formularios", lambda p, ctx: test_forms.run(p, config, reporter)),
            "pedidos":     ("Pedidos",     lambda p, ctx: test_orders.run(p, config, reporter)),
            "visual":      ("Visual IA",   lambda p, ctx: test_visual.run(p, ctx, config, reporter)),
            "custom":      ("Instrucoes",  lambda p, ctx: test_custom.run(p, config, reporter)),
        }
        suites_to_run = list(suite_map.keys()) if job_type == "full" else [job_type]

        print(f"\n{'='*50}")
        print(f"  Job: {job.get('label')} | Suites: {suites_to_run}")
        print(f"  Run ID: {run_id}")
        print(f"{'='*50}")

        try:
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
        except Exception as e:
            print(f"  [ERRO] Falha ao iniciar browser ou executar testes: {e}")
            traceback.print_exc()
            suite = reporter.add_suite("Erro de Execucao")
            reporter.add_result(suite, "Inicializar browser", "FAIL", str(e))
            _save_critical_error(f"BrowserError: {e}")

    except Exception as e:
        err_msg = f"Erro na execucao do job: {type(e).__name__}: {e}"
        print(f"  [ERRO CRITICO] {err_msg}")
        traceback.print_exc()
        _save_critical_error(err_msg)
    finally:
        if reporter is not None:
            run_result = reporter.finish()
            if user_email:
                try:
                    save_user_run_result(r, user_email, run_result)
                except Exception as ex:
                    print(f"  [WARN] Nao foi possivel salvar historico do usuario: {ex}")
            _update_ai_context(r, reporter, config)
            print(f"\n  Finalizado: {run_result['passed']} OK / {run_result['failed']} falhas / {run_result['warned']} avisos")
        clear_run_status(r)



def main():
    print("QA Worker iniciado. Aguardando jobs na fila...")
    print(f"Redis: {os.getenv('REDIS_URL', 'redis://localhost:6379/0')}")

    r = get_client()

    # Verifica conexao
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
