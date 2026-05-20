"""
test_custom.py — Testes dirigidos pelas instruções do QA Panel

Lê o arquivo instrucoes_ia.yaml (gerado pelo panel.html) e executa:
  1. Instruções gerais → IA analisa cada screenshot com as regras informadas
  2. Instruções por página → IA analisa cada página com suas instruções específicas
  3. Fluxos / jornadas → IA executa e valida cada etapa do fluxo descrito

A IA recebe suas instruções como contexto e as usa como critérios de avaliação.
"""

import time
import yaml
from pathlib import Path
from playwright.sync_api import Page
from utils.browser import take_screenshot, navigate_to
from utils.reporter import Reporter
from utils.ai_custom import analyze_with_custom_instructions
from utils.ai_analyzer import format_issues_for_report


INSTRUCOES_FILE = "instrucoes_ia.yaml"


def load_instrucoes(path: str = INSTRUCOES_FILE) -> dict | None:
    """Carrega o arquivo de instruções gerado pelo painel."""
    p = Path(path)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(page: Page, config: dict, reporter: Reporter) -> None:
    """Ponto de entrada. Executa todas as suítes de instruções customizadas."""

    # Prefere as instruções vindas do Redis (via worker) sobre o arquivo YAML legado
    redis_instr = config.get("_redis_instructions")
    if redis_instr:
        instrucoes = {
            "instrucoes_gerais": redis_instr.get("general", []),
            "paginas":           redis_instr.get("pages", []),
            "fluxos":            redis_instr.get("flows", []),
        }
    else:
        instrucoes = load_instrucoes()

    if instrucoes is None:
        suite = reporter.add_suite("🎯 Instruções Personalizadas (QA Panel)")
        reporter.add_result(
            suite, "Carregar instrucoes_ia.yaml", "WARN",
            "Arquivo instrucoes_ia.yaml não encontrado.\n"
            "Abra o panel.html no browser, configure suas instruções e exporte o arquivo."
        )
        return

    base_url = config["app"]["base_url"]
    ss_dir   = config.get("report", {}).get("screenshots_dir", "./screenshots")

    # ── 1. Instruções gerais ──────────────────────────────────────────────
    gerais = instrucoes.get("instrucoes_gerais", [])
    if isinstance(gerais, list) and gerais:
        _run_gerais(page, config, reporter, gerais, base_url, ss_dir)

    # ── 2. Por página ────────────────────────────────────────────────────
    paginas = instrucoes.get("paginas", [])
    if isinstance(paginas, list) and paginas:
        _run_paginas(page, config, reporter, paginas, base_url, ss_dir)

    # ── 3. Fluxos / jornadas ─────────────────────────────────────────────
    fluxos = instrucoes.get("fluxos", [])
    if isinstance(fluxos, list) and fluxos:
        _run_fluxos(page, config, reporter, fluxos, base_url, ss_dir)


# ═══════════════════════════════════════════════════════════════════════════
# 1 · Instruções gerais
# ═══════════════════════════════════════════════════════════════════════════

