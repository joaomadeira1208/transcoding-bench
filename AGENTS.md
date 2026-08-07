# AGENTS.md

## Agent skills

### Issue tracker

Issues live as GitHub issues in `joaomadeira1208/transcoding-bench`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Commits

Every commit message must follow Conventional Commits 1.0.0: `<type>[optional scope][!]: <description>`.

### Ticket workflow

For every implementation ticket:

1. Start from an up-to-date `master` and create a dedicated `issue-<number>-<slug>` branch.
2. Implement only that ticket, run its required tests and checks, and complete the code review against `master`.
3. Commit the reviewed work, push the branch, and open a pull request targeting `master`.
4. Reference the parent spec and include `Closes #<ticket>` in the pull request body, together with the tests and checks performed.
5. Leave the pull request unmerged for human review and merge approval.
