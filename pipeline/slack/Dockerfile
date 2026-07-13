# slackexport — offline bootstrap CLI (like corpus): export ZIP -> clean-compatible raw dir.
FROM python:3.12-slim
WORKDIR /app
COPY src/ /app/src/
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "slackexport.sync"]
