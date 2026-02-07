# Repository Guidelines

## Project Structure & Module Organization
- Backend: Django project in `wmsmaster/` (settings, urls, wsgi). Domain apps live under `allapp/*` (e.g., `inbound/`, `outbound/`, `inventory/`).
- UI assets: templates in `templates/`, static files in `static/`. Tailwind build tooling is in `frontend/` and outputs `static/css/app.css`.
- Dependencies: pinned files in `requirements/` (`requirements.txt`, `dev.txt`). Example env in `.env.example`.
- CI: see `.github/workflows/ci.yml` for lint, tests, coverage, image build, and deploy.

## Build, Test, and Development Commands
- Setup: `python -m venv .venv && source .venv/bin/activate && pip install -r requirements/dev.txt`
- Configure env: `cp .env.example .env` then set `SECRET_KEY`, `DB_*`, `DEBUG`, `ALLOWED_HOSTS`.
- Migrate DB: `python manage.py migrate --noinput`
- Run server: `python manage.py runserver` (default: http://127.0.0.1:8000)
- Build CSS: `cd frontend && npm ci && ./build_tailwind.sh` → `static/css/app.css`
- Run tests: `pytest -q` (uses `pytest-django`); parallel: `pytest -n auto`.

## Coding Style & Naming Conventions
- Python 3.12. Format with Black; sort imports with isort; lint with Ruff and Flake8 (line length 100; E203/W503 ignored in CI).
- Naming: modules/files `snake_case`; classes `CamelCase`; functions/variables `snake_case`.
- Keep functions small, add docstrings for public APIs, and co-locate app-specific tests in the same app.

## Testing Guidelines
- Frameworks: `pytest`, `pytest-django`. Most tests live in per‑app `tests.py`; additional suites under `allapp/locations/test_*.py`.
- DB usage: mark with `@pytest.mark.django_db` when hitting the database. Ensure `.env` has valid MySQL `DB_*` for local runs.
- Coverage: CI aggregates shards and enforces ≥70%. Generate locally with `pytest --cov=allapp --cov-report=term-missing`.

## Commit & Pull Request Guidelines
- Messages: imperative mood; prefer Conventional Commits, e.g., `feat(outbound): wave picking generation` or `fix(inventory): correct negative delta`.
- PRs: include purpose, linked issues, migration notes, screenshots for UI, and test steps. Keep CI green and avoid lowering coverage.

## Security & Configuration Tips
- Use `.env` via `django-environ`; never commit secrets. Set `DEBUG=False` and configure `ALLOWED_HOSTS` in non‑dev.
- Database: use strong MySQL credentials; charset `utf8mb4` already configured.

## Pre-commit Hooks
- Install once: `pip install pre-commit && pre-commit install`.
- Run on all files: `pre-commit run --all-files`.
- Hooks: Black, isort, Ruff, Flake8; Bandit runs on pre-push. Edit `.pre-commit-config.yaml` if needed.
