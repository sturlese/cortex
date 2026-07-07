# syntax=docker/dockerfile:1
# clean — agentic worker (`python -m clean.main`) + supervisor (`python -m clean.ops`).
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/sturlese/cortex" \
      org.opencontainers.image.description="cortex clean: agentic ingestion worker + ops supervisor" \
      org.opencontainers.image.licenses="MIT"

# uv: fast, reproducible installs (reads plain requirements.txt)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# poppler-utils = pdftotext (deterministic PDF converter)
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt
COPY src/ ./src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

# non-root; /data pre-created so named volumes inherit app ownership on first use
RUN useradd -r -u 10001 app && mkdir -p /data/raw /data/brain-md /data/state && chown -R app /data
USER app

CMD ["python", "-m", "clean.main"]
