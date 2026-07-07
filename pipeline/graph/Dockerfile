# syntax=docker/dockerfile:1
# graph — batch stage: brain-md -> brain-md-graphed (entity nodes + wikilinks). No LLM.
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/sturlese/cortex" \
      org.opencontainers.image.description="cortex graph: entity nodes + wikilinks (deterministic)" \
      org.opencontainers.image.licenses="MIT"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt
COPY src/ ./src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

RUN useradd -r -u 10001 app && mkdir -p /data/brain-md /data/brain-md-graphed && chown -R app /data
USER app

ENTRYPOINT ["python", "-m", "graph.cli"]
