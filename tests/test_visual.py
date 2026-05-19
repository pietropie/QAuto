"""
test_visual.py — Análise visual com IA (Claude)

Para cada página configurada:
  1. Tira screenshot em desktop e mobile
  2. Envia para a IA analisar bugs visuais
  3. Compara os viewports para verificar responsividade
"""

import time
from playwright.sync_api import Page, BrowserContext
from utils.browser import take_screenshot, navigate_to
from utils.ai_analyzer import analyze_screenshot, compare_viewports, format_issues_for_report
from utils.reporter import Reporter


def run(page: Page, context: BrowserContext, config: dict, reporter: Reporter) -> None:
    suite      = reporter.add_suite("🎨 Análise Visual com IA")
    visual_cfg = config.get("flows", {}).get("visual", {})
    ai_cfg     = config.get("ai", {})
    base_url   = config["app"]["base_url"]
    ss_dir     = config.get("report", {}).get("screenshots_dir", "./screenshots")

    if not visual_cfg.get("enabled", True):
        reporter.add_result(suite, "Visual", "WARN", "Desativado no config.yaml")
        return

    if not ai_cfg.get("enabled", True):
        reporter.add_result(suite, "Visual IA", "WARN", "IA desativada no config.yaml")
        return

    pages_to_check = visual_cfg.get("pages_to_screenshot", ["/"])
    check_responsive = visual_cfg.get("check_responsive", True)
    viewports = visual_cfg.get("viewports", {})
    desktop_vp = viewports.get("desktop", {"width": 1280, "height": 800})
    mobile_vp  = viewports.get("mobile",  {"width": 375,  "height": 812})

    for path in pages_to_check:
        page_label = path if path != "/" else "Home"
        print(f"\n  Analisando visualmente: {page_label}")

        # ---------------------------------------------------------------- #
        # Desktop screenshot + análise IA
        # ---------------------------------------------------------------- #
        start = time.time()
        try:
            page.set_viewport_size(desktop_vp)
            navigate_to(page, base_url, path)
            desktop_ss = take_screenshot(page, f"visual_desktop_{page_label}", ss_dir)

            analysis = analyze_screenshot(desktop_ss, f"{page_label} (desktop)", config)
            ai_html  = format_issues_for_report(analysis)

            reporter.add_result(
                suite,
                f"Desktop: {page_label}",
                analysis["status"],
                analysis["summary"],
                screenshot=desktop_ss,
                ai_analysis=ai_html,
                duration=time.time() - start,
            )

        except Exception as e:
            reporter.add_result(
                suite, f"Desktop: {page_label}", "FAIL",
                str(e), duration=time.time() - start
            )
            continue

        if not check_responsive:
            continue

        # ---------------------------------------------------------------- #
        # Mobile screenshot + análise IA
        # ---------------------------------------------------------------- #
        start = time.time()
        mobile_ss = None
        try:
            # Cria nova página com viewport mobile para não afetar o fluxo principal
            mobile_page = context.new_page()
            mobile_page.set_viewport_size(mobile_vp)

            # Copia cookies/sessão da página principal
            storage = context.storage_state()

            navigate_to(mobile_page, base_url, path)
            mobile_ss = take_screenshot(mobile_page, f"visual_mobile_{page_label}", ss_dir)

            mobile_analysis = analyze_screenshot(
                mobile_ss, f"{page_label} (mobile 375px)", config,
                extra_context="Esta é a versão mobile da tela — verifique responsividade."
            )
            ai_html_mobile = format_issues_for_report(mobile_analysis)

            reporter.add_result(
                suite,
                f"Mobile: {page_label}",
                mobile_analysis["status"],
                mobile_analysis["summary"],
                screenshot=mobile_ss,
                ai_analysis=ai_html_mobile,
                duration=time.time() - start,
            )
            mobile_page.close()

        except Exception as e:
            reporter.add_result(
                suite, f"Mobile: {page_label}", "FAIL",
                str(e), duration=time.time() - start
            )
            continue

        # ---------------------------------------------------------------- #
        # Comparação desktop vs mobile (responsividade)
        # ---------------------------------------------------------------- #
        if mobile_ss:
            start = time.time()
            try:
                resp_analysis = compare_viewports(
                    desktop_ss, mobile_ss, page_label, config
                )
                ai_html_resp = format_issues_for_report(resp_analysis)

                reporter.add_result(
                    suite,
                    f"Responsividade: {page_label}",
                    resp_analysis["status"],
                    resp_analysis["summary"],
                    screenshot=mobile_ss,
                    ai_analysis=ai_html_resp,
                    duration=time.time() - start,
                )
            except Exception as e:
                reporter.add_result(
                    suite, f"Responsividade: {page_label}", "WARN",
                    f"Não foi possível comparar viewports: {e}",
                    duration=time.time() - start
                )

    # Restaura viewport desktop ao final
    page.set_viewport_size(desktop_vp)
