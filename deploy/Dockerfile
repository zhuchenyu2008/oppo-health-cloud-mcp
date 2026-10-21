FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home appuser

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY cloud_mcp.py oppo_sign.py ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /app/data /app/backups \
    && chmod 700 /app/data /app/backups \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
CMD ["uvicorn", "oppo_health.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]