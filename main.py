#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║         QA Automation Framework — React/Next.js             ║
║  Playwright + IA (Claude) para testes funcionais e visuais  ║
╚══════════════════════════════════════════════════════════════╝

Como usar:
  1. Edite o arquivo config.yaml com URL, credenciais e fluxos da sua app
  2. Abra panel.html no browser para configurar instruções personalizadas
  3. python main.py                    → roda todos os testes
  4. python main.py --suite navegacao  → roda só navegação
  5. python main.py --suite formularios
  6. python main.py --suite pedidos
  7. python main.py --suite visual
  8. python main.py --suite custom     → roda só as instruções do QA Panel
  9. python main.py --no-headless      → abre o browser na tela (ótimo para debug)
"""

import sys
import time
import argparse
import yaml
from pathlib import Path
from colorama import init, Fore, Style

# Inicializa colorama (suporte a cores no Windows)
init(autoreset=True)

# ------------------------------------------------------------------ #
# Imports internos
# ------------------------------------------------------------------ #
from utils.browser   import create_browser, login
from utils.reporter  import Reporter
from tests           import test_navigation, test_forms, test_orders, test_visual, test_custom


def load_config(path: str = "config.yaml") -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        print(f"{Fore.RED}✗ Arquivo de configuração não encontrado: {path}")
        sys.exit(1)
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_config(config: dict):
    """Valida campos obrigatórios e avisa sobre TODOs."""
    warnings = []
    base_url = config.get("app", {}).get("base_url", "")
    if not base_url or "TODO" in base_url:
        print(f"{Fore.RED}✗ ERRO: Configure app.base_url no config.yaml antes de rodar.")
        sys.exit(1)

    username = config.get("app", {}).get("login", {}).get("username", "")
    password = config.get("app", {}).get("login", {}).get("password", "")
    if "TODO" in username or "TODO" in password:
        warnings.append("Credenciais de login não configuradas (app.login.username / password)")

    ai_key = config.get("ai", {}).get("api_key", "")
    if "TODO" in ai_key:
        warnings.append("API key da IA não configurada (ai.api_key) — análise visual desativada")

    for w in warnings:
        print(f"{Fore.YELLOW}⚠  {w}")

    return warnings


def print_banner(config: dict):
    url = config["app"]["base_url"]
    print(f"\n{Style.BRIGHT}{'═' * 60}")
    print(f"  🔍  QA Automation Framework")
    print(f"  📍  {url}")
    print(f"  ⏰  {time.strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'═' * 60}{Style.RESET_ALL}\n")


def print_summary(reporter: Reporter):
    all_tests = [t for s in reporter.suites for t in s["tests"]]
    total     = len(all_tests)
    passed    = sum(1 for t in all_tests if t["status"] == "PASS")
    failed    = sum(1 for t in all_tests if t["status"] == "FAIL")
    warned    = sum(1 for t in all_tests if t["status"] == "WARN")

    print(f"\n{'═' * 60}")
    print(f"  📊  RESULTADO FINAL")
    print(f"{'─' * 60}")
    print(f"  {Fore.GREEN}✅ Passou:  {passed}/{total}")
    print(f"  {Fore.RED}❌ Falhou:  {failed}/{total}")
    print(f"  {Fore.YELLOW}⚠️  Avisos:  {warned}/{total}")
    print(f"{'═' * 60}\n")

    if failed > 0:
        print(f"{Fore.RED}  Testes com falha:")
        for suite in reporter.suites:
            for t in suite["tests"]:
                if t["status"] == "FAIL":
                    print(f"    ✗ [{suite['name'].split()[-1]}] {t['name']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="QA Automation — testes funcionais e visuais com IA"
    )
    parser.add_argument("--config",      default="config.yaml",
                        help="Caminho do arquivo de configuração")
    parser.add_argument("--suite",       default="all",
                        choices=["all", "navegacao", "formularios", "pedidos", "visual", "custom"],
                        help="Qual suíte rodar (padrão: all)")
    parser.add_argument("--no-headless", action="store_true",
                        help="Abre o browser na tela (útil para debug)")
    args = parser.parse_args()

    # ------------------------------------------------------------------ #
    # Carrega config
    # ------------------------------------------------------------------ #
    config = load_config(args.config)
    if args.no_headless:
        config.setdefault("browser", {})["headless"] = False

    print_banner(config)
    warnings = validate_config(config)

    # ------------------------------------------------------------------ #
    # Inicializa browser e reporter
    # ------------------------------------------------------------------ #
    reporter = Reporter(config)
    pw, browser, context = create_browser(config)
    page = context.new_page()

    try:
        # ---------------------------------------------------------------- #
        # Login
        # ---------------------------------------------------------------- #
        print(f"{Fore.CYAN}🔐 Fazendo login em {config['app']['base_url']}...")
        login_ok = login(page, config)
        if not login_ok:
            print(f"{Fore.RED}✗ Login falhou — verifique credenciais no config.yaml")
            # Continua mesmo assim (pode ser app sem login)

        # ---------------------------------------------------------------- #
        # Executa suítes selecionadas
        # ---------------------------------------------------------------- #
        suite_map = {
            "navegacao":   ("🧭 Navegação",              lambda: test_navigation.run(page, config, reporter)),
            "formularios": ("📋 Formulários",            lambda: test_forms.run(page, config, reporter)),
            "pedidos":     ("🛒 Pedidos",                 lambda: test_orders.run(page, config, reporter)),
            "visual":      ("🎨 Visual IA",               lambda: test_visual.run(page, context, config, reporter)),
            "custom":      ("🎯 Instruções do QA Panel", lambda: test_custom.run(page, config, reporter)),
        }

        suites_to_run = list(suite_map.keys()) if args.suite == "all" else [args.suite]

        for key in suites_to_run:
            name, fn = suite_map[key]
            print(f"\n{Style.BRIGHT}{Fore.CYAN}▶ Iniciando: {name}{Style.RESET_ALL}")
            try:
                fn()
            except Exception as e:
                print(f"{Fore.RED}  ERRO CRÍTICO na suíte {name}: {e}")

    finally:
        # ---------------------------------------------------------------- #
        # Salva relatório e fecha browser
        # ---------------------------------------------------------------- #
        report_path = r