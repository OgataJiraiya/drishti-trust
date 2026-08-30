# Team Workflow

## Rule 1: `main` stays stable

All day-to-day development happens on feature branches. Merge into `main` only through reviewed pull requests.

## Assigned branches

- Person 1 — Data Integrity: `feat/data-integrity`
- Person 2 — Model Integrity: `feat/model-integrity`
- Person 3 — Security / Provenance / Backend: `feat/security-backend`
- Person 4 — Distribution Shift + UI: `feat/drift-ui`

## Daily routine

```bash
git checkout <your-branch>
git pull origin <your-branch>
# work
git add .
git commit -m "feat(...): ..."
git push origin <your-branch>
```

When you need the latest stable integration work:

```bash
git fetch origin
git checkout <your-branch>
git merge origin/main
```

Resolve conflicts on your own branch, test, then push.

## Pull-request routine

1. Push feature branch.
2. Open PR: feature branch → `main`.
3. Describe change and test steps.
4. Get at least one teammate review.
5. Fix requested changes.
6. Merge only after tests pass.
7. Everyone fetches/pulls the updated `main` when they next integrate.

## Ownership guidance

Try to keep work isolated:

- Data algorithms under `modules/data_integrity/`
- Model algorithms under `modules/model_integrity/`
- Provenance/security algorithms under `modules/inference_integrity/` plus shared backend APIs where necessary
- Drift algorithms under `modules/distribution_shift/`
- Dashboard under `frontend/`
- Shared contract code only under `shared/` after team agreement

Changes to `shared/`, common backend schemas, or integration contracts should be called out prominently in PRs because they can affect everybody.

## Deadline discipline

By integration phase, modules should emit valid Finding Schema v1 documents even if their internal algorithms are still improving. This lets dashboard/backend integration proceed independently.
