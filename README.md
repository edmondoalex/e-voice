# Ekonex Voice

Ekonex Voice is the planned multi-tenant cloud control layer between Amazon
Alexa and Home Assistant. The repository currently contains Milestone M3: the
core multi-tenant backend, secure pairing and native HA Connector foundation.
Alexa is not implemented. Milestone M3 adds the native Home Assistant Connector
foundation without entity synchronization or command handling.

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
mypy apps custom_components
pytest
docker compose config --quiet
```

The test suite also applies all Alembic migrations to a temporary database and
checks that the resulting schema matches the SQLAlchemy metadata.

See [docs/SPEC_V1.md](docs/SPEC_V1.md) for the product baseline and
[docs/adr](docs/adr) for architectural decisions. Completed changes are tracked
in [CHANGELOG.md](CHANGELOG.md).

## Home Assistant Connector foundation

M3 adds `custom_components/ekonex_voice` for HAOS. It is configured only from
**Settings → Devices & services → Add integration** and requires no YAML,
inbound port, add-on, or connection back into Home Assistant's local WebSocket.

The foundation implements secure M2 pairing, ConfigEntry lifecycle,
reauthentication, connection supervision and redacted diagnostics. Entity
inventory, EVCP WebSocket messages, commands and Alexa remain deferred. See
[docs/home-assistant.md](docs/home-assistant.md) for scope and HAOS acceptance.
