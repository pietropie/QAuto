"""
reporter.py — Gerador de relatório HTML com resultados dos testes
"""

from pathlib import Path
from datetime import datetime
import json
import webbrowser


TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QA Report — {{ app_url }}</title>
<style>
  :root {
    --pass: #16a34a; --fail: #dc2626; --warn: #d97706;
    --bg: #f8fafc; --card: #fff; --border: #e2e8f0;
    --text: #1e293b; --muted: #64748b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); }
  header { background: #1e293b; color: #fff; padding: 24px 40px; }
  header h1 { font-size: 1.5rem; margin-bottom: 4px; }
  header p  { color: #94a3b8; font-size: 0.9rem; }
  .summary  { display: flex; gap: 16px; padding: 24px 40px; flex-wrap: wrap; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
          padding: 16px 24px; flex: 1; min-width: 140px; }
  .stat .num { font-size: 2rem; font-weight: 700; }
  .stat .lbl { color: var(--muted); font-size: 0.85rem; margin-top: 2px; }
  .pass .num { color: var(--pass); }
  .fail .num { color: var(--fail); }
  .warn .num { color: var(--warn); }
  main  { padding: 0 40px 40px; }
  .suite { margin-bottom: 32px; }
  .suite h2 { font-size: 1.1rem; border-bottom: 2px solid var(--border);
               padding-bottom: 8px; margin-bottom: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; font-size: 0.8rem; }
  .test-card { background: var(--card); border: 1px solid var(--border);
                border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
  .test-header { display: flex; align-items: center; gap: 12px; padding: 14px 18px; cursor: pointer; }
  .badge { font-size: 0.75rem; font-weight: 700; padding: 2px 8px; border-radius: 4px; min-width: 60px; text-align: center; }
  .PASS  { background: #dcfce7; color: var(--pass); }
  .FAIL  { background: #fee2e2; color: var(--fail); }
  .WARN  { background: #fef3c7; color: var(--warn); }
  .test-name { font-weight: 500; flex: 1; }
  .test-time { color: var(--muted); font-size: 0.8rem; }
  .test-body { border-top: 1px solid var(--border); padding: 14px 18px; display: none; }
  .test-body.open { display: block; }
  .test-body pre { background: #f1f5f9; border-radius: 6px; padding: 12px; font-size: 0.82rem;
                   overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  .screenshot { margin-top: 12px; }
  .screenshot img { max-width: 100%; border-radius: 6px; border: 1px solid var(--border); cursor: zoom-in; }
  .ai-analysis { margin-top: 12px; background: #f0f9ff; border: 1px solid #bae6fd;
                  border-radius: 6px; padding: 12px; font-size: 0.88rem; line-height: 1.6; }
  .ai-analysis strong { color: #0369a1; }
  footer { text-align: center; padding: 24px; color: var(--muted); font-size: 0.82rem; }
  .lightbox { display:none; position:fixed; inset:0; background:rgba(0,0,0,.85);
              z-index:999; align-items:center; justify-content:center; }
  .lightbox.open { display:flex; }
  .lightbox img { max-width:92vw; max-height:92vh; border-radius:8px; }
  .lightbox-close { position:fixed; top:16px; right:24px; color:#fff; font-size:2rem; cursor:pointer; }
</style>
</head>
<body>
<header>
  <h1>🔍 QA Automation Report</h1>
  <p>{{ app_url }} &nbsp;|&nbsp; {{ run_date }} &nbsp;|&nbsp; Duração: {{ duration }}s</p>
</header>

<div class="summary">
  <div class="stat pass"><div class="num">{{ total_pass }}</div><div class="lbl">Passou</div></div>
  <div class="stat fail"><div class="num">{{ total_fail }}</div><div class="lbl">Falhou</div></div>
  <div class="stat warn"><div class="num">{{ total_warn }}</div><div class="lbl">Avisos IA</div></div>
  <div class="stat"><div class="num">{{ total_tests }}</div><div class="lbl">Total de testes</div></div>
</div>

<main>
{% for suite in suites %}
<div class="suite">
  <h2>{{ suite.name }}</h2>
  {% for test in suite.tests %}
  <div class="test-card">
    <div class="test-header" onclick="toggle(this)">
      <span class="badge {{ test.status }}">{{ test.status }}</span>
      <span class="test-name">{{ test.name }}</span>
      <span class="test-time">{{ test.duration }}s</span>
    </div>
    <div class="test-body">
      {% if test.message %}
      <pre>{{ test.message }}</pre>
      {% endif %}
      {% if test.ai_analysis %}
      <div class="ai-analysis"><strong>🤖 Análise da IA:</strong><br>{{ test.ai_analysis }}</div>
      {% endif %}
      {% if test.screenshot %}
      <div class="screenshot">
        <img src="{{ test.screenshot }}" onclick="openLightbox(this)" title="Clique para ampliar">
      </div>
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>
{% endfor %}
</main>

<footer>Gerado por QA Automation Framework &nbsp;·&nbsp; {{ run_date }}</footer>

<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <span class="lightbox-close" onclick="closeLightbox()">✕</span>
  <img id="lightbox-img" src="">
</div>

<script>
function toggle(header) {
  const body = header.nextElementSibling;
  body.classList.toggle('open');
}
function openLightbox(img) {
  event.stopPropagation();
  document.getElementById('lightbox-img').src = img.src;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
}
// Auto-abre cards com falha
document.querySelectorAll('.FAIL').forEach(b => {
  b.closest('.test-card').querySelector('.test-body').classList.add('open');
});
</script>
</body>
</html>"""


class Reporter:
    def __init__(self, config: dict):
        self.config      = config
        self.suites      = []
        self.start_time  = datetime.now()
        self.output_dir  = Path(config.get("report", {}).get("output_dir", "./reports"))
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def add_suite(self, name: str):
        suite = {"name": name, "tests": []}
        self.suites.append(suite)
        return suite

    def add_result(self, suite: dict, name: str, status: str,
                   message: str = "", screenshot: str = "",
                   ai_analysis: str = "", duration: float = 0.0):
        """
        status: "PASS" | "FAIL" | "WARN"
        """
        # Torna o caminho do screenshot relativo ao HTML para portabilidade
        if screenshot:
            try:
                screenshot = str(Path(screenshot).resolve().relative_to(
                    self.output_dir.resolve().parent
                ))
            except ValueError:
                pass  # mantém absoluto se não conseguir relativizar

        suite["tests"].append({
            "name":        name,
            "status":      status,
            "message":     message,
            "screenshot":  screenshot,
            "ai_analysis": ai_analysis,
            "duration":    round(duration, 2),
        })

        # Log colorido no terminal
        icons = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️ "}
        print(f"  {icons.get(status, '?')} [{status}] {name}" +
              (f"\n     {message}" if message and status != "PASS" else ""))

    def save(self) -> str:
        """Renderiza o HTML e salva. Retorna o caminho do arquivo."""
        duration  = round((datetime.now() - self.start_time).total_seconds(), 1)
        all_tests = [t for s in self.suites for t in s["tests"]]

        html = TEMPLATE
        replacements = {
            "{{ app_url }}":     self.config["app"]["base_url"],
            "{{ run_date }}":    self.start_time.strftime("%d/%m/%Y %H:%M"),
            "{{ duration }}":    str(duration),
            "{{ total_pass }}":  str(sum(1 for t in all_tests if t["status"] == "PASS")),
            "{{ total_fail }}":  str(sum(1 for t in all_tests if t["status"] == "FAIL")),
            "{{ total_warn }}":  str(sum(1 for t in all_tests if t["status"] == "WARN")),
            "{{ total_tests }}": str(len(all_tests)),
        }
        for k, v in replacements.items():
            html = html.replace(k, v)

        # Renderiza suites/testes
        suites_html = ""
        for suite in self.suites:
            tests_html = ""
            for t in suite["tests"]:
                screenshot_tag = (
                    f'<div class="screenshot"><img src="{t["screenshot"]}" '
                    f'onclick="openLightbox(this)" title="Clique para ampliar"></div>'
                    if t["screenshot"] else ""
                )
                ai_tag = (
                    f'<div class="ai-analysis"><strong>🤖 Análise da IA:</strong><br>'
                    f'{t["ai_analysis"]}</div>'
                    if t["ai_analysis"] else ""
                )
                msg_tag = f"<pre>{t['message']}</pre>" if t["message"] else ""
                open_cls = " open" if t["status"] == "FAIL" else ""
                tests_html += f"""
  <div class="test-card">
    <div class="test-header" onclick="toggle(this)">
      <span class="badge {t['status']}">{t['status']}</span>
      <span class="test-name">{t['name']}</span>
      <span class="test-time">{t['duration']}s</span>
    </div>
    <div class="test-body{open_cls}">
      {msg_tag}{ai_tag}{screenshot_tag}
    </div>
  </div>"""

            suites_html += f"""
<div class="suite">
  <h2>{suite['name']}</h2>
  {tests_html}
</div>"""

        html = html.replace("{% for suite in suites %}", "").replace("{% endfor %}", "")
        # Remove os blocos Jinja originais e insere o HTML gerado
        import re
        html = re.sub(r'<div class="suite">.*</div>\s*<footer', suites_html + "\n<footer",
                      html, flags=re.DOTALL)

        output_path = self.output_dir / f"report_{self.start_time.strftime('%Y%m%d_%H%M%S')}.html"
        output_path.write_text(html, encoding="utf-8")
        print(f"\n📄 Relatório salvo em: {output_path}")

        if self.config.get("report", {}).get("open_after_run", True):
            webbrowser.open(str(output_path))

        return str(output_path)
