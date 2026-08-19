# Pairing security

Milestone M2 implements provider-neutral pairing between an HAOS/Home Assistant
OS installation and an Ekonex tenant. It does not contain Home Assistant runtime
code, Alexa behavior, or provider-specific identity.

## Values and storage

The connector receives two temporary values when it creates a session:

- a human code such as `ABCD-1234`, valid for ten minutes by default;
- a high-entropy polling secret, used only by that connector session.

The database stores neither value. Human codes are normalized and HMAC-SHA256
hashed with a server-side pepper; polling secrets are SHA-256 hashed because they
already contain high entropy. The code alone cannot poll or authenticate a
connector.

At claim time, an authenticated active membership establishes the tenant
context. The service locks the pairing row, validates expiry and one-time state,
creates the tenant-owned installation, and generates a new high-entropy Connector
credential. Only its SHA-256 hash is stored as the durable credential.

The initial Connector credential is held temporarily in a Fernet envelope under
a server-managed delivery key. A correct polling secret can retrieve it once;
the envelope is deleted and committed before the plaintext is returned. The
human pairing code is never reused or transformed into the credential.

## Pairing portal

The installer opens `https://voice.e-control.tech/pair`, signs in with an Ekonex
portal account, enters the `XXXX-XXXX` code and chooses an installation name.
The same tenant-scoped claim is also available to the authenticated portal as
`POST /connector/v1/pairing/claims`. Neither response contains the pairing
session, polling secret, Connector credential or any other secret. Home
Assistant remains the only recipient of the Connector credential through its
existing authenticated polling request, and receives it once.

Passwords are Argon2 hashes. Successful login creates a random opaque session;
only its SHA-256 digest is stored in PostgreSQL. The browser receives the token
in an HttpOnly, Secure (production), SameSite Strict cookie. Sessions expire,
can be revoked with `POST /logout`, and never travel in URLs. Failed logins are
rate-limited per normalized email without persisting the email itself.

Users with one active membership enter that tenant automatically. Users with
several memberships must select one; the submitted identifier is accepted only
after the existing `AuthenticationService` verifies membership and status.
Tenant roles remain authoritative and read-only support accounts cannot claim.

Browser submissions use a signed, tenant/user-bound, 30-minute CSRF token in a
SameSite Strict HttpOnly cookie. Production must set a high-entropy value for
`EKONEX_PAIRING_PORTAL_CSRF_SECRET` in the deployment secret manager.

## First account bootstrap

Apply migrations, then run this command once from an interactive shell in the
API runtime (replace names and email with production values):

```console
alembic upgrade head
python -m apps.cloud_api.app.bootstrap_portal_user \
  --email installer@example.com \
  --dealer-name "Ekonex" --dealer-slug ekonex \
  --tenant-name "Cliente iniziale" --tenant-slug cliente-iniziale \
  --role owner
```

The command prompts twice for a password of at least 12 characters, never takes
it on the command line, refuses to run when any user already exists, and creates
the initial dealer, tenant and verified membership atomically. There is no
default production credential. Subsequent user administration is not yet a web
feature and must use an audited operational process until a dedicated admin UI
is implemented.

## Abuse prevention

- Failed claims are persisted per authenticated user in a rolling time window.
- The default limit is five failures in fifteen minutes.
- Invalid, replayed, expired, and rate-limited claims are audited without codes,
  hashes, polling secrets, credential values, or sensitive payloads.
- Row locking and one-way status transitions prevent concurrent double claims.
- A claimed session cannot be replayed into another tenant.
- Tenant membership is resolved before claim, and credential lifecycle queries
  include tenant scope in SQL.

## Credential lifecycle

Connector credentials can be revoked. Rotation revokes every currently active
credential for the installation and returns one newly generated secret while
recording the previous credential identifier. Secrets are returned only at issue
time and are never recoverable from their stored hashes.

The application must supply the code pepper and Fernet delivery key through its
secret-management configuration when the pairing service is wired to transport
endpoints in a later milestone. They must not be logged or committed.
