# Ekonex Voice

Ekonex Voice is a multi-tenant cloud control layer between Home Assistant and Amazon Alexa.

The project currently includes:

- native Home Assistant custom integration `ekonex_voice`;
- secure pairing and installation identity;
- persistent EVCP WebSocket connection to Ekonex Cloud;
- opt-in entity exposure from Home Assistant;
- inventory and live state synchronization;
- typed Cloud → Home Assistant command execution;
- Alexa Smart Home v3 discovery, state reporting and command routing;
- OAuth account linking and Alexa proactive event reporting.

## Home Assistant installation with HACS

Ekonex Voice can be installed as a custom HACS integration.

1. Open **HACS → Integrations** in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/edmondoalex/e-voice`.
4. Select category **Integration**.
5. Install **Ekonex Voice**.
6. Restart Home Assistant if HACS requests it.
7. Open **Settings → Devices & services → Add integration → Ekonex Voice**.

No YAML configuration and no inbound port on the Home Assistant installation are required.

Entity exposure is opt-in. The installer decides which Home Assistant devices/entities are synchronized to Ekonex Cloud using the Ekonex Voice options UI and/or the dedicated Home Assistant label configured for Ekonex Voice.

## Cloud service

The cloud backend runs with Python 3.13, PostgreSQL and Redis. Docker Compose is provided for deployment.

For local development:

```bash
cp .env.example .env
docker compose up --build
```

The API liveness endpoint is:

```text
GET /health
```

Example response:

```json
{"status":"ok","version":"0.1.0"}
```

## Development

The authenticated multi-installation administration console and its retention operations
are documented in [docs/ADMIN_CONSOLE.md](docs/ADMIN_CONSOLE.md).

Requirements:

- Python 3.13
- Docker with Docker Compose

For development outside Docker:

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
uvicorn apps.cloud_api.app.main:app --reload
```

Quality checks:

```bash
ruff format --check .
ruff check .
mypy apps custom_components
pytest
docker compose config --quiet
```

The repository also runs HACS validation and Home Assistant Hassfest validation in GitHub Actions.

See [docs/SPEC_V1.md](docs/SPEC_V1.md) for the product baseline, [docs/EVCP_V1.md](docs/EVCP_V1.md) for the connector protocol, [docs/ALEXA_SMART_HOME.md](docs/ALEXA_SMART_HOME.md) for Alexa setup, and [docs/adr](docs/adr) for architectural decisions. Completed changes are tracked in [CHANGELOG.md](CHANGELOG.md).
