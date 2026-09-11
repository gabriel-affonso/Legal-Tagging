FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=4 ORT_NUM_THREADS=4 PADDLE_NUM_THREADS=4
COPY pyproject.toml requirements.txt ./
COPY src ./src
RUN pip install --no-cache-dir ".[document-ai]" && useradd --create-home appuser
USER appuser
VOLUME ["/app/data/input", "/app/data/output", "/app/data/cache", "/app/models", "/app/logs"]
CMD ["python", "-m", "doc_register", "rent-scan", "--pipeline", "v2", "--config", "/app/config.json"]
