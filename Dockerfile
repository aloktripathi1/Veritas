FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY . .
RUN pip install -e ".[api,postgres]" \
    && useradd --create-home app && mkdir /data && chown app /data
USER app

# SQLite file in a volume by default; compose points VERITAS_DATABASE_URL at Postgres instead.
ENV VERITAS_HOST=0.0.0.0 VERITAS_PORT=8000 VERITAS_DOCS_ROOT=/app/research_pack \
    VERITAS_DATABASE_URL=sqlite:////data/veritas.db SRV_PROVIDER=anthropic
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["veritas-api"]
