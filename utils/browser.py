"""
browser.py — Setup e gerenciamento do Playwright
"""

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from pathlib import Path
import time


def create_browser(config: dict):
    """Cria e retorna (playwright, browser, context) com as configs do config.yaml."""
    browser_cfg = config.get("browser", {})
    report_cfg  = config.get("report", {})

    pw       = sync_playwright().start()
    # --no-sandbox e flags adicionais sao necessarios para rodar em Docker como root
    browser  = pw.chromium.launch(
        headless=browser_cfg.get("headless", True),
        slow_mo=browser_cfg.get("slow_mo", 0),
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process",
        ],
    )
    context  = browser.new_context(
        viewport={"width": 1280, "height": 800},
        record_video_dir=report_cfg.get("screenshots_dir", "./screenshots")
        if browser_cfg.get("video_on_failure") else None,
    )
    context.set_default_timeout(browser_cfg.get("timeout", 30000))
    return pw, browser, context


def login(page: Page, config: dict) -> bool:
    """
    Realiza login na aplicação.
    Retorna True se bem-sucedido ou se login estiver desativado.
    Para desativar, defina login.enabled: false no config.yaml.
    """
    login_cfg = config.get("app", {}).get("login", {})

    if not login_cfg.get("enabled", True):
        print("  [INFO] Login desativado (login.enabled: false) — pulando etapa.")
        return True

    base_url  = config["app"]["base_url"].rstrip("/")
    login_url = base_url + login_cfg.get("url_path", "/login")

    try:
        page.goto(login_url, wait_until="networkidle")
        page.fill(login_cfg["username_selector"], login_cfg.get("username", ""))
        page.fill(login_cfg["password_selector"], login_cfg.get("password", ""))
        page.click(login_cfg["submit_selector"])
        page.wait_for_load_state("networkidle")

        # Verifica se o login foi bem-sucedido
        success_sel = login_cfg.get("success_indicator", "")
        if success_sel:
            page.wait_for_selector(success_sel, timeout=10000)

        return True

    except Exception as e:
        print(f"  [ERRO] Falha no login: {e}")
        return False


def take_screenshot(page: Page, name: str, screenshots_dir: str = "") -> str:
    """
    Tira um screenshot JPEG e retorna como data URL base64.
    O arquivo também é salvo em disco se screenshots_dir for informado.
    Retorna string vazia em caso de erro.
    """
    import base64
    try:
        buf = page.screenshot(type="jpeg", quality=55, full_page=False)
        b64 = base64.b64encode(buf).decode("ascii")
        # Salva em disco opcionalmente (backup)
        if screenshots_dir:
            try:
                Path(screenshots_dir).mkdir(parents=True, exist_ok=True)
                safe_name = name.replace(" ", "_").replace("/", "-").replace(":", "-")
                disk_path = str(Path(screenshots_dir) / f"{safe_name}_{int(time.time())}.jpg")
                Path(disk_path).write_bytes(buf)
            except Exception:
                pass
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"  [WARN] Screenshot falhou ({name}): {e}")
        return ""


def navigate_to(page: Page, base_url: str, path: str):
    """Navega para uma URL da aplicação e aguarda carregamento."""
    url = base_url.rstrip("/") + path
    page.goto(url, wait_until="networkidle")
    return url
