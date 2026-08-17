# Ekonex Voice repository instructions

These rules apply to the entire repository and to every coding agent working in
it.

## Source of truth

- Read `docs/SPEC_V1.md` in full before starting each milestone.
- Treat the specification as the primary architectural and security baseline.
- Inspect the repository and current branch before modifying files.
- Follow the milestone prompt embedded in the specification exactly.

## Branch and Pull Request workflow

- Never implement directly on `main`.
- Start each milestone from an up-to-date `main` on a dedicated branch named
  `codex/m<NUMBER>-<short-description>`.
- Use a separate `codex/<short-description>` branch for non-milestone work.
- Keep one milestone or one coherent maintenance change per branch and PR.
- Commit intentionally, push the branch, and open a Pull Request targeting
  `main`.
- Never merge, squash, rebase-merge, approve, or close a PR autonomously.
- Treat the PR and its checks as the source of truth for review and corrections.
- Address review feedback on the same branch and push follow-up commits.

## Milestone boundaries

- Implement only the milestone explicitly authorized by the user.
- Do not begin, scaffold, or partially implement the next milestone.
- Stop after validation, documentation, commit, push, and PR creation.
- A merged milestone does not authorize starting the next milestone.
- New milestone work requires explicit user authorization.

## Validation and documentation

- Add tests with every implementation.
- Run Ruff formatting and lint, mypy, pytest, and relevant migration or service
  checks before opening or updating a PR.
- Fix failures before publishing unless an external blocker is documented.
- Update `CHANGELOG.md` for every completed implementation or repository-level
  change, including validation and deviations from `docs/SPEC_V1.md`.
- Update architecture documentation and ADRs when architectural decisions change.
- Report the exact branch, commit, checks, PR target, and known deviations.

## Security and repository safety

- Never commit credentials, tokens, production secrets, or sensitive logs.
- Preserve strict tenant isolation: tenant-owned repository/service methods must
  scope records in their database query.
- Never introduce arbitrary remote Home Assistant service execution.
- Preserve unrelated user changes and stage only files belonging to the current
  PR.
