# Contributing to Vyasa Agent

Thank you for helping the 29-partner fleet grow. This file is the short version;
the full constitution lives in [`CLAUDE.md`](CLAUDE.md).

## Ground rules

1. **Conventional Commits.** Every subject starts with `feat:`, `fix:`,
   `chore:`, `docs:`, `refactor:`, `test:`, `ci:`, or `build:`. Scope in
   parens is encouraged: `fix(gateway): drop unknown-chat updates`.
2. **DCO signoff.** Sign every commit with `git commit -s`. The
   `Signed-off-by:` trailer certifies that you wrote the patch or have the
   right to submit it under Apache-2.0.
3. **Squash-merge only.** `main` is protected; required status checks must
   be green before merge.
4. **No vendor leaks.** Do not introduce the strings listed in
   `scripts/white-label-check.sh`. CI runs that script on every PR.

## Local checks before you push

```bash
# White-label gate (must pass with zero hits)
bash scripts/white-label-check.sh

# Tests (must be green)
uv run pytest

# Lint
uv run ruff check .
uv run ruff format --check .
```

If any of the above fails, fix it before opening a PR. Reviewers do not
debug red pipelines for you.

## Branch and PR flow

1. Branch off `main` with a descriptive name: `feat/telegram-allowlist`,
   `fix/graph-migrate-0-to-1`, `docs/install-linux`.
2. Keep PRs under 400 lines of diff. Larger changes: open an issue first
   and agree a phasing plan with the Managing Partner.
3. Fill in the PR template. Every PR declares:
   - The problem it solves (one paragraph).
   - The test evidence (command output or screenshot).
   - The risk / rollback plan.
4. Request review from the partner whose domain you are touching (see
   [`docs/roster.md`](docs/roster.md) for the map).

## Quality gate

Every partner's output, human or machine, must clear:

- `confidence_score >= 0.80`
- a `verification_step` that a reviewer can re-run
- a one-paragraph `summary` in the PR body

Blocking partners (Kavach, Varuna, Mitra, Indra, Dr. Reddy) have veto
power. A single CRITICAL finding halts the merge.

## Reporting security issues

Do not open a public issue for a security finding. Email
`security@graymatteronline.com` with the details. Dr. Reddy triages within
one business day.

## PR template

The template lives at `.github/PULL_REQUEST_TEMPLATE.md`. Fill every
section; empty sections are a rejection.

## Code of conduct

Be direct, be specific, and be kind. Disagree with ideas, not people. The
Managing Partner will close threads that go personal.
