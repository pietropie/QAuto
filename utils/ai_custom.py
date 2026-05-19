"""
ai_custom.py — Analise visual com instrucoes personalizadas do QA Panel
"""
import base64
import anthropic
from pathlib import Path


def _enc(p):
    return base64.standard_b64encode(Path(p).read_bytes()).decode("utf-8")


def analyze_with_custom_instructions(
    screenshot_path,
    page_name,
    instrucao,
    config,
    contexto_extra="",
    multi_check=False,
):
    ai_cfg  = config.get("ai", {})
    api_key = ai_cfg.get("api_key", "")
    model   = ai_cfg.get("model", "claude-opus-4-6")

    if not api_key or api_key.startswith("TODO"):
        return {
            "status":  "WARN",
            "summary": "API key nao configurada",
            "details": "Configure ai.api_key no config.yaml.",
            "issues":  [],
        }

    ctx_line = f"CONTEXTO: {contexto_extra}\n\n" if contexto_extra else ""

    if multi_check:
        prompt = (
            f"Voce e especialista em QA de apps React/Next.js.\n"
            f"Analise o screenshot da pagina '{page_name}'.\n\n"
            f"{ctx_line}"
            f"Verifique CADA item abaixo:\n{instrucao}\n\n"
            f"Responda em JSON puro:\n"
            f'{{"status":"PASS|WARN|FAIL","summary":"1 linha","issues":[{{"severity":"alto|medio|baixo","description":"...","location":"..."}}],"details":"analise completa"}}\n\n'
            f"PASS=tudo OK, WARN=suspeito, FAIL=problema confirmado."
        )
    else:
        prompt = (
            f"Voce e especialista em QA de apps React/Next.js.\n"
            f"Analise o screenshot da pagina '{page_name}'.\n\n"
            f"{ctx_line}"
            f"INSTRUCAO DO TIME DE QA:\n\"{instrucao}\"\n\n"
            f"Verifique especificamente esta instrucao.\n\n"
            f"Responda em JSON puro:\n"
            f'{{"status":"PASS|WARN|FAIL","summary":"o item foi atendido?","issues":[{{"severity":"alto|medio|baixo","description":"...","location":"..."}}],"details":"analise detalhada"}}\n\n'
            f"PASS=atendida, WARN=nao confirmar so pelo visual, FAIL=claramente violada."
        )

    try:
        client  = anthropic.Anthropic(api_key=api_key)
        img_b64 = _enc(screenshot_path)
        ext     = Path(screenshot_path).suffix.lower().replace(".", "")
        media   = f"image/{ext if ext in ('png','jpg','jpeg','gif','webp') else 'png'}"

        msg = client.messages.create(
            model=model,
            max_tokens=1500,
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
            return {
                "status":  r.get("status",  "WARN"),
                "summary": r.get("summary", ""),
                "details": r.get("details", ""),
                "issues":  r.get("issues",  []),
            }
        return {"status": "WARN", "summary": "Formato inesperado", "details": raw, "issues": []}

    except Exception as e:
        return {"status": "WARN", "summary": f"Erro API: {type(e).__name__}", "details": str(e), "issues": []}
