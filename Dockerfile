# OperAItor: one image with the built React console and the Python backend.
#   docker build -t operaitor .
#   docker run -p 7860:7860 operaitor                       # hosted mode: visitors bring their own key
#   docker run -p 7860:7860 -e PLANT_HOSTED=0 -e OPENAI_API_KEY=sk-... operaitor   # private: server key

# ---- console (React + Vite) -------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend -------------------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLANT_HOSTED=1 PLANT_CAMPAIGN_DIR= PLANT_MAX_SESSIONS=4 PORT=7860
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
# Hugging Face Spaces runs containers as uid 1000.
RUN useradd -m -u 1000 app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY operaitor ./operaitor
COPY --from=web /web/dist ./operaitor/server/web
RUN pip install ".[llm,admit]" && chown -R app /app
USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/api/health')"
CMD ["sh", "-c", "operaitor serve --host 0.0.0.0 --port ${PORT}"]
