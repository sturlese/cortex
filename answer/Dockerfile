# answer — the MCP answer server over brain-md + brain-facts (both mounted read-only).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

# stdio by default; the compose service overrides with --transport http
ENTRYPOINT ["python", "-m", "answer.mcp_server"]
