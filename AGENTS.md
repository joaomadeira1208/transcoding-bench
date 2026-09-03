# AGENTS.md

## Agent skills

### Issue tracker

Issues live as GitHub issues in `joaomadeira1208/transcoding-bench`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Toolchain versions

Before creating a local environment or invoking a pinned tool, check the local version against the repo's pin. Never fall back to whatever the system happens to provide — a system interpreter that merely *works* is the failure mode this rule exists for. The pins are not copied here; they live where they are declared: `.github/workflows/ci.yml` (Python, `pre-commit`, Terraform), `.pre-commit-config.yaml` (hook revisions), `ruff.toml` (`target-version`) and each role's `requirements.txt`/`requirements-dev.txt`. Python is managed with `asdf`; if the pinned version is not installed, install it rather than downgrading the work to what is there.

### Comment convention

Default to none. A file with zero comments is the normal case, and the good one: it means the code says it on its own.

When you want to write one, the first move is to make the code say it — rename the variable, extract the function, give the literal a name. A comment is what is left when that fails, so treat writing one as a small defeat rather than as work delivered.

If what you want to record is *why* — the rejected alternative, the constraint the experimental design imposes — its home is an ADR or a README. A comment repeating it is the copy that goes stale in silence.

Whatever survives that has to name the **concrete wrong edit** someone makes without it. "Deletes `--quiet` and every failed run's `time.json` stops being JSON" is an answer; "the file would be less explained" is not. Being true is not the bar, and neither is being interesting. Moving the same prose into a docstring does not change the answer.

Skip what a linter, a type or a test already guarantees — `hadolint`, `shellcheck`, the pydantic model of the `meta.json`.

Judge one comment at a time, on its own — how many a file has measures nothing. Over-explaining collects in the Dockerfile and the CI config, where every line rests on some external tool's behaviour.

### Commits

Every commit message must follow Conventional Commits 1.0.0: `<type>[optional scope][!]: <description>`.

### Ticket workflow

For every implementation ticket:

1. Start from an up-to-date `master` and create a dedicated `issue-<number>-<slug>` branch.
2. Implement only that ticket, run its required tests and checks, and complete the code review against `master`.
3. Commit the reviewed work, push the branch, and open a pull request targeting `master`.
4. Reference the parent spec and include `Closes #<ticket>` in the pull request body, together with the tests and checks performed.
5. Leave the pull request unmerged for human review and merge approval.
