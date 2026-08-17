# Architecture

Ekonex Voice is a monorepo whose components remain independently deployable.
Milestone M0 contains only the cloud API bootstrap and local infrastructure.

The cloud API uses FastAPI and Pydantic settings sourced from environment
variables. Persistence is async SQLAlchemy targeting PostgreSQL, with Alembic
managing schema changes. Redis is reserved for transient coordination; durable
product configuration will remain in PostgreSQL.

Future components will be isolated under `apps/`, `custom_components/`, and
`packages/`. Alexa mapping, connector transport, persistence, and UI must remain
separate.

## Development topology

```text
client -> cloud API -> PostgreSQL
                    -> Redis
```

`GET /health` is a liveness check and intentionally does not depend on database
or Redis availability. A separate readiness check can be added when the API
starts serving dependency-backed routes.

See [SPEC_V1.md](SPEC_V1.md) for the complete product architecture.

