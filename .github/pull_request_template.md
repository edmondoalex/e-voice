## Summary

- Describe the change.

## Milestone and scope

- Authorized milestone or maintenance task:
- Specification section/prompt:
- Explicitly excluded work:

## Impact

- User/developer impact:
- Root cause, when this is a fix:

## Validation

- [ ] `ruff format --check .`
- [ ] `ruff check .`
- [ ] `mypy apps`
- [ ] `pytest`
- [ ] `docker compose config --quiet`
- [ ] Relevant migration checks passed or are not applicable

## Security and scope

- [ ] No secrets were committed
- [ ] Tenant-owned access is tenant-scoped
- [ ] No arbitrary Home Assistant service execution was introduced
- [ ] Architectural changes include an ADR
- [ ] No work from the next milestone is included
- [ ] `CHANGELOG.md` and relevant documentation are updated

## Deviations and follow-up

- Deviations from `docs/SPEC_V1.md`:
- Known limitations or follow-up (must not start the next milestone):

## Agent handoff

- [ ] Branch was pushed and this PR targets `main`
- [ ] I will not merge, approve, close, or start the next milestone autonomously
