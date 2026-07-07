# Fetcher image: Python + gog (gogcli). DETERMINISTIC, no LLM.
# gog is baked in; ca-certificates STAYS (gog needs it at runtime to reach Google).
# syntax=docker/dockerfile:1
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/sturlese/cortex" \
      org.opencontainers.image.description="cortex fetch: deterministic Google Drive mirror" \
      org.opencontainers.image.licenses="MIT"

# gogcli — Linux amd64 binary, pinned to a validated version.
# Repo github.com/openclaw/gogcli; asset gogcli_<VER>_linux_amd64.tar.gz, binary `gog`.
ARG GOG_VERSION=0.22.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSL "https://github.com/openclaw/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_amd64.tar.gz" \
      | tar -xzO gog > /usr/local/bin/gog \
 && chmod +x /usr/local/bin/gog \
 && apt-get purge -y curl && rm -rf /var/lib/apt/lists/* \
 && gog --version

WORKDIR /app
COPY src/drive_fetch.py ./
ENV PYTHONUNBUFFERED=1

# non-root; /data pre-created so named volumes inherit app ownership on first use
RUN useradd -r -u 10001 app && mkdir -p /data/raw /data/gog && chown -R app /data
USER app

ENTRYPOINT ["python", "/app/drive_fetch.py"]
