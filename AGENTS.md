# Repository Guidelines

## Project Structure & Module Organization

- Backend: Django project in `wmsmaster/` (settings, urls, wsgi). Domain apps live under `allapp/*` (e.g. `inbound/`, `outbound/`, `inventory/`).
- UI assets: templates in `templates/`, static files in `static/`. Tailwind build tooling is in `frontend/` and outputs `static/css/app.css`.
- Dependencies: pinned files in `requirements/` (`requirements.txt`, `dev.txt`). Example env in `.env.example`.
- CI: see `.github/workflows/ci.yml` for lint, tests, coverage, image build, and deploy.
- Prefer focused changes. Inspect the relevant implementation and existing tests before modifying code.
- Do not redesign unrelated parts of the repository unless the task explicitly requires it.

## Database Test Execution

- The canonical database engine for database-backed tests is MySQL 8.
- Do not switch database-backed tests to SQLite when MySQL is unavailable.
- Load local test credentials from `.env.test.local`.
- Never print database passwords or commit `.env.test.local`.

### Database Safety

- The canonical automated test database is:

  `wms_db_test`

- The production/business database is:

  `wms_db`

- Automated tests must never delete, recreate, truncate, flush, migrate, reset, or otherwise write to `wms_db`.
- Destructive database operations are allowed only against `wms_db_test`.
- Before any destructive database operation, resolve and validate the target database name.
- The resolved database name must be exactly `wms_db_test`.
- Reject:
  - an empty database name;
  - `wms_db`;
  - any unexpected database name;
  - any database whose identity cannot be established safely.
- Never use production customer orders or production customer data for destructive testing.
- If database identity is uncertain, stop rather than guessing.

### Database Availability

- Before running database tests, perform a bounded MySQL reachability check against `DB_HOST:DB_PORT`.
- If the connection fails with a sandbox or network permission error, request scoped network escalation and retry the same check.
- Do not install or use SQLite as a fallback.
- If MySQL is not running, use the repository's `compose.test.yml` test environment when Docker is available.
- Do not wait indefinitely for database creation or connectivity.
- When blocked, identify the cause, such as:
  - sandbox access;
  - MySQL availability;
  - credentials;
  - database privileges;
  - an existing or corrupted test schema;
  - migration failure.

### Database Reuse Policy

During normal development and Codex iteration, database-backed tests should default to:

```bash
pytest --reuse-db ...
```

Do not recreate `wms_db_test` after every code change.

A healthy reused test database is the default development path.

Frontend-only changes must not trigger Django test database recreation merely as a precaution.

Ordinary Python business-logic changes must not trigger database recreation unless they alter database schema or migrations.

Recreate `wms_db_test` with:

```bash
pytest --reuse-db --create-db ...
```

only when at least one of the following is true:

1. Django migration files were added, removed, renamed, or changed.
2. The reused test database fails a migration or schema sanity check.
3. Migration behavior itself is under test.
4. A clean-database verification was explicitly requested.
5. The final release gate is being executed.
6. There is concrete evidence that `wms_db_test` is stale or corrupted.

When migrations change:

1. Rebuild `wms_db_test` once.
2. Execute the real migration chain.
3. Validate the resulting schema.
4. Run the relevant database tests.
5. Return to `--reuse-db` for subsequent test runs.

Do not rebuild a valid test database merely because the previous pytest process has exited.

### Migration Safety

- Final verification must exercise the real Django migration chain.
- Do not use `--no-migrations` or `--fast-db` to hide migration problems in final verification.
- Do not manually add missing columns as a substitute for correcting migration state.
- Do not use `--fake` merely to make migrations or tests appear successful.
- Do not modify already-deployed historical migrations solely to make tests faster unless the task explicitly requires it and compatibility consequences have been analyzed.
- A reused database must not be considered healthy merely because pytest can connect to it.
- If migration or schema validation fails, recreate `wms_db_test`.
- Final release verification must cover clean database creation and migration from the beginning to the current migration HEAD.

### Database Test Execution Order

