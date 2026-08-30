# Contributing to DRISHTI-TRUST

## Branch policy

- `main` is the stable integration branch.
- Do not develop directly on `main`.
- Use your assigned feature branch:
  - `feat/data-integrity`
  - `feat/model-integrity`
  - `feat/security-backend`
  - `feat/drift-ui`

## Before you start

```bash
git clone https://github.com/OgataJiraiya/drishti-trust.git
cd drishti-trust
git fetch --all
```

Then checkout your branch, for example:

```bash
git checkout feat/data-integrity
```

Before each work session:

```bash
git pull origin feat/data-integrity
```

## Commits

Prefer small, focused commits.

Examples:

```text
feat(data): add perceptual-hash duplicate detector
feat(model): add behavioural reference battery
feat(security): add replay detection tests
feat(ui): add findings dashboard
fix(api): preserve Finding Schema v1 evidence order
test(integration): validate four-module finding ingestion
```

## Pull requests

Open a PR from your feature branch into `main` when a coherent unit of work is ready.

Every PR should explain:

- what changed
- why it changed
- how to test it
- whether Finding Schema v1 is affected
- limitations / unfinished work

Do not merge a PR that breaks the shared schema or existing tests.

## Integration contract

The cross-module Finding Schema v1 is frozen. Do not rename, remove, or change the types of its outer fields without a team decision.

The exact contract lives in:

`docs/INTEGRATION_CONTRACT.md`

## Security rules

Never commit:

- private keys
- `.env` files
- API tokens
- generated SQLite databases
- large datasets
- model checkpoints / weights unless explicitly approved
- local virtual environments
- IDE caches
- temporary outputs

Never use `eval`, `exec`, or unsafe model deserialization on untrusted artifacts.

## Merge rule

Before merging into `main`:

1. Pull latest `main` into your branch.
2. Resolve conflicts locally.
3. Run your tests.
4. Run shared/integration tests if your change touches common code.
5. Push the resolved branch.
6. Ask another teammate to review the PR.
