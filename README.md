# Ekonex Voice

Ekonex Voice is the planned multi-tenant cloud control layer between Amazon
Alexa and Home Assistant. The repository currently contains only Milestone M0:
the service and development bootstrap.

## Requirements

- Python 3.13
- Docker with Docker Compose

## Run locally

Create the local environment file and start the stack:

```bash
cp .env.example .env
docker compose up --build
```

The API is available at `http://localhost:8000`. Its liveness endpoint returns:

```json
{"status":"ok","version":"0.1.0"}
```

For development outside Docker:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
uvicorn apps.cloud_api.app.main:app --reload
```

## Quality checks

```bash
ruff format --check .
ruff check .
mypy apps
pytest
docker compose config --quiet
```

See [docs/SPEC_V1.md](docs/SPEC_V1.md) for the product baseline and
[docs/adr](docs/adr) for architectural decisions.