- Run database tests serially first.
- Use parallel pytest only after serial database setup and relevant database tests have succeeded.
- Do not repeatedly recreate the test database between related database test groups.
- Never report database tests as passed when they were skipped or blocked by infrastructure.

## Build, Test, and Development Commands

- Setup:

  ```bash
  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements/dev.txt
  ```

- Configure env:

  ```bash
  cp .env.example .env
  ```

  Then configure `SECRET_KEY`, `DB_*`, `DEBUG`, and `ALLOWED_HOSTS`.

- Migrate DB:

  ```bash
  python manage.py migrate --noinput
  ```

- Run server:

  ```bash
  python manage.py runserver
  ```

  Default: `http://127.0.0.1:8000`

- Build CSS:

  ```bash
  cd frontend
  npm ci
  ./build_tailwind.sh
  ```

  Output: `static/css/app.css`.

- Full backend test suite:

  ```bash
  pytest -q
  ```

  This uses `pytest-django`.

  Do not use the full backend suite as the default inner development loop. Prefer targeted tests first.

- Parallel full suite:

  ```bash
  pytest -n auto
  ```

  Use parallel execution only after serial database setup has succeeded.

## Coding Style & Naming Conventions

- Python 3.12.
- Format with Black.
- Sort imports with isort.
- Lint with Ruff and Flake8.
- Line length: 100.
- CI ignores E203/W503 where configured.
- Modules and files: `snake_case`.
- Classes: `CamelCase`.
- Functions and variables: `snake_case`.
- Keep functions focused and reasonably small.
- Add docstrings for public APIs where appropriate.
- Co-locate app-specific tests with the corresponding app when consistent with the existing repository structure.
- Follow existing repository patterns before introducing new abstractions or dependencies.

## Testing Guidelines

- Primary frameworks: `pytest`, `pytest-django`.
- Most tests live in per-app `tests.py`.
- Additional suites exist under locations such as `allapp/locations/test_*.py`.
- Mark database-backed tests with `@pytest.mark.django_db` when required.
- Ensure local test configuration contains valid MySQL settings.
- CI aggregates coverage shards and enforces at least 70% coverage.
- Generate local coverage with:

  ```bash
  pytest --cov=allapp --cov-report=term-missing
  ```

- Tests should verify observable behavior and important invariants, not merely implementation details.
- For critical business flows, include failure paths and concurrency behavior where relevant.

## Test Selection & Agent Behavior

After changing code, run the smallest relevant test set first.

Do not run the full repository test suite or full release gate after every edit.

Escalate testing progressively:

```text
targeted unit tests
        ↓
targeted integration/database tests
        ↓
module-level tests
        ↓
final release gate
```

During iterative development:

1. Inspect the relevant code and existing tests.
2. Make a focused change.
3. Run the smallest test that exercises that change.
4. If it fails, diagnose and fix the failure.
5. Repeat the focused test.
6. Expand to related integration tests after focused tests pass.
7. Reuse `wms_db_test` whenever its schema remains valid.
8. Run broader module validation when the implementation becomes stable.
9. Run the expensive final release gate only when appropriate.

Avoid this pattern:

```text
edit one file
→ rebuild database
→ replay all migrations
→ run full release gate
→ edit another file
→ rebuild everything again
```

Prefer:

```text
edit
→ targeted test
→ edit
→ targeted test
→ related integration tests using --reuse-db
→ broader validation
→ final release gate once
```

### Frontend-Only Changes

- Run the relevant frontend unit or behavior tests first.
- Do not rebuild the Django test database.
- Run backend/database integration tests only when the frontend change depends on an API contract that requires verification.
- Do not run unrelated backend test suites merely as a precaution.

### Backend Business Logic Changes

- Run directly related Python tests first.
- If database access is required, reuse the healthy `wms_db_test`.
- Do not recreate the database unless schema or migration state requires it.

### Model and Migration Changes

- Run migration checks.
- Recreate `wms_db_test` once when necessary.
- Execute the real migration chain.
- Validate migration/schema state.
- Run the relevant database tests.
- Subsequent tests should return to `--reuse-db`.

