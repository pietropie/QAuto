"""
ai_analyzer.py - Analise visual de screenshots com suporte a OpenAI, DeepSeek e Claude
"""
import base64
import json
import re
from pathlib import Path


def _encode_image(image_path):
    """Extrai base64 de data URL ou lê de arquivo de disco."""
    if isinstance(image_path, str) and image_path.startswith("data:"):
        return image_path.split(",", 1)[1]
    return base64.standard_b64encode(Path(image_path).read_bytes()).decode("utf-8")


def _media_type(image_path):
    """Detecta media type de data URL ou extensão de arquivo."""
    if isinstance(image_path, str) and image_path.startswith("data:"):
        return image_path.split(";")[0].split(":")[1]
    ext = Path(image_path).suffix.lower().replace(".", "")
    return "image/" + (ext if ext in ("png", "jpg", "jpeg", "gif", "webp") else "jpeg")


def _parse_json(raw):
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return None


def _call_ai(prompt, image_path, api_key, model, provider):
    """Chama a IA correta baseado no provider."""
    img_b64 = _encode_image(image_path)
    media   = _media_type(image_path)

    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": img_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return response.content[0].text.strip()

    else:
        # OpenAI ou DeepSeek (ambos usam SDK openai)
        from openai import OpenAI
        base_url = "https://api.deepseek.com/v1" if provider == "deepseek" else None
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:" + media + ";base64," + img_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return response.choices[0].message.content.strip()


def analyze_screenshot(image_path, page_name, config, extra_context=""):
    ai_cfg   = config.get("ai", {})
    api_key  = ai_cfg.get("api_key", "")
    model    = ai_cfg.get("model", "gpt-4o-mini")
    provider = ai_cfg.get("provider", "openai").lower()
    checks   = ai_cfg.get("visual_checks", [])

    if not api_key or api_key.startswith("TODO"):
        return {
            "status": "WARN", "summary": "API key nao configurada - analise visual desativada",
            "details": "Configure a API key no painel (aba Config IA).", "issues": [],
        }

    checks_text = "\n".join("  - " + c for c in checks) if checks else \
        "  - bugs visuais gerais\n  - elementos quebrados\n  - layout inconsistente"

    ctx = ("Contexto: " + extra_context + "\n\n") if extra_context else ""
    prompt = (
        "Voce e especialista em QA visual de apps web.\n"
        "Analise o screenshot da pagina '" + page_name + "' (React/Next.js).\n\n"
        + ctx +
        "Verifique:\n" + checks_text + "\n\n"
        "Responda APENAS em JSON puro:\n"
        '{"status":"PASS|WARN|FAIL","summary":"1 linha",'
        '"issues":[{"severity":"alto|medio|baixo","description":"...","location":"..."}],'
        '"details":"analise completa"}\n\n'
        "PASS=sem problemas, WARN=pequenos problemas, FAIL=problemas serios."
    )

    try:
        raw = _call_ai(prompt, image_path, api_key, model, provider)
        parsed = _parse_json(raw)
        if parsed:
            return {
                "status":  parsed.get("status", "WARN"),
                "summary": parsed.get("summary", ""),
                "details": parsed.get("details", ""),
                "issues":  parsed.get("issues", []),
            }
        return {"status": "WARN", "summary": "Formato inesperado", "details": raw, "issues": []}
    except Exception as e:
        return {"status": "WARN", "summary": "Erro API: " + type(e).__name__, "details": str(e), "issues": []}


def compare_viewports(desktop_path, mobile_path, page_name, config):
    ai_cfg   = config.get("ai", {})
    api_key  = ai_cfg.get("api_key", "")
    model    = ai_cfg.get("model", "gpt-4o-mini")
    provider = ai_cfg.get("provider", "openai").lower()

    if not api_key or api_key.startswith("TODO"):
        return {"status": "WARN", "summary": "API key nao configurada", "details": "", "issues": []}

    prompt = (
        "Compare os screenshots da pagina '" + page_name + "': 1a imagem=desktop(1280px), 2a imagem=mobile(375px).\n"
        "Verifique: layout adaptado, elementos cortados, menu mobile, legibilidade, tamanho botoes.\n"
        'JSON puro: {"status":"PASS|WARN|FAIL","summary":"1 linha",'
        '"issues":[{"severity":"alto|medio|baixo","description":"...","location":"..."}],'
        '"details":"analise comparativa"}'
    )

    def enc(p):
        if isinstance(p, str) and p.startswith("data:"):
            return p.split(",", 1)[1]
        return base64.standard_b64encode(Path(p).read_bytes()).decode("utf-8")

    try:
        if provider == "claude":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=model, max_tokens=1024,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": enc(desktop_path)}},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": enc(mobile_path)}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            raw = response.content[0].text.strip()
        else:
            from openai import OpenAI
            base_url = "https://api.deepseek.com/v1" if provider == "deepseek" else None
            client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
            response = client.chat.completions.create(
                model=model, max_tokens=1024,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + enc(desktop_path)}},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + enc(mobile_path)}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            raw = response.choices[0].message.content.strip()

        parsed = _parse_json(raw)
        if parsed:
            return {
                "status":  parsed.get("status", "WARN"),
                "summary": parsed.get("summary", ""),
                "details": parsed.get("details", ""),
                "issues":  parsed.get("issues", []),
            }
        return {"status": "WARN", "summary": "Formato inesperado", "details": raw, "issues": []}
    except Exception as e:
        return {"status": "WARN", "summary": str(e), "details": "", "issues": []}


def format_issues_for_report(analysis):
    lines = []
    if analysis.get("details"):
        lines.append(analysis["details"])
    if analysis.get("issues"):
        lines.append("<br><strong>Problemas encontrados:</strong><ul>")
        for issue in analysis["issues"]:
            sev   = issue.get("severity", "?").upper()
            desc  = issue.get("description", "")
            loc   = issue.get("location", "")
            colors = {"ALTO": "#dc2626", "MEDIO": "#d97706", "BAIXO": "#16a34a"}
            color = colors.get(sev, "#64748b")
            loc_html = " <em>(" + loc + ")</em>" if loc else ""
            lines.append(
                '<li><span style="color:' + color + ';font-weight:700">[' + sev + ']</span> ' + desc + loc_html + '</li>'
            )
        lines.append("</ul>")
    return "".join(lines)
