# Coverage Workflow Design

## Purpose

Add a local, informational test coverage workflow for `confluence-markdown-publisher`.

The project already has a full pytest suite under `tests/confluence_publisher`. A one-time baseline measurement with `coverage` reported:

- `185 passed`
- total coverage: `91%`
- lowest module coverage: `src/confluence_publisher/mcp_atlassian.py` at `86%`

The initial project target is `90%` total coverage. This target is intentionally below the current baseline so coverage tracking starts as a useful signal without creating immediate cleanup work.

## Goals

- Provide a stable local command for measuring test coverage.
- Keep coverage reporting informational only.
- Document the command and target coverage in both `README.md` and `AGENTS.md`.
- Make the workflow easy to run after bug fixes and feature changes.

## Non-Goals

- Do not add CI coverage enforcement.
- Do not fail local test runs when coverage is below the target.
- Do not attempt to raise coverage to `100%` in this change.
- Do not add broad test rewrites unrelated to the coverage workflow.

## Design

Add `pytest-cov` to the development dependency group in `pyproject.toml`. This keeps coverage measurement inside the existing pytest workflow and lets developers run tests and coverage with a single command:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

The command intentionally omits `--cov-fail-under=90` because the coverage check is informational. The terminal report shows the total percentage and missing lines by module, which is enough to decide whether a follow-up test should be added after a change.

Update `README.md` with a development or testing section that lists:

- the existing full test command;
- the coverage command;
- the current observed baseline of `91%`;
- the target coverage of `90%`;
- a note that coverage is informational and not CI-enforced.

Update `AGENTS.md` under Verification so future maintenance work runs the coverage command after behavior changes, bug fixes, or new features. The agent-facing instruction should use the same command and target as the README.

## Data Flow

1. A developer runs the coverage command through `uv`.
2. `uv` provides the dev environment, including `pytest` and `pytest-cov`.
3. `pytest` runs tests from `tests/confluence_publisher`.
4. `pytest-cov` measures imports from `confluence_publisher`.
5. The terminal report prints module coverage and missing lines.

No coverage artifacts need to be committed.

## Error Handling

If coverage dependencies are not installed, `uv run` resolves them from the development dependency group after `uv lock` is updated.

If tests fail, the coverage result is not considered meaningful until the test failure is fixed. The existing full test command remains the primary correctness check.

If coverage drops below `90%`, the command still exits based on test status only. The report should be treated as a signal to consider adding focused tests, not as a hard gate.

## Testing

Implementation should be verified with:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

The expected result after implementation is a passing test suite and a terminal coverage report. The report should remain informational, with no `fail-under` behavior.
