FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic pydantic-settings sqlalchemy psycopg2-binary "httpx>=0.27.0"

COPY app ./app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

