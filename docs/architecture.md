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

## Tenant isolation

Tenant is the authorization and data isolation boundary. Authentication resolves
an active user membership into a tenant context. Domain services accept that
context, and repositories require `tenant_id` on every tenant-owned lookup.

Resources that inherit ownership indirectly are scoped in the database query:
entities join their installation, and Alexa publications join their entity and
installation. A foreign resource therefore produces the same not-found result as
an absent resource; callers never receive an unscoped object to validate later.

M1 established persistence and authorization scaffolding without session
endpoints, Alexa directives, pairing, or connector transport.

## Pairing boundary

M2 adds a provider-neutral pairing service above the tenant-scoped core. Pairing
creates an HAOS installation only after an active membership selects the tenant.
Temporary human codes, polling authorization, and durable Connector credentials
remain separate security values with independent hashes and lifecycles.

The one-time Connector credential is encrypted only for deferred delivery and is
removed after the first authenticated poll. Credential revocation and rotation
remain tenant-scoped. See [pairing.md](pairing.md) for the threat controls and
storage rules.

See [SPEC_V1.md](SPEC_V1.md) for the complete product architecture.
