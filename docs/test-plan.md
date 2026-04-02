# WMS System Test Plan

## Purpose

This plan defines how the WMS system should be tested across backend services, APIs,
console pages, owner-facing pages, and PDA workflows. The goal is to keep release
quality tied to business outcomes instead of only checking whether a single endpoint
returns `200`.

As of April 2, 2026, the current backend regression baseline is:

- Core regression command passes locally with `67 passed`
- Billing-only regression command passes locally with `46 passed`
- The codebase still emits Django deprecation warnings that should be tracked as
  technical debt, not ignored as part of the release gate

## Quality Goals

- Core warehouse flows remain stable: inbound, inventory, outbound, and tasking
- Settlement flows remain stable: metrics, accruals, period lock, invoice, export
- `owner + warehouse` data scoping remains correct in models, services, and APIs
- Reporting and billing numbers remain explainable against inventory and task facts
- Every release candidate has a repeatable smoke path for console, owner, and PDA use

## Test Layers

### 1. Static Checks

Run on every pull request:

- Black
- isort
- Ruff
- Flake8
- Migration presence check after model changes
- URL and route sanity checks for duplicate or stale registrations

### 2. Model and Service Tests

Primary responsibility:

- Guard data integrity
- Guard scope integrity
- Guard business calculations

Must cover:

- `accounts`: user scope and permission boundaries
- `baseinfo`, `products`, `locations`: master data validity and uniqueness
- `inbound`: receiving and posting paths
- `inventory`: transaction ledgers, summary, and snapshot generation
- `outbound`: order lifecycle, approvals, and cancellation rollback
- `tasking`: task generation, scan handling, posting, and idempotency
- `billing`: rule resolution, metric generation, accrual, lock, invoice, export
- `reports`: snapshot generation and consistency against source business data

### 3. API Integration Tests

Primary responsibility:

- Authentication and expiry
- Authorization and scoping
- Filters, pagination, ordering, and search
- Export endpoints and response content
- Action endpoints such as approve, cancel, post, lock, and invoice

### 4. End-to-End Business Flow Tests

These are the most valuable tests and should stay few but stable.

Required flow set:

1. Receive without order -> putaway/post -> inventory visible
2. Create outbound -> approve -> generate task -> scan -> post
3. Cancel or unapprove outbound -> reservation released
4. Stock count or adjustment -> inventory updated -> report visible
5. Inventory snapshot -> billing metrics -> accrual -> lock -> invoice
6. Owner portal -> view inventory -> view bill -> export bill
7. PDA -> login -> pick/scan task -> state advances correctly
8. Console -> billing overview -> drill into bill detail

Executable coverage for the eight flows lives in:

- `allapp/test_business_flows.py`

Manual multi-client smoke companion lives in:

- `docs/business-flow-smoke-checklist.md`

### 5. Frontend and Multi-Client Smoke Tests

Current priority is smoke coverage, not full browser automation.

Targets:

- Django console pages
- `wmsownersale`
- `wmspda`

Minimum smoke actions:

- Login
- Open key list pages
- Apply filters
- Open detail pages
- Submit one key action
- Export one file
- Verify unauthorized access is blocked

### 6. Non-Functional Tests

Track separately from feature regression:

- Large-list and export performance
- Concurrent actions and duplicate submit protection
- Unauthorized and cross-scope access attempts
- Backup, restore, and migration verification
- Scheduler and command observability

## CI Suite Layout

The repository currently stores most tests in per-app `tests.py` files. CI should
therefore group tests by business domain instead of pointing to non-existent
`allapp/tests/test_*.py` files.

### Suite A: `platform`

Purpose:

- User scope
- Master data
- Core configuration

Files:

- `allapp/accounts/tests.py`
- `allapp/baseinfo/tests.py`
- `allapp/core/tests.py`
- `allapp/driverapp/tests.py`
- `allapp/locations/tests.py`
- `allapp/products/tests.py`

### Suite B: `warehouse`

Purpose:

- Core warehouse operations

Files:

