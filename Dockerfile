FROM python:3.11

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       curl \
       build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

COPY pyproject.toml ./
RUN poetry lock
COPY poetry.lock ./
RUN poetry install --no-interaction --no-ansi --only main --no-root

COPY . .

EXPOSE 8000

CMD sh -c "uvicorn app.app:app --host 0.0.0.0 --port 8000"
