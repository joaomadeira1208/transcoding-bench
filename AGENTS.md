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

A comment earns its place only if it says something the code cannot and if it is extremally important. The code already states *what* it does — restating that duplicates a fact that will drift out of sync. What code cannot state: why this option and not the obvious alternative, what breaks if someone changes it, which failure mode it defends against, and which outside constraint forces it (an ADR, a tool's actual behavior, the experimental design).

### Commits

Every commit message must follow Conventional Commits 1.0.0: `<type>[optional scope][!]: <description>`.

### Ticket workflow

For every implementation ticket:

1. Start from an up-to-date `master` and create a dedicated `issue-<number>-<slug>` branch.
2. Implement only that ticket, run its required tests and checks, and complete the code review against `master`.
3. Commit the reviewed work, push the branch, and open a pull request targeting `master`.
4. Reference the parent spec and include `Closes #<ticket>` in the pull request body, together with the tests and checks performed.
5. Leave the pull request unmerged for human review and merge approval.