- `allapp/inbound/tests.py`
- `allapp/inventory/tests.py`
- `allapp/outbound/tests.py`
- `allapp/tasking/tests.py`

### Suite C: `settlement`

Purpose:

- Reporting, billing, and console-facing settlement flows

Files:

- `allapp/reports/tests.py`
- `allapp/billing/tests.py`
- `allapp/console/tests.py`
- `allapp/salesapp/tests.py`

### Suite D: `business-flows`

Purpose:

- Cross-app business loop validation for the eight critical end-to-end flows

Files:

- `allapp/test_business_flows.py`

## Local Commands

### Fast backend regression

```bash
./.venv/bin/python -m pytest -q \
  allapp/accounts/tests.py \
  allapp/baseinfo/tests.py \
  allapp/core/tests.py \
  allapp/driverapp/tests.py \
  allapp/inbound/tests.py \
  allapp/locations/tests.py \
  allapp/outbound/tests.py \
  allapp/reports/tests.py \
  allapp/inventory/tests.py \
  allapp/tasking/tests.py \
  allapp/billing/tests.py \
  allapp/test_business_flows.py
```

### Platform suite

```bash
./.venv/bin/python -m pytest -q \
  allapp/accounts/tests.py \
  allapp/baseinfo/tests.py \
  allapp/core/tests.py \
  allapp/driverapp/tests.py \
  allapp/locations/tests.py \
  allapp/products/tests.py
```

### Warehouse suite

```bash
./.venv/bin/python -m pytest -q \
  allapp/inbound/tests.py \
  allapp/inventory/tests.py \
  allapp/outbound/tests.py \
  allapp/tasking/tests.py
```

### Settlement suite

```bash
./.venv/bin/python -m pytest -q \
  allapp/reports/tests.py \
  allapp/billing/tests.py \
  allapp/console/tests.py \
  allapp/salesapp/tests.py
```

### Business-flow suite

```bash
./.venv/bin/python -m pytest -q allapp/test_business_flows.py
```

### Coverage run

```bash
./.venv/bin/python -m pytest -q --cov=allapp --cov-report=term-missing
```

## Test Environments

Use four environments with clear ownership:

- Local: developer self-check and focused module regression
- CI: pull request validation and coverage gating
- Staging: cross-team integration and UAT
- Pre-production: release-candidate regression and rollback rehearsal

Standard stack:

- Python 3.12
- Django 5.2.5
- MySQL 8
- Redis 7

## Test Data Strategy

Create repeatable fixtures around these canonical entities:

- At least 2 owners
- At least 2 warehouses
- Shared and warehouse-specific products
- One inbound receipt path
- One outbound dispatch path
- One tasking path with scan data
- One reporting snapshot date
- One billing period with rules, metrics, accruals, and bill

Guidelines:

- Prefer deterministic factories or setup helpers over ad hoc shell inserts
- Keep one reference owner and one reference warehouse for smoke tests
- Seed at least one cross-scope negative case in each suite

## Release Gates

### Pull Request Gate

- Lint passes
- Relevant suite passes
- New model changes include migrations
- No new critical warnings or obvious scope regressions

### Nightly Gate

- All three CI suites pass
- Coverage report is generated
- Scheduler, snapshot, export, and billing paths are covered
- Warning count trend is monitored

### Release Gate

- Staging smoke passes
- All eight critical flows pass
- No blocker or critical defects remain open
- Billing and reporting totals are spot-checked against source facts

## Defect Priority

- Blocker: release must stop
- Critical: core flow broken, wrong data scope, wrong inventory, or wrong billing result
- Major: degraded but workaround exists
- Minor: cosmetic or low-frequency issue

## Immediate Actions

1. Keep `pytest.ini` as the shared pytest contract for the repo
2. Keep CI suites aligned to real app test files
3. Add migration and route sanity checks to PR review discipline
4. Expand smoke scripts for `wmsownersale` and `wmspda`
5. Track Django deprecation warnings and reduce them over time instead of suppressing them
