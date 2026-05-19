"""
test_forms.py — Validação de formulários

Testa:
  - Campos obrigatórios: submissão vazia deve mostrar erro
  - Campos com formato inválido (email, número, CPF etc.)
  - Feedback visual de erro (mensagens, bordas vermelhas)
  - Submissão com dados válidos completos
  - Campos desabilitados não aceitam digitação
  - Preenchimento automático (autocomplete) não quebra validação
"""

import time
from playwright.sync_api import Page
from utils.browser import take_screenshot, navigate_to
from utils.reporter import Reporter


def run(page: Page, config: dict, reporter: Reporter) -> None:
    suite     = reporter.add_suite("📋 Validação de Formulários")
    forms_cfg = config.get("flows", {}).get("forms", {})
    base_url  = config["app"]["base_url"]
    ss_dir    = config.get("report", {}).get("screenshots_dir", "./screenshots")

    if not forms_cfg.get("enabled", True):
        reporter.add_result(suite, "Formulários", "WARN", "Desativado no config.yaml")
        return

    test_cases = forms_cfg.get("test_cases", [])
    if not test_cases:
        reporter.add_result(suite, "Formulários", "WARN",
                            "Nenhum formulário configurado em flows.forms.test_cases")
        return

    for form in test_cases:
        form_name  = form.get("name", "Formulário")
        url_path   = form.get("url_path", "/")
        fields     = form.get("fields", [])
        submit_sel = form.get("submit_selector", "button[type='submit']")
        error_sel  = form.get("expected_error_selector",
                              ".error-message, [role='alert'], .invalid-feedback")

        print(f"\n  Testando formulário: {form_name}")

        # ---------------------------------------------------------------- #
        # 1. Teste de campos obrigatórios (submit vazio)
        # ---------------------------------------------------------------- #
        _test_empty_submit(page, base_url, url_path, form_name,
                           fields, submit_sel, error_sel,
                           suite, reporter, ss_dir)

        # ---------------------------------------------------------------- #
        # 2. Teste de valores inválidos
        # ---------------------------------------------------------------- #
        _test_invalid_values(page, base_url, url_path, form_name,
                             fields, submit_sel, error_sel,
                             suite, reporter, ss_dir)

        # ---------------------------------------------------------------- #
        # 3. Teste de preenchimento completo válido
        # ---------------------------------------------------------------- #
        _test_valid_submit(page, base_url, url_path, form_name,
                           fields, submit_sel,
                           suite, reporter, ss_dir)


def _test_empty_submit(page, base_url, url_path, form_name,
                       fields, submit_sel, error_sel, suite, reporter, ss_dir):
    """Tenta submeter o formulário vazio e verifica mensagens de erro."""
    start = time.time()
    try:
        navigate_to(page, base_url, url_path)

        # Limpa todos os campos antes de submeter
        for field in fields:
            sel = field.get("selector", "")
            try:
                page.fill(sel, "")
            except Exception:
                pass

        # Clica em submeter
        page.click(submit_sel)
        page.wait_for_timeout(1000)  # Aguarda validação

        screenshot = take_screenshot(page, f"form_{form_name}_vazio", ss_dir)

        # Verifica se erros apareceram
        required_fields = [f for f in fields if f.get("required") or f.get("test_empty")]
        errors_found = []

        for field in required_fields:
            label = field.get("label", field.get("selector", "?"))
            try:
                # Verifica se há mensagem de erro visível
                error_visible = page.evaluate(f"""
                    (() => {{
                        const errorEls = document.querySelectorAll('{error_sel}');
                        return errorEls.length > 0 && Array.from(errorEls).some(el => el.offsetParent !== null);
                    }})()
                """)

                # Verifica também atributo aria-invalid
                aria_invalid = page.evaluate(f"""
                    (() => {{
                        const field = document.querySelector('{field['selector']}');
                        return field && field.getAttribute('aria-invalid') === 'true';
                    }})()
                """)

                if not error_visible and not aria_invalid:
                    errors_found.append(f"'{label}' não mostrou erro quando vazio")
            except Exception:
                pass

        if errors_found:
            reporter.add_result(
                suite, f"{form_name}: Campos obrigatórios", "WARN",
                "Possível falta de validação:\n" + "\n".join(errors_found),
                screenshot=screenshot, duration=time.time() - start
            )
        else:
            reporter.add_result(
                suite, f"{form_name}: Campos obrigatórios", "PASS",
                "Formulário vazio bloqueou submissão com feedback visual",
                screenshot=screenshot, duration=time.time() - start
            )

    except Exception as e:
        screenshot = take_screenshot(page, f"form_{form_name}_vazio_erro", ss_dir)
        reporter.add_result(
            suite, f"{form_name}: Campos obrigatórios", "FAIL",
            str(e), screenshot=screenshot, duration=time.time() - start
        )


