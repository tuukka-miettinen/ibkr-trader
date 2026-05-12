FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY alembic/ alembic/
COPY alembic.ini .
COPY app/ app/

EXPOSE 8000

# Run migrations before starting the server
CMD sh -c 'python -m alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000'
