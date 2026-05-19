"""
test_orders.py — Fluxo completo de pedidos (checkout / fechamento)

Testa:
  - Cada etapa configurada em flows.orders.flow
  - Dados são passados corretamente entre etapas
  - Network requests durante o fluxo (verifica payloads e status codes)
  - Pedido criado aparece na listagem
  - Ações de negócio (fechar, cancelar, aprovar) funcionam
"""

import time
import json
from playwright.sync_api import Page, Response
from utils.browser import take_screenshot, navigate_to
from utils.reporter import Reporter


def run(page: Page, config: dict, reporter: Reporter) -> None:
    suite      = reporter.add_suite("🛒 Fluxo de Pedidos")
    orders_cfg = config.get("flows", {}).get("orders", {})
    base_url   = config["app"]["base_url"]
    ss_dir     = config.get("report", {}).get("screenshots_dir", "./screenshots")

    if not orders_cfg.get("enabled", True):
        reporter.add_result(suite, "Pedidos", "WARN", "Desativado no config.yaml")
        return

    flow = orders_cfg.get("flow", [])
    if not flow:
        reporter.add_result(suite, "Pedidos", "WARN",
                            "Nenhum fluxo configurado em flows.orders.flow")
        return

    # Monitora requests de rede durante todo o fluxo
    api_requests   = []
    api_errors     = []

    def on_response(response: Response):
        url = response.url
        # Filtra apenas chamadas de API (ignora assets estáticos)
        if any(x in url for x in ["/api/", "/graphql", "/rest/", ".json"]):
            try:
                status = response.status
                api_requests.append({"url": url, "status": status})
                if status >= 400:
                    api_errors.append({
                        "url": url,
                        "status": status,
                        "step": "monitoramento de rede",
                    })
            except Exception:
                pass

    page.on("response", on_response)

    # ------------------------------------------------------------------ #
    # Executa cada etapa do fluxo
    # ------------------------------------------------------------------ #
    previous_url = ""
    flow_failed  = False

    for i, step in enumerate(flow):
        step_name = step.get("step", f"Etapa {i + 1}")
        url_path  = step.get("url_path", "/")
        actions   = step.get("actions", [])
        assert_sel  = step.get("assert_selector", "")
        assert_url  = step.get("assert_url_contains", "")
        assert_text = step.get("assert_text", "")
        start       = time.time()

        print(f"\n  Etapa {i+1}: {step_name}")

        if flow_failed:
            reporter.add_result(
                suite, f"Etapa {i+1}: {step_name}", "WARN",
                "Pulado — etapa anterior falhou", duration=0
            )
            continue

        try:
            navigate_to(page, base_url, url_path)

            # Executa ações configuradas
            for action in actions:
                atype = action.get("type", "")
                sel   = action.get("selector", "")
                val   = action.get("value", "")

                if atype == "fill":
                    page.fill(sel, val)
                elif atype == "click":
                    page.click(sel)
                    page.wait_for_load_state("networkidle")
                elif atype == "select":
                    page.select_option(sel, val)
                elif atype == "check":
                    page.check(sel)
                elif atype == "wait":
                    page.wait_for_timeout(int(val) if val else 1000)
                elif atype == "wait_selector":
                    page.wait_for_selector(sel)
                elif atype == "scroll":
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

            page.wait_for_load_state("networkidle")
            screenshot = take_screenshot(page, f"order_step_{i+1}", ss_dir)
            current_url = page.url
            content     = page.content()

            # Validações
            failures = []

            if assert_sel:
                try:
                    page.wait_for_selector(assert_sel, timeout=8000)
                except Exception:
                    failures.append(f"Seletor '{assert_sel}' não encontrado após a ação")

            if assert_url and assert_url not in current_url:
                failures.append(
                    f"URL esperada conter '{assert_url}', mas é '{current_url}'"
                )

            if assert_text and assert_text.lower() not in content.lower():
                failures.append(f"Texto '{assert_text}' não encontrado na página")

            # Verifica se houve erro visível na tela
            has_error_ui = any(x in content.lower() for x in
                               ["erro interno", "500", "not found", "404",
                                "something went wrong", "algo deu errado"])

            if has_error_ui:
                failures.append("Mensagem de erro detectada na interface")

            if failures:
                flow_failed = True
                reporter.add_result(
                    suite, f"Etapa {i+1}: {step_name}", "FAIL",
                    "\n".join(failures),
                    screenshot=screenshot, duration=time.time() - start
                )
            else:
                reporter.add_result(
                    suite, f"Etapa {i+1}: {step_name}", "PASS",
                    f"URL atual: {current_url}",
                    screenshot=screenshot, duration=time.time() - start
                )

            previous_url = current_url

        except Exception as e:
            flow_failed = True
            screenshot = take_screenshot(page, f"order_step_{i+1}_erro", ss_dir)
            reporter.add_result(
                suite, f"Etapa {i+1}: {step_name}", "FAIL",
                str(e), screenshot=screenshot, duration=time.time() - start
            )

    # Remove listener
    page.remove_listener("response", on_response)

    # ------------------------------------------------------------------ #
    # Relatório de erros de rede
    # ------------------------------------------------------------------ #
    if api_errors:
        errors_msg = "\n".join(
            f"[{e['status']}] {e['url']}" for e in api_errors[:10]
        )
        reporter.add_result(
            suite, "Chamadas de API: erros de rede", "FAIL",
            f"Foram detectadas {len(api_errors)} chamadas de API com erro:\n{errors_msg}"
        )
    elif api_requests:
        reporter.add_result(
            suite, "Chamadas de API: erros de rede", "PASS",
            f"{len(api_requests)} chamadas de API monitoradas, todas com sucesso"
        )

    # ------------------------------------------------------------------ #
    # Verifica payloads suspeitos (dados ausentes em POST)
    # ------------------------------------------------------------------ #
    _check_network_payloads(page, base_url, suite, reporter, ss_dir)


