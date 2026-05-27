# Cursor / AI Safety (SwingTradeV2)

These prompts are for **operator safety** when using Cursor (or any coding agent) on this repo.

---

## Non-negotiables (paste at top of any Cursor chat)

```text
Current branch: jarrid-branch.
Do not merge to main/master.
Do not commit or push without approval.
Do not run the trading pipeline unless explicitly requested.
Do not call Anthropic/API unless explicitly requested (including models-list refresh).
Do not touch config/ or data/ unless explicitly requested.
If you need to change behavior, propose first, then implement only after confirmation.
When reporting back, include changed files and a diff summary.
```

---

## “Docs-only” change guardrail

Use this when you want **documentation updates only**.

```text
Task is docs-only.
Allowed changes: README.md and docs/ only.
Do not modify: swingtrade/, prompts/, config/, data/, tests/, pyproject.toml, requirements.txt.
If you detect unrelated untracked files (e.g., __pycache__, data outputs), ignore them and do not add them.
```

---

## Git safety prompts

### Inspect-only (no edits)

```text
Inspect the current repo state and report back findings only.
Do not edit any files.
Do not run network calls.
Include: current branch, git status summary, and relevant file locations.
```

### Show diff before any commit request

```text
Before any commit is created, show:
1) changed file list
2) git diff --stat
3) key diff excerpts (only the important hunks)
Wait for approval before staging/committing.
```

---

## PowerShell command chaining reminder

In Windows PowerShell, prefer `;` not `&&` for chaining:

```powershell
git status -s; git diff --stat; git diff
```