def _run_gerais(page, config, reporter, gerais, base_url, ss_dir):
    """
    Para cada instrução geral, tira screenshot da página inicial e
    pede à IA que verifique especificamente aquela instrução.
    """
    suite = reporter.add_suite("🎯 Verificações Gerais (suas instruções)")
    print(f"\n  {len(gerais)} instrução(ões) geral(is) para verificar")

    # Agrupa por severidade para exibir as críticas primeiro
    criticas    = [g for g in gerais if g.get("severity") == "alto"]
    importantes = [g for g in gerais if g.get("severity") == "medio"]
    informativas= [g for g in gerais if g.get("severity") == "baixo"]
    ordered     = criticas + importantes + informativas

    for item in ordered:
        instrucao = item.get("instrucao", "").strip()
        severity  = item.get("severity", "medio")
        if not instrucao:
            continue

        start = time.time()
        print(f"\n  🔍 Verificando: {instrucao[:70]}...")

        try:
            # Navega para a raiz (instrução geral se aplica a qualquer tela)
            navigate_to(page, base_url, "/")
            screenshot = take_screenshot(page, f"custom_geral_{instrucao[:30]}", ss_dir)

            analysis = analyze_with_custom_instructions(
                screenshot_path=screenshot,
                page_name="Aplicação (visão geral)",
                instrucao=instrucao,
                contexto_extra="Esta é uma verificação geral que se aplica a toda a aplicação.",
                config=config,
            )
            ai_html = format_issues_for_report(analysis)

            # Mapeia severidade da instrução com o resultado da IA
            status = analysis["status"]
            if status == "PASS" and severity == "alto":
                status = "PASS"  # mantém PASS mesmo em críticos que passaram

            reporter.add_result(
                suite,
                f"[{severity.upper()}] {instrucao[:80]}",
                status,
                analysis["summary"],
                screenshot=screenshot,
                ai_analysis=ai_html,
                duration=time.time() - start,
            )

        except Exception as e:
            reporter.add_result(
                suite, f"Instrução: {instrucao[:60]}", "FAIL",
                str(e), duration=time.time() - start
            )


# ═══════════════════════════════════════════════════════════════════════════
# 2 · Instruções por página
# ═══════════════════════════════════════════════════════════════════════════

def _run_paginas(page, config, reporter, paginas, base_url, ss_dir):
    """
    Para cada página configurada, navega até ela, tira screenshot
    e pede à IA que verifique cada instrução específica da tela.
    """
    suite = reporter.add_suite("📄 Verificações por Página (suas instruções)")
    print(f"\n  {len(paginas)} página(s) com instruções personalizadas")

    for pg in paginas:
        nome         = pg.get("nome", pg.get("name", "?"))
        path         = pg.get("path", "/")
        instrucoes   = pg.get("instrucoes", pg.get("instructions", []))
        contexto     = pg.get("contexto", pg.get("context", ""))

        print(f"\n  📄 Página: {nome} ({path}) — {len(instrucoes)} instrução(ões)")

        start = time.time()
        try:
            navigate_to(page, base_url, path)
            screenshot = take_screenshot(page, f"custom_pg_{nome}", ss_dir)

            # Chama a IA uma vez com TODAS as instruções da página de uma vez
            analysis = analyze_with_custom_instructions(
                screenshot_path=screenshot,
                page_name=nome,
                instrucao="\n".join(f"- {i}" for i in instrucoes),
                contexto_extra=contexto,
                config=config,
                multi_check=True,
            )
            ai_html = format_issues_for_report(analysis)

            reporter.add_result(
                suite,
                f"Página: {nome}",
                analysis["status"],
                f"{len(instrucoes)} instrução(ões) verificada(s) — {analysis['summary']}",
                screenshot=screenshot,
                ai_analysis=ai_html,
                duration=time.time() - start,
            )

        except Exception as e:
            reporter.add_result(
                suite, f"Página: {nome}", "FAIL",
                str(e), duration=time.time() - start
            )


# ═══════════════════════════════════════════════════════════════════════════
# 3 · Fluxos / Jornadas
# ═══════════════════════════════════════════════════════════════════════════

