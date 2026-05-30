FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    npm_config_cache=/tmp/npm-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates nodejs npm \
    && npm install -g @arizeai/phoenix-mcp@4.0.13 \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-production.txt /app/requirements-production.txt
RUN pip install --no-cache-dir -r /app/requirements-production.txt
COPY . /app

RUN useradd --create-home --shell /usr/sbin/nologin traceguard \
    && chown -R traceguard:traceguard /app /tmp

USER traceguard
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()" || exit 1

CMD ["python", "-m", "traceguard.server", "--host", "0.0.0.0", "--port", "8080"]
