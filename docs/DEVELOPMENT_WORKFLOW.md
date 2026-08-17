# Development workflow

All repository changes use reviewable branches and Pull Requests.

```text
authorized milestone or maintenance task
                |
                v
      dedicated codex/* branch
                |
                v
 implementation + tests + docs
                |
                v
       commit and branch push
                |
                v
        Pull Request to main
                |
                v
       CI and human review
                |
                v
       corrections on branch
                |
                v
       human-approved merge
```

## Starting a milestone

1. Obtain explicit authorization for exactly one milestone.
2. Update local `main` from `origin/main`.
3. Read `docs/SPEC_V1.md` completely.
4. Inspect the current repository and relevant existing architecture.
5. Create `codex/m<NUMBER>-<description>` from `main`.
6. State the intended file and scope changes before implementation.

## Completing a milestone

1. Confirm no next-milestone behavior was introduced.
2. Run formatter, lint, type-check, tests, and relevant migration checks.
3. Update documentation, ADRs where applicable, and `CHANGELOG.md`.
4. Commit the complete milestone and push its branch.
5. Open a Pull Request to `main` using the repository template.
6. Stop. Do not merge and do not start the next milestone.

Review fixes remain on the same branch and must repeat the relevant checks.
GitHub is the source of truth for PR state, review feedback, and CI results.
