# corpus — local corpus curation, IN a container (pure Python, no network, no docker-in-docker).
# Reads the corpus mounted RO (/data/corpus) and emits JSON artifacts to the workdir (/data/work).
# Deps baked into the image; the code is mounted at /app to iterate (same pattern as clean).
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/sturlese/cortex" \
      org.opencontainers.image.description="cortex corpus: reproducible offline curation CLI" \
      org.opencontainers.image.licenses="MIT"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt
# Runs as root ON PURPOSE: an ad-hoc CLI over host bind mounts, where dropping to a fixed
# uid would fight the host's file ownership. The long-running services all run non-root.
COPY . .
ENV PYTHONPATH=/app/src

ENTRYPOINT ["python", "-m", "corpus.cli"]