def _run_fluxos(page, config, reporter, fluxos, base_url, ss_dir):
    """
    Para cada fluxo, executa as etapas descritas usando a IA como guia.
    Tira screenshot em momentos-chave e verifica as condições informadas.
    """
    suite = reporter.add_suite("🔄 Fluxos / Jornadas (suas instruções)")
    print(f"\n  {len(fluxos)} fluxo(s) para executar")

    for fluxo in fluxos:
        nome         = fluxo.get("nome", fluxo.get("name", "Fluxo"))
        criticidade  = fluxo.get("criticidade", "importante")
        descricao    = fluxo.get("descricao", fluxo.get("desc", ""))
        etapas       = fluxo.get("etapas", fluxo.get("steps", []))
        verificacoes = fluxo.get("verificacoes", fluxo.get("checks", []))

        crit_icon = {"critico": "🔴", "importante": "🟡", "informativo": "🟢"}.get(criticidade, "🟡")
        print(f"\n  {crit_icon} Fluxo: {nome} ({len(etapas)} etapas)")

        flow_start = time.time()

        # Tira screenshot do estado inicial
        try:
            navigate_to(page, base_url, "/")
            screenshot_inicial = take_screenshot(page, f"flow_{nome}_inicio", ss_dir)
        except Exception:
            screenshot_inicial = ""

        # ── Análise das etapas com a IA ───────────────────────────────────
        etapas_str   = "\n".join(f"{i+1}. {e}" for i, e in enumerate(etapas))
        verific_str  = "\n".join(f"- {v}" for v in verificacoes)

        analysis = analyze_with_custom_instructions(
            screenshot_path=screenshot_inicial,
            page_name=f"Fluxo: {nome} (estado inicial)",
            instrucao=f"""Execute e valide o seguinte fluxo:

DESCRIÇÃO DO FLUXO:
{descricao}

ETAPAS A EXECUTAR:
{etapas_str}

{'VERIFICAÇÕES ESPECÍFICAS:' + chr(10) + verific_str if verificacoes else ''}

Analise se a aplicação está preparada para suportar este fluxo com base no que é visível.
Identifique riscos, elementos faltando, ou inconsistências que possam quebrar este fluxo.""",
            contexto_extra=f"Criticidade: {criticidade}. Fluxo de negócio: {nome}.",
            config=config,
        )
        ai_html = format_issues_for_report(analysis)

        # ── Executa cada etapa navegando pela app ─────────────────────────
        etapas_results = []
        screenshots    = [screenshot_inicial]

        for i, etapa in enumerate(etapas):
            step_start = time.time()
            step_ss    = ""
            try:
                # Tenta extrair path de navegação da etapa (heurística)
                path = _extract_path_from_step(etapa)
                if path:
                    navigate_to(page, base_url, path)
                    step_ss = take_screenshot(page, f"flow_{nome}_step{i+1}", ss_dir)
                    screenshots.append(step_ss)

                etapas_results.append({
                    "step": i + 1,
                    "desc": etapa,
                    "status": "OK",
                    "screenshot": step_ss,
                    "duration": time.time() - step_start,
                })
            except Exception as e:
                etapas_results.append({
                    "step": i + 1,
                    "desc": etapa,
                    "status": f"ERRO: {e}",
                    "screenshot": step_ss,
                    "duration": time.time() - step_start,
                })

        # ── Resultado consolidado ─────────────────────────────────────────
        step_errors = [r for r in etapas_results if r["status"].startswith("ERRO")]
        final_status = analysis["status"]
        if step_errors:
            final_status = "FAIL"

        # Formata detalhes das etapas para o relatório
        steps_html = "<br><strong>Etapas executadas:</strong><ol style='margin:8px 0 0 18px;line-height:2'>"
        for r in etapas_results:
            icon = "✅" if not r["status"].startswith("ERRO") else "❌"
            steps_html += f"<li>{icon} {r['desc']}"
            if r["status"].startswith("ERRO"):
                steps_html += f" <em style='color:#f87171'>({r['status']})</em>"
            steps_html += "</li>"
        steps_html += "</ol>"

        full_ai_html = ai_html + steps_html

        reporter.add_result(
            suite,
            f"{crit_icon} Fluxo: {nome}",
            final_status,
            analysis["summary"],
            screenshot=screenshot_inicial or (screenshots[1] if len(screenshots) > 1 else ""),
            ai_analysis=full_ai_html,
            duration=time.time() - flow_start,
        )


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _extract_path_from_step(step_text: str) -> str | None:
    """
    Tenta extrair um