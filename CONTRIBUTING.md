# Contributing to django-icv-tree

Practical guide for contributors to this package.

---

## Prerequisites

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Django 5.1 or later (installed as part of the dev setup)

No PostgreSQL is needed — the test suite uses SQLite.

---

## Local Development Setup

```bash
git clone https://github.com/icvoss/django-icv-tree.git
cd django-icv-tree

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install the package in editable mode plus test and dev dependencies
pip install -e ".[dev]"
pip install "Django~=5.1" pytest pytest-django pytest-cov pytest-mock factory-boy
```

---

## Running Tests

```bash
DJANGO_SETTINGS_MODULE=settings \
PYTHONPATH=src:tests \
pytest tests/ -v --tb=short
```

Or, if you have configured `[tool.pytest.ini_options]` in `pyproject.toml`
(which this repo does), simply:

```bash
pytest tests/ -v --tb=short
```

---

## Code Standards

All Python code is linted and formatted with [ruff](https://docs.astral.sh/ruff/),
configured in `pyproject.toml`.

| Setting | Value |
|---------|-------|
| Line length | 120 |
| Quote style | Double |
| Target Python | 3.11 |

```bash
ruff check .             # lint check
ruff format --check .    # format check (no writes)
ruff format .            # reformat in place
```

CI will fail if either check reports errors. Run both before pushing.

---

## Repository Structure

```
django-icv-tree/
    src/icv_tree/           # importable package
    tests/
        settings.py         # Django settings for the test suite
    pyproject.toml
    CHANGELOG.md
    README.md
    RELEASING.md
```

---

## Git Workflow

### Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <description>
```

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `chore` | Maintenance, version bumps, dependency updates |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `style` | Formatting, whitespace, no logic change |
| `refactor` | Code change that is neither a fix nor a feature |

### Branches and PRs

Push feature branches and open a pull request against `main`. CI must pass before
merging. Prefer small, focused commits over large ones.

---

## Releasing

See [RELEASING.md](RELEASING.md) for the full release flow.

The short version: bump version in `pyproject.toml` and `src/icv_tree/__init__.py`,
update `CHANGELOG.md`, open a PR, merge to `main`, then tag the merged commit with
`v<version>`. The tag triggers CI, which publishes to PyPI automatically.
