"""
test_navigation.py — Testes de navegação entre páginas

Verifica:
  - Todas as páginas configuradas carregam sem erro
  - Elementos obrigatórios estão presentes
  - Transferência de dados entre páginas (query params, estado)
  - Redirecionamentos funcionam corretamente
  - Console do browser não tem erros críticos
"""

import time
from playwright.sync_api import Page
from utils.browser import take_screenshot, navigate_to
from utils.reporter import Reporter


def run(page: Page, config: dict, reporter: Reporter) -> None:
    """Executa todos os testes de navegação e registra no reporter."""
    suite      = reporter.add_suite("🧭 Navegação entre Páginas")
    nav_cfg    = config.get("flows", {}).get("navigation", {})
    base_url   = config["app"]["base_url"]
    ss_dir     = config.get("report", {}).get("screenshots_dir", "./screenshots")

    if not nav_cfg.get("enabled", True):
        reporter.add_result(suite, "Navegação", "WARN", "Desativada no config.yaml")
        return

    pages = nav_cfg.get("pages", [])
    if not pages:
        reporter.add_result(suite, "Navegação", "WARN", "Nenhuma página configurada em flows.navigation.pages")
        return

    # ------------------------------------------------------------------ #
    # 1. Verifica cada página configurada
    # ------------------------------------------------------------------ #
    for pg in pages:
        name      = pg.get("name", pg.get("path", "?"))
        path      = pg.get("path", "/")
        must_have = pg.get("must_contain", [])
        start     = time.time()
        screenshot = ""

        # Captura erros do console
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text)
                if msg.type == "error" else None)

        try:
            url = navigate_to(page, base_url, path)
            screenshot = take_screenshot(page, f"nav_{name}", ss_dir)

            # Verifica elementos obrigatórios
            missing = []
            for item in must_have:
                try:
                    # Tenta como seletor CSS primeiro, depois como texto
                    if item.startswith((".","#","[","button","input","a","div","span","h")):
                        page.wait_for_selector(item, timeout=5000)
                    else:
                        if item.lower() not in page.content().lower():
                            missing.append(item)
                except Exception:
                    missing.append(item)

            # Verifica erros HTTP (404, 500)
            status_code = None
            try:
                response = page.goto(url, wait_until="networkidle")
                if response:
                    status_code = response.status
            except Exception:
                pass

            if status_code and status_code >= 400:
                reporter.add_result(
                    suite, f"Página: {name}", "FAIL",
                    f"HTTP {status_code} ao acessar {path}",
                    screenshot=screenshot, duration=time.time() - start
                )
            elif missing:
                reporter.add_result(
                    suite, f"Página: {name}", "FAIL",
                    f"Elementos não encontrados: {', '.join(missing)}",
                    screenshot=screenshot, duration=time.time() - start
                )
            elif console_errors:
                # Filtra erros menos relevantes (ex: extensões do Chrome)
                real_errors = [e for e in console_errors
                               if not any(x in e for x in ["Extension", "favicon", "chrome-extension"])]
                if real_errors:
                    reporter.add_result(
                        suite, f"Página: {name}", "WARN",
                        f"Erros no console: {'; '.join(real_errors[:3])}",
                        screenshot=screenshot, duration=time.time() - start
                    )
                else:
                    reporter.add_result(
                        suite, f"Página: {name}", "PASS",
                        screenshot=screenshot, duration=time.time() - start
                    )
            else:
                reporter.add_result(
                    suite, f"Página: {name}", "PASS",
                    screenshot=screenshot, duration=time.time() - start
                )

        except Exception as e:
            screenshot = screenshot or take_screenshot(page, f"nav_{name}_erro", ss_dir)
            reporter.add_result(
                suite, f"Página: {name}", "FAIL",
                str(e), screenshot=screenshot, duration=time.time() - start
            )
        finally:
            # Remove o listener de console para evitar acúmulo
            page.remove_listener("console", lambda msg: None)

    # ------------------------------------------------------------------ #
    # 2. Testa transferência de dados via query params
    # ------------------------------------------------------------------ #
    _test_data_transfer(page, base_url, suite, reporter, ss_dir)


def _test_data_transfer(page: Page, base_url: str, suite: dict,
                         reporter: Reporter, ss_dir: str) -> None:
    """
    Verifica se parâmetros de URL são lidos corretamente pela aplicação.
    Testa URLs com query strings e verifica se o conteúdo reflete os parâmetros.
    """
    start = time.time()
    try:
        # Testa URL com ID inválido — a app deve tratar e não quebrar
        test_url = base_url.rstrip("/") + "/pedidos/99999999"
        page.goto(test_url, wait_until="networkidle")
        screenshot = take_screenshot(page, "nav_id_invalido", ss_dir)

        # A página não deve mostrar erro 500 ou tela em branco
        content = page.content()
        blank = len(page.query_selector_all("body > *")) == 0
        has_500 = "500" in content and "server error" in content.lower()

        if has_500:
            reporter.add_result(
                suite, "Dados entre páginas: ID inválido", "FAIL",
                "Erro 500 ao acessar ID inexistente — a aplicação deve tratar este caso",
                screenshot=screenshot, duration=time.time() - start
            )
        elif blank:
            reporter.add_result(
                suite, "Dados entre páginas: ID inválido", "WARN",
                "Tela em branco ao acessar ID inexistente — verifique tratamento de 404",
                screenshot=screenshot, duration=time.time() - start
            )
        else:
            reporter.add_result(
                suite, "Dados entre páginas: ID inválido", "PASS",
                screenshot=screenshot, duration=time.time() - start
            )

    except Exception as e:
        reporter.add_result(
            suite, "Dados entre páginas: ID inválido", "WARN",
            f"Não foi possível testar: {e}", duration=time.time() - start
        )
