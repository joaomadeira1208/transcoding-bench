# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in. Start from **`docs/adr/INDEX.md`**, which lists every ADR with a one-line summary; use it to pick which ones are relevant instead of opening all of them.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo:

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── INDEX.md
│   ├── 0001-instance-types.md
│   └── 0002-codec-encoder-configuration.md
└── ...
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

`CONTEXT.md` is written in Portuguese, and each term lists its canonical English identifier in backticks. Use the **English** form in code, identifiers, commits, logs, issue titles and test names; the Portuguese term belongs to the article (`.tex`) and the glossary itself.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (raw data schema) — but worth reopening because…_

The ADRs and `CONTEXT.md` outrank the article (`.tex`): the article is in continuous development and may carry old or still-unrevised decisions, while the ADRs record the decided and justified state of the project. On inconsistency, adjust the article — never the ADR.
