# ---- build the web app ------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- API + engine -----------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ONTOMEM_HOME=/data ONTOMEM_FRONTEND=/app/frontend/dist
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
COPY scripts/ scripts/
COPY results/ results/
RUN pip install --no-cache-dir .
COPY --from=web /web/dist /app/frontend/dist
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "ontomem.backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
