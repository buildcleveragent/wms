# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

WMS (Warehouse Management System) — a Django 5.2 monolith with 18 domain apps under `allapp/`, two UNI-framework mobile apps (`wmspda/`, `wmsownersale/`), and server-rendered templates with Tailwind CSS.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env   # then set SECRET_KEY, DB_*, etc.
python manage.py migrate --noinput

# Run
python manage.py runserver

# Tests
pytest -q                                       # quick
pytest -n auto                                  # parallel (pytest-xdist)
pytest --cov=allapp --cov-report=term-missing   # with coverage (target ≥70%)
pytest allapp/inventory/tests.py -k test_name   # single test

# Lint & format
black .
isort .
ruff check .
flake8 . --max-line-length=100 --extend-ignore=E203,W503
pre-commit run --all-files

# Docker
docker build -t wms:latest .
docker run -p 8000:8000 wms:latest
```

## Architecture

**Backend:** Django settings in `wmsmaster/settings.py`, environment-based config via `django-environ`. Three modes: development, test, production (controlled by `APP_ENV`). MySQL 8.0, Redis for scheduling.

**Domain apps** (`allapp/`):
- **inbound** — receiving, purchase orders, putaway task creation
- **outbound** — orders, wave picking, allocation, shipping
- **inventory** — multi-location tracking, adjustments, snapshots
- **tasking** — task generation, assignment, scanning, approval workflows
- **billing** — usage-based billing, metrics, accruals, invoicing, period locks
- **baseinfo** — owners, customers, suppliers, warehouse locations
- **products** — SKUs, UOM management
- **console** — web dashboard and operational views
- **core** — shared models, data accuracy utilities, domain helpers
- **reports**, **accounts**, **api**, **locations**, **strategies**, **labeling**, **salesapp**, **driverapp**

Each app follows: `models.py`, `views.py`, `serializers.py`, `services.py` (business logic), `admin.py`, `urls.py`, `tests.py`.

**API routing:** REST via DRF. JWT auth (`simplejwt`). Unified v1 API at `api/v1/` (aggregated in `allapp/api/urls.py`). Some apps also mount directly under `api/` in the root `wmsmaster/urls.py`.

**Auth:** Custom user model in `accounts`. JWT tokens for API clients, session-based for web/admin.

**Mobile apps:** `wmspda/` (PDA warehouse operations) and `wmsownersale/` (owner/sales interface) — Vue-based UNI framework, built via HBuilder.

## Testing

- Framework: pytest + pytest-django. DB tests need `@pytest.mark.django_db`.
- Markers: `unit`, `integration`, `api`, `smoke`, `e2e`, `slow` (defined in `pytest.ini`).
- CI splits tests into 4 shards: platform, warehouse, settlement, business-flows.
- Business flow tests live in `allapp/test_business_flows.py`.

## Code Style

- Python 3.12, Black formatter, isort (black profile), Ruff + Flake8 (line length 100, ignore E203/W503).
- Commit messages: Conventional Commits — `feat(outbound): wave picking generation`, `fix(inventory): correct negative delta`.
- Pre-commit hooks: `pre-commit install` to activate. Hooks run Black, isort, Ruff, Flake8; Bandit on pre-push.

## Key Patterns

- Service layer: complex business logic goes in `services.py`, not in views or models.
- Data accuracy: `core` module has cleanup/validation utilities; inventory snapshots for audit trail.
- Billing scheduler: started automatically by `docker/start.sh` alongside the Django server.
- Owner-based data isolation throughout billing and baseinfo modules.
