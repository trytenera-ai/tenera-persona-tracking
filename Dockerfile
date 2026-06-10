FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY app/ ./app/
COPY README.md .
COPY alembic.ini .
COPY alembic/ ./alembic/

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
