"""
ai_custom.py - Analise visual com instrucoes personalizadas do QA Panel
Suporte a OpenAI, DeepSeek e Claude (Anthropic)
"""
import base64
import json
import re
from pathlib import Path


def _enc(p):
    return base64.standard_b64encode(Path(p).read_bytes()).decode("utf-8")


def _parse_json(raw):
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        return json.loads(m.group())
    return None


def _call_ai(prompt, screenshot_path, api_key, model, provider):
    img_b64 = _enc(screenshot_path)
    ext = Path(screenshot_path).suffix.lower().replace(".", "")
    media = "image/" + (ext if ext in ("png", "jpg", "jpeg", "gif", "webp") else "png")

    if provider == "claude":
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media, "data": img_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return response.content[0].text.strip()
    else:
        from openai import OpenAI
        base_url = "https://api.deepseek.com/v1" if provider == "deepseek" else None
        client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model, max_tokens=1500,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:" + media + ";base64," + img_b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        return response.choices[0].message.content.strip()


def analyze_with_custom_instructions(
    screenshot_path, page_name, instrucao, config,
    contexto_extra="", multi_check=False,
):
    ai_cfg   = config.get("ai", {})
    api_key  = ai_cfg.get("api_key", "")
    model    = ai_cfg.get("model", "gpt-4o-mini")
    provider = ai_cfg.get("provider", "openai").lower()

    if not api_key or api_key.startswith("TODO"):
        return {
            "status": "WARN", "summary": "API key nao configurada",
            "details": "Configure a API key no painel (aba Config IA).", "issues": [],
        }

    ctx_line = ("CONTEXTO: " + contexto_extra + "\n\n") if contexto_extra else ""

    if multi_check:
        prompt = (
            "Voce e especialista em QA de apps React/Next.js.\n"
            "Analise o screenshot da pagina '" + page_name + "'.\n\n"
            + ctx_line +
            "Verifique CADA item abaixo:\n" + instrucao + "\n\n"
            "Responda em JSON puro:\n"
            '{"status":"PASS|WARN|FAIL","summary":"1 linha",'
            '"issues":[{"severity":"alto|medio|baixo","description":"...","location":"..."}],'
            '"details":"analise completa"}\n\n'
            "PASS=tudo OK, WARN=suspeito, FAIL=problema confirmado."
        )
    else:
        prompt = (
            "Voce e especialista em QA de apps React/Next.js.\n"
            "Analise o screenshot da pagina '" + page_name + "'.\n\n"
            + ctx_line +
            'INSTRUCAO DO TIME DE QA:\n"' + instrucao + '"\n\n'
            "Responda em JSON puro:\n"
            '{"status":"PASS|WARN|FAIL","summary":"o item foi atendido?",'
            '"issues":[{"severity":"alto|medio|baixo","description":"...","location":"..."}],'
            '"details":"analise detalhada"}\n\n'
            "PASS=atendida, WARN=nao confirmar so pelo visual, FAIL=claramente violada."
        )

    try:
        raw = _call_ai(prompt, screenshot_path, api_key, model, provider)
        parsed = _parse_json(raw)
        if parsed:
            return {
                "status":  parsed.get("status",  "WARN"),
                "summary": parsed.get("summary", ""),
                "details": parsed.get("details", ""),
                "issues":  parsed.get("issues",  []),
            }
        return {"status": "WARN", "summary": "Formato inesperado", "details": raw, "issues": []}
    except Exception as e:
        return {"status": "WARN", "summary": "Erro API: " + type(e).__name__, "details": str(e), "issues": []}
