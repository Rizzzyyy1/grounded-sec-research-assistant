# syntax=docker/dockerfile:1.7
# One file, two deployable images:  --target api   and   --target ui
# Build from the repo root:  docker build -f docker/Dockerfile --target api -t finsight-api .

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# Never run as root inside the container.
RUN useradd --create-home --uid 10001 finsight
COPY pyproject.toml README.md ./
COPY src ./src

# ------------------------------------------------------------------ API
FROM base AS api
RUN pip install ".[data,ml,llm,api]"
COPY configs ./configs
RUN mkdir -p data reports && chown -R finsight:finsight /app
USER finsight
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "finsight.api.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# ------------------------------------------------------------------ UI
FROM base AS ui
RUN pip install ".[ui]"
RUN chown -R finsight:finsight /app
USER finsight
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"
CMD ["streamlit", "run", "src/finsight/ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
