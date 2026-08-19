FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY apps ./apps
COPY brand ./brand
COPY migrations ./migrations
COPY alembic.ini ./

RUN python -m pip install --upgrade pip && python -m pip install .
RUN useradd --create-home --uid 10001 ekonex

USER ekonex
EXPOSE 8000

CMD ["uvicorn", "apps.cloud_api.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