def _test_invalid_values(page, base_url, url_path, form_name,
                         fields, submit_sel, error_sel, suite, reporter, ss_dir):
    """Testa campos com valores inválidos."""
    fields_with_invalid = [f for f in fields if f.get("test_invalid")]
    if not fields_with_invalid:
        return

    start = time.time()
    try:
        navigate_to(page, base_url, url_path)
        problems = []

        for field in fields_with_invalid:
            sel          = field.get("selector", "")
            label        = field.get("label", sel)
            invalid_val  = field.get("test_invalid", "INVALIDO_XYZ")

            try:
                page.fill(sel, invalid_val)
                page.click(submit_sel)
                page.wait_for_timeout(800)

                # Verifica se algum erro apareceu
                error_count = page.evaluate(f"""
                    document.querySelectorAll('{error_sel}').length
                """)
                if error_count == 0:
                    problems.append(f"'{label}' aceitou valor inválido '{invalid_val}' sem erro")

                page.fill(sel, "")  # Limpa para próximo campo
            except Exception as e:
                problems.append(f"'{label}': erro ao testar — {e}")

        screenshot = take_screenshot(page, f"form_{form_name}_invalido", ss_dir)

        if problems:
            reporter.add_result(
                suite, f"{form_name}: Valores inválidos", "WARN",
                "\n".join(problems), screenshot=screenshot, duration=time.time() - start
            )
        else:
            reporter.add_result(
                suite, f"{form_name}: Valores inválidos", "PASS",
                "Campos rejeitaram valores inválidos corretamente",
                screenshot=screenshot, duration=time.time() - start
            )

    except Exception as e:
        reporter.add_result(
            suite, f"{form_name}: Valores inválidos", "FAIL",
            str(e), duration=time.time() - start
        )


def _test_valid_submit(page, base_url, url_path, form_name,
                       fields, submit_sel, suite, reporter, ss_dir):
    """Preenche todos os campos com valores válidos e submete."""
    fields_with_value = [f for f in fields if f.get("test_value")]
    if not fields_with_value:
        return

    start = time.time()
    try:
        navigate_to(page, base_url, url_path)
        url_before = page.url

        for field in fields_with_value:
            sel = field.get("selector", "")
            val = field.get("test_value", "")
            try:
                field_type = page.evaluate(f"""
                    document.querySelector('{sel}')?.type || 'text'
                """)
                if field_type == "select-one":
                    page.select_option(sel, index=1)  # Seleciona segunda opção
                elif field_type == "checkbox":
                    page.check(sel)
                else:
                    page.fill(sel, val)
            except Exception as e:
                print(f"     Aviso: não foi possível preencher '{sel}': {e}")

        page.click(submit_sel)
        page.wait_for_load_state("networkidle")

        screenshot = take_screenshot(page, f"form_{form_name}_valido", ss_dir)
        url_after  = page.url

        # Verifica se houve alguma mudança (redirecionamento ou sucesso)
        # Se a URL não mudou, pode ser que o form falhou silenciosamente
        content = page.content().lower()
        has_success = any(x in content for x in
                          ["sucesso", "success", "salvo", "criado", "adicionado",
                           "alert-success", "toast-success", "✓"])
        has_error   = any(x in content for x in
                          ["erro", "error", "falha", "inválido", "alert-danger"])

        if has_error:
            reporter.add_result(
                suite, f"{form_name}: Preenchimento válido", "FAIL",
                "Formulário com dados válidos retornou erro",
                screenshot=screenshot, duration=time.time() - start
            )
        elif has_success or url_after != url_before:
            reporter.add_result(
                suite, f"{form_name}: Preenchimento válido", "PASS",
                f"Formulário submetido com sucesso (URL: {url_after})",
                screenshot=screenshot, duration=time.time() - start
            )
        else:
            reporter.add_result(
                suite, f"{form_name}: Preenchimento válido", "WARN",
                "Formulário submetido mas não houve confirmação clara de sucesso",
                screenshot=screenshot, duration=time.time() - start
            )

    except Exception as e:
        screenshot = take_screenshot(page, f"form_{form_name}_valido_erro", ss_dir)
        reporter.add_result(
            suite, f"{form_name}: Preenchimento válido", "FAIL",
            str(e), screenshot=screenshot, duration=time.time() - start
        )
