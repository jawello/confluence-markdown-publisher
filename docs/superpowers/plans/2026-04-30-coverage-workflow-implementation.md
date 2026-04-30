# Coverage Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an informational local coverage workflow with a documented `90%` target.

**Architecture:** Use `pytest-cov` as a dev dependency so coverage runs through the existing pytest workflow. Keep the check informational by omitting `--cov-fail-under`, and document the same command and target in both human-facing and agent-facing docs.

**Tech Stack:** Python 3.11+, uv, pytest, pytest-cov, Markdown.

---

## File Structure

- Modify `pyproject.toml`: add `pytest-cov` to the `dev` dependency group.
- Modify `uv.lock`: refresh dependency lock data after adding `pytest-cov`.
- Modify `README.md`: add a local development/testing section with full test and coverage commands.
- Modify `AGENTS.md`: update Verification instructions with the coverage command, `90%` target, and informational-only policy.
- No source files under `src/confluence_publisher` should change.
- No tests need to be added because this is a workflow and documentation change; verification is command-based.

## Task 1: Add Coverage Dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Run the coverage command before adding the dependency**

Run:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Expected: pytest exits non-zero because `--cov` is not recognized before `pytest-cov` is installed.

- [ ] **Step 2: Add `pytest-cov` to `pyproject.toml`**

Edit the dev dependency group so it becomes:

```toml
[dependency-groups]
dev = [
    "pytest>=8,<9",
    "pytest-asyncio>=0.23,<1",
    "pytest-cov>=6,<8",
]
```

- [ ] **Step 3: Update the lockfile**

Run:

```bash
uv lock
```

Expected: `uv.lock` changes and includes `pytest-cov` plus its coverage dependency.

- [ ] **Step 4: Verify the coverage command now runs**

Run:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Expected: tests pass and the terminal report includes a `TOTAL` coverage line. The current expected total is around `91%`.

- [ ] **Step 5: Commit dependency changes**

Run:

```bash
git add pyproject.toml uv.lock
git commit -m "Add local coverage dependency"
```

## Task 2: Document Coverage In README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a Development section after Setup**

Insert this section immediately after the existing `## Setup` section and before `## Usage`:

````markdown
## Development

Run the full test suite:

```bash
uv run pytest tests/confluence_publisher -v
```

Measure local test coverage:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Coverage is informational and not CI-enforced. The current observed baseline is `91%`; the project target is `90%` total coverage.
````

- [ ] **Step 2: Verify README contains the documented command and target**

Run:

```bash
rg "pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing|project target is `90%`" README.md
```

Expected: both the coverage command and target sentence are matched.

- [ ] **Step 3: Commit README changes**

Run:

```bash
git add README.md
git commit -m "Document local coverage workflow"
```

## Task 3: Update Agent Verification Instructions

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add coverage instructions under Verification**

Insert this block after the existing full test suite command and before the whitespace check:

````markdown
Measure coverage after behavior changes, bug fixes, or new features:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Coverage is informational, not a hard gate. The target is `90%` total coverage; the current observed baseline is `91%`.
````

- [ ] **Step 2: Verify AGENTS contains the same command and target**

Run:

```bash
rg "pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing|target is `90%`" AGENTS.md
```

Expected: both the coverage command and target sentence are matched.

- [ ] **Step 3: Commit AGENTS changes**

Run:

```bash
git add AGENTS.md
git commit -m "Document coverage verification target"
```

## Task 4: Final Verification

**Files:**
- Verify: `pyproject.toml`
- Verify: `uv.lock`
- Verify: `README.md`
- Verify: `AGENTS.md`

- [ ] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest tests/confluence_publisher -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the documented coverage command**

Run:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Expected: all tests pass and the report prints total coverage. A result below `90%` should be reported in the final summary but should not fail the command because no fail-under option is configured.

- [ ] **Step 3: Check whitespace**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 4: Confirm no coverage artifacts are staged**

Run:

```bash
git status --short
```

Expected: no `.coverage`, `htmlcov/`, or other coverage artifacts are staged or untracked. The existing untracked `.codex` entry may remain unrelated.
