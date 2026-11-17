---
name: git-conventions
description: >-
  Provides git branch naming and Conventional Commit message standards for the IssueLens repository.
  Use when creating feature/phase branches, staging changes, or crafting commit messages.
---

# IssueLens Git Conventions & Runbook

This skill defines the branch naming and commit message standards for the IssueLens repository, derived from existing commit history and project standards.

## 1. Branch Naming Convention

All feature and phase branches must follow this structure:

```text
feature/phase-<N>-<kebab-case-description>
```

### Examples
- `feature/phase-5-investigation-agent`
- `feature/phase-6-persistence`
- `feature/phase-7-async-worker`

---

## 2. Commit Message Convention

All commit messages must follow the **Conventional Commits** format:

```text
<type>(<scope>): <imperative summary>
# or
<type>: <imperative summary>
```

### Allowed Types
- `feat`: New feature, subsystem, or phase milestone.
- `fix`: Bug fix, error resolution, boundary correction, or retrieval calibration.
- `docs`: Documentation updates (architecture diagrams, LEARNING_GUIDE.md, BUILD_LOG.md, ADRs, README.md).
- `refactor`: Code reorganization without functional or behavioral changes.
- `test`: Adding or adjusting unit, integration, or regression tests.
- `chore`: Tooling, lockfile updates, or general workspace maintenance.

### Optional Scopes
Use the specific subsystem or module in lowercase parentheses:
- `feat(persistence): ...`
- `feat(agent): ...`
- `fix(validator): ...`
- `fix(search): ...`
- `docs(adr): ...`

### Summary Guidelines
- Use the imperative mood ("implement", "add", "fix", "preserve", "calibrate", "demote").
- Do not capitalize the first word unless referring to a proper noun (e.g. `FastAPI`, `LangGraph`, `Phase 6`).
- Do not place a period at the end of the summary.
- For phase completion milestone commits, append `(Phase <N>)` or specify `implement Phase <N> ...`.

### Real Repository Examples
```text
feat(persistence): implement PostgreSQL schema, Alembic migrations, repository pattern, and restart survivability (Phase 6)
fix: preserve agent state across nodes, validate snippet bounds, and record failed runs
docs: add build log entry 016 and update learning guide for validator and interval fixes
fix: expand two-digit version strings to three-digit release tags
fix: deduplicate and merge overlapping snippet intervals in code search
fix: classify tutorial and example directories as documentation to prevent evidence flooding
feat: Implement LangGraph investigation workflow with state machine and execution nodes
docs: add comprehensive system architecture blueprints, ADRs, build specification, and standardized diagrams
```

---

## 3. Windows Sandbox Execution Rules

On Windows environments:
1. Git operations touching `.git/refs/` or `.git/index.lock` (such as `git branch -m`, `git add`, `git commit`) encounter AppContainer permission restrictions in standard sandbox mode and must run with elevated permissions (`BypassSandbox: true`).
2. Read-only git queries (such as `git status`, `git log`, `git diff`) should always run in standard sandbox mode (`BypassSandbox: false`).