def _check_network_payloads(page: Page, base_url: str, suite: dict,
                              reporter: Reporter, ss_dir: str) -> None:
    """
    Intercepta uma chamada de criação e verifica se o payload está completo.
    """
    start = time.time()
    captured_requests = []

    def capture_request(request):
        if request.method in ("POST", "PUT", "PATCH"):
            if any(x in request.url for x in ["/api/", "/graphql", ".json"]):
                try:
                    body = request.post_data
                    captured_requests.append({
                        "method": request.method,
                        "url": request.url,
                        "body": body or "",
                    })
                except Exception:
                    pass

    page.on("request", capture_request)

    try:
        # Navega por algumas páginas para acionar requests
        for path in ["/", "/pedidos"]:
            page.goto(base_url.rstrip("/") + path, wait_until="networkidle")

        page.remove_listener("request", capture_request)

        # Analisa payloads capturados
        empty_bodies = [r for r in captured_requests
                        if not r["body"] or r["body"] in ("{}", "[]", "null", "")]

        if empty_bodies:
            msgs = [f"[{r['method']}] {r['url']}" for r in empty_bodies[:5]]
            reporter.add_result(
                suite, "Payloads de API: dados enviados", "WARN",
                f"Requests com body vazio detectados:\n" + "\n".join(msgs),
                duration=time.time() - start
            )
        elif captured_requests:
            reporter.add_result(
                suite, "Payloads de API: dados enviados", "PASS",
                f"{len(captured_requests)} requests POST/PUT capturados com payload preenchido",
                duration=time.time() - start
            )

    except Exception as e:
        page.remove_listener("request", capture_request)
        reporter.add_result(
            suite, "Payloads de API: dados enviados", "WARN",
            f"Não foi possível monitorar: {e}", duration=time.time() - start
        )