### High-Risk Changes

For payment, inventory, order processing, concurrency, permissions, migrations, billing, or other high-risk business logic:

1. Run focused tests first.
2. Run relevant MySQL integration tests.
3. Run concurrency/failure-path tests where applicable.
4. Run the appropriate broader module gate before completion.

Do not recreate the database between these groups unless required.

### Failure Handling

If a broad test or release gate fails:

1. Identify the smallest test or command that reproduces the failure.
2. Fix the underlying problem.
3. Run the focused reproducer until it passes.
4. Run related tests.
5. Rerun the broad gate only after focused verification succeeds.

Do not repeatedly run an expensive full gate while the same focused failure remains unresolved.

## Release Gate

The release gate is intentionally more expensive than normal development testing.

A final release gate may use:

```text
clean wms_db_test
        ↓
all Django migrations
        ↓
database integration tests
        ↓
critical concurrency/business tests
        ↓
data accuracy checks
        ↓
frontend tests
        ↓
production builds
```

This clean-database verification is valuable because it proves that a fresh installation can migrate from the beginning to the current schema.

However:

- Do not use the release gate as the default inner development loop.
- Normally run it near final task completion, when explicitly requested, or when migration correctness must be verified.
- A final release gate must not use shortcuts that bypass real migrations.
- If external credentials or environment configuration block part of the gate, report that portion as blocked rather than passed.

## Test Performance

Testing time is an engineering constraint.

If a test spends disproportionate time creating databases, replaying migrations, collecting tests, building frontends, or performing unrelated validation, investigate the source rather than automatically accepting the delay.

For slow database creation, inspect especially:

- `RunPython`;
- `RunSQL`;
- large data migrations;
- per-row ORM loops;
- repeated fixture/bootstrap work;
- unnecessary initialization;
- expensive operations that could be batched safely.

Prefer test database reuse over bypassing migration correctness.

Do not modify historical production migrations merely for performance without first identifying the bottleneck and assessing compatibility.

When useful, compare:

```text
fresh database + targeted test
```

against:

```text
reused database + same targeted test
```

to distinguish migration/setup cost from actual test execution time.

## Completion Reporting

When reporting completion, clearly distinguish between:

- tests actually executed and passed;
- tests executed and failed;
- tests not executed;
- tests skipped;
- tests blocked by environment or configuration;
- behavior inferred from code but not empirically verified.

Do not describe blocked, skipped, or unexecuted tests as passed.

For database work, report whether `wms_db_test` was reused or recreated.

If it was recreated, state why recreation was necessary.

Do not claim production readiness when required real-environment validation remains outstanding.

Do not hide failures, skipped tests, unresolved warnings, or environmental blockers merely to produce a successful-looking completion report.

## Commit & Pull Request Guidelines

- Use imperative commit messages.
- Prefer Conventional Commits where practical, for example:

  ```text
  feat(outbound): wave picking generation
  fix(inventory): correct negative delta
  ```

- Pull requests should include:
  - purpose;
  - linked issues where applicable;
  - migration notes;
  - screenshots for UI changes where useful;
  - test steps and results.
- Keep CI green.
- Do not lower coverage merely to make CI pass.

## Security & Configuration Tips

- Use `.env` via `django-environ`.
- Never commit secrets.
- Set `DEBUG=False` outside development.
- Configure `ALLOWED_HOSTS` appropriately.
- Use strong MySQL credentials.
- MySQL charset `utf8mb4` is already configured.
- Never expose passwords, API keys, private keys, certificates, payment credentials, or other secrets in test output, logs, commits, or completion reports.

## Pre-commit Hooks

Install once:

```bash
pip install pre-commit
pre-commit install
```

Run on all files:

```bash
pre-commit run --all-files
```

Configured hooks include:

- Black;
- isort;
- Ruff;
- Flake8.

Bandit runs on pre-push.

Edit `.pre-commit-config.yaml` when hook configuration legitimately needs to change.

