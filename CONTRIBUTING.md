# Contributing

Thanks for helping improve VitaDrive. Keep changes focused, preserve the
SQLite and PostgreSQL paths, and include tests and documentation when
behavior or configuration changes.

## Development setup

Use Python 3.10 or newer:

```bash
git clone https://github.com/adotak/vitadrive.git
cd vitadrive
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

The dev extra includes pytest, coverage, Ruff, mypy, pip-audit, pre-commit,
and PyYAML.

## Checks

```bash
ruff check .
mypy
pytest -q
pytest -q --cov
pre-commit run --all-files
```

To exercise the PostgreSQL path, start a PostgreSQL database and set
`VITADRIVE_TEST_POSTGRES` to its SQLAlchemy-compatible URL:

```bash
docker run --rm --detach --name vitadrive-test-postgres \
  -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=vitadrive \
  -p 5432:5432 postgres:16
VITADRIVE_TEST_POSTGRES=postgresql://postgres:devpass@localhost:5432/vitadrive pytest -q
docker stop vitadrive-test-postgres
```

Run browser end-to-end tests with Chromium:

```bash
pip install -e ".[e2e]"
python -m playwright install chromium
pytest e2e -q --browser chromium
```

The Lighthouse CI configuration can be run locally with:

```bash
npx -y @lhci/cli@0.14.x autorun
```

## Database migrations

Update the SQLAlchemy Core tables in `vitadrive/storage.py`, then generate a
revision from the repository root:

```bash
alembic revision --autogenerate -m "describe the schema change"
```

Review the generated script; autogeneration is a starting point, not a
replacement for checking upgrade and downgrade behavior. Apply migrations to
the configured database with `DATABASE_URL=... vitadrive db upgrade`. Tests
cover SQLite and, when `VITADRIVE_TEST_POSTGRES` is set, PostgreSQL.

## Commits and pull requests

Use small conventional commits, for example:

- `feat(trips): add trip summary endpoint`
- `fix(api): reject mismatched vehicle identifiers`
- `test: cover daily summary merging`
- `docs: explain local setup`
- `chore: update issue forms`

Before requesting review:

- [ ] Explain the problem and the chosen approach.
- [ ] Add or update tests for behavior changes.
- [ ] Run the relevant checks above; include PostgreSQL or browser checks when
  the change touches those paths.
- [ ] Add a migration for schema changes and review it manually.
- [ ] Update user and operator documentation for new features or settings.
- [ ] Include before/after screenshots for dashboard changes and note any
  accessibility or responsive-layout considerations.
- [ ] Check that no secrets or generated local databases are included.
- [ ] Confirm CI checks pass before merging.
