"""
ai_analyzer.py - Analise visual de screenshots usando Claude API
Funcoes: analyze_screenshot, compare_viewports, format_issues_for_report
Instrucoes customizadas do QA Panel: utils/ai_custom.py
"""
import base64
import anthropic
from pathlib import Path


def _encode_image(image_path):
    return base64.standard_b64encode(Path(image_path).read_bytes()).decode("utf-8")


def analyze_screenshot(image_path, page_name, config, extra_context=""):
    ai_cfg  = config.get("ai", {})
    api_key = ai_cfg.get("api_key", "")
    model   = ai_cfg.get("model", "claude-opus-4-6")
    checks  = ai_cfg.get("visual_checks", [])

    if not api_key or api_key.startswith("TODO"):
        return {
            "status":  "WARN",
            "summary": "API key nao configurada - analise visual desativada",
            "details": "Configure ai.api_key no config.yaml para ativar a IA.",
            "issues":  [],
        }

    checks_text = "\n".join(f"  - {c}" for c in checks) if checks else \
        "  - bugs visuais gerais\n  - elementos quebrados\n  - layout inconsistente"

    ctx = f"Contexto: {extra_context}\n\n" if extra_context else ""
    prompt = (
        f"Voce e especialista em QA visual de apps web.\n"
        f"Analise o screenshot da pagina '{page_name}' (React/Next.js).\n\n"
        f"{ctx}"
        f"Verifique:\n{checks_text}\n\n"
        f"Responda APENAS em JSON puro:\n"
        f'{{"status":"PASS|WARN|FAIL","summary":"1 linha",'
        f'"issues":[{{"severity":"alto|medio|baixo","description":"...","location":"..."}}],'
        f'"details":"analise completa"}}\n\n'
        f"PASS=sem problemas, WARN=pequenos problemas, FAIL=problemas serios."
    )

    try:
        client  = anthropic.Anthropic(api_key=api_key)
        img_b64 = _encode_image(image_path)
        ext     = Path(image_path).suffix.lower().replace(".", "")
        media   = f"image/{ext if ext in ('png','jpg','jpeg','gif','webp') else 'png'}"

        msg = client.messages.create(
            model=model, max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": img_b64}},
                {"type": "text",  "text": prompt},
            ]}],
        )
        raw = msg.content[0].text.strip()
        import json, re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            r = json.loads(m.group())
            return {"status": r.get("status","WARN"), "summary": r.get("summary",""),
                    "details": r.get("details",""), "issues": r.get("issues",[])}
        return {"status": "WARN", "summary": "Formato inesperado", "details": raw, "issues": []}

    except Exception as e:
        return {"status": "WARN", "summary": f"Erro API: {type(e).__name__}",
                "details": str(e), "issues": []}


def compare_viewports(desktop_path, mobile_path, page_name, config):
    ai_cfg  = config.get("ai", {})
    api_key = ai_cfg.get("api_key", "")
    model   = ai_cfg.get("model", "claude-opus-4-6")

    if not api_key or api_key.startswith("TODO"):
        return {"status": "WARN", "summary": "API key nao configurada", "details": "", "issues": []}

    prompt = (
        f"Compare os screenshots da pagina '{page_name}': 1a=desktop(1280px), 2a=mobile(375px).\n"
        f"Verifique: layout adaptado, elementos cortados, menu mobile, legibilidade, tamanho botoes.\n"
        f"JSON puro: {{\"status\":\"PASS|WARN|FAIL\",\"summary\":\"1 linha\","
        f"\"issues\":[{{\"severity\":\"alto|medio|baixo\",\"description\":\"...\",\"location\":\"...\"}}],"
        f"\"details\":\"analise comparativa\"}}"
    )

    def enc(p):
        return base64.standard_b64encode(Path(p).read_bytes()).decode("utf-8")

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model, max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": enc(desktop_path)}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": enc(mobile_path)}},
                {"type": "text",  "text": prompt},
            ]}],
        )
        raw = msg.content[0].text.strip()
        import json, re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            r = json.loads(m.group())
            return {"status": r.get("status","WARN"), "summary": r.get("summary",""),
                    "details": r.get("details",""), "issues": r.get("issues",[])}
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
            color = {"ALTO": "#dc2626", "MEDIO": "#d97706", "BAIXO": "#16a34a"}.get(sev, "#64748b")
            loc_html = f" <em>({loc})</em>" if loc else ""
            lines.append(
                f'<li><span style="color:{color};font-weight:700">[{sev}]</span> {desc}{loc_html}</li>'
            )
        lines.append("</ul>")
    return "".join(lines)
