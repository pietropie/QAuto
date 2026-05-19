# ── QA Automation — Dockerfile ──────────────────────────────
# Imagem base com Python + Playwright (Chromium headless)
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codigo da aplicacao
COPY . .

# Cria diretorios de saida
RUN mkdir -p reports screenshots

# Porta da API
EXPOSE 8000

# Comando padrao: inicia a API (override no docker-compose para o worker)
CMD ["python", "api.py"]
