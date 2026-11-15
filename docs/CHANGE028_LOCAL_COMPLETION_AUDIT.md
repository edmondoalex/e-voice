# CHANGE028 local completion audit

Date: 2026-11-15 (Europe/Rome)

## Outcome

The isolated local candidate implements an e-Control launch whose effective
authority is restricted server-side to one e-Voice installation. The existing
global portal remains separate. No live account, installation, cloud adapter or
hardware command was used.

## Requirement evidence

- Signed launch: HMAC verification, exact issuer/audience, issued/expiry window,
  one-use hashed `jti`, synthetic contract fixture.
- Canonical mapping: unique issuer/external installation binding, tenant match,
  active binding and assignment required, ambiguous/cross-tenant mappings fail closed.
- Bounded session: opaque token stored only as SHA-256, secure restricted cookie,
  `effective_installation_id`, short expiry, logout and revocation.
- Effective permissions: intersection of requested allowlist, tenant role and
  assignment level. A readonly support role cannot be amplified by `manage`.
- Continuous revocation: account, membership, binding, assignment and session
  are revalidated; an open SSE stream stops before its next item after revocation.
- Indirect objects: installation, entity, Alexa publication, routines, speaker/media
  groups, execution history, entity history, operational diagnostics and audit rows
  are constrained by tenant plus effective installation.
- Mutations: command capability and installation equality are checked before the
  dispatcher. Synthetic A/B counters prove side effect B remains zero.
- HTTP surfaces: bounded context/logout, entity/routine lists, JSON/CSV export,
  SSE stream and typed commands. Denials are uniform and disclose no B metadata.
- Tampering: path, query, body, header and form attempts cannot change the effective
  installation; no bounded websocket route exists; SSE is installation-scoped.
- Global-session isolation: a concurrent global portal cookie does not amplify the
  bounded cookie or change A/B results.
- Audit/privacy: launch and command audits carry correlation ID, hashed jti and
  redacted command metadata; launch tokens and secrets are absent from logs/audit.
- Compatibility: central portal behavior is unchanged; full cloud backend suite passes.

## Verification

- Focused bounded core and HTTP: 25 tests pass (latest focused runs: 23 + 7,
  overlapping by design).
- Full backend regression: 391 passed, 0 failed, 0 errors, 16 pre-existing
  deprecation warnings, 83.45 seconds.
- Python compileall: pass.
- Alembic upgrade/model metadata parity: covered by the full suite.

## Candidate manifest

```text
005FDCB734CB144AD82188C1EE6CFFADCE1BFBD9A30A40643E7D3286CB37F1AB apps/cloud_api/app/domain/models.py
2C9CAF145BC314ADADFFF39CE5A0A007CD493FA08F0798E60F32DA1619164702 apps/cloud_api/app/config.py
A25AF12FFA385FD2F4A08439A4DA00490C614223446DA225E439BBFF52D3EA47 apps/cloud_api/app/installation_bound.py
80DE6E81F613D5457E57DBBF4D9E2DE88A6839CA2872C91B7A034D680C44B821 apps/cloud_api/app/installation_bound_api.py
5504E7F6E6CD82BEDAA49FCC39417E9AF0E6583FB28F728A4568B7396F902C1F apps/cloud_api/app/main.py
F52AD4C0A650AA87C43FC89DC7DA35AEB61432DCA0942559F2C16178FA969093 apps/cloud_api/app/alexa_events.py
839C5D6622B9F0E2595236D954B33929E8EEA0DCE880B4E1E6A7479DF1B65B75 apps/cloud_api/tests/test_installation_bound.py
DB2B22BFFB764BDF3A7C6D440274496D8C4CBF2D4530B23656AE593C05EAE610 apps/cloud_api/tests/test_installation_bound_api.py
32503EF3693DBEFBC4DC93926CD72E1D818E5F23DAFEBBC4B65196BB55B5FBDB apps/cloud_api/tests/test_admin_console.py
C0D25CBE784DA683FB14960E54634A6DBEA76BEA860C5B1E7733A356B8E4EBA6 apps/cloud_api/tests/test_models.py
8035AF6FC32E2A97C347CFBAE062A044755ECC788B9DF6736BF557C8D467833E migrations/versions/20261115_0024_installation_bound_sessions.py
BE8ACCBCC23103636067386B3F893B3A0DA5AC72347FD7CF03D4507027134FB3 docs/contracts/change028_econtrol_launch_fixture.json
```

## Gate and residual risk

Local implementation and synthetic verification are complete. The original
`e-Voice` working tree remains intentionally untouched because it contains
unattributed overlapping work. Integration/review into that dirty tree is a
separate non-destructive handoff step. Commit, push, release, deployment,
database migration on a live service and real e-Control/e-Voice acceptance remain
outside this gate and require explicit authorization.
