# CLAUDE.md — genblaze-gen-media-multi-provider-sample

Follow [AGENTS.md](AGENTS.md) at all times — it is the single source of truth.

## Doc Read Order

1. `AGENTS.md`
2. `ARCHITECTURE.md`
3. `docs/features/<feature>.md` (if applicable)
4. `docs/app-workflows.md`

## Plans

Create new exec plans in `docs/exec-plans/active/`; move to
`docs/exec-plans/completed/` after validation.

## Test Commands

- Backend: `cd services/api && uv run pytest tests/ -x`
- Frontend typecheck: `cd apps/web && pnpm typecheck`
- Structural-only (fast): `uv run pytest tests/test_structure.py`

## Diff Discipline

- Update docs in the same PR as code changes
- Only change files relevant to the task
- Never add `boto3` (see AGENTS Rule 1)
- Never wrap Genblaze response types (see AGENTS Rule 2)
