# Developer Guidelines

This document provides guidelines for AI assistants and developers working on this codebase.

## Project Overview

Python monorepo using:
- **uv** - Package manager
- **Ruff** - Linting and formatting
- **MyPy** - Type checking
- **pytest** - Testing

## File Size Guidelines

- **Maximum file size: 600 lines** (individual source files)
- **Maximum line length: 320 characters** (enforced by ruff)
- New files that exceed these limits will generate lint warnings
- Existing files that exceed these limits are grandfathered in
- If a file approaches these limits, consider splitting it into modules

## Code Quality Standards

### Always Fix Warnings Before Pushing

The following tools must report zero errors/warnings before code is pushed:

```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Type check
uv run mypy .

# Run tests
uv run pytest .
```

### Pre-commit Hooks

Native git hooks are configured to validate code automatically:

- **pre-commit**: Runs code formatting (ruff), linting (ruff), type checking (mypy), and secret scanning (gitleaks)
- **pre-push**: Runs ruff check, mypy, and pytest

To install hooks on a new machine:
```bash
./scripts/install-hooks.sh
```

### Secret Scanning

This project uses [Gitleaks](https://github.com/gitleaks/gitleaks) for secret detection:

- **Pre-commit hook**: Scans staged files for potential secrets before commit (uses `scripts/secrets-scan.sh`)
- **CI workflow**: Runs gitleaks on push/PR to detect any secrets that slipped through

**Requirements for pre-commit hook:**
- `gitleaks` binary in PATH, OR
- `mise` with gitleaks plugin, OR
- `rg` (ripgrep) for fallback scanning

**Allowlisted files** (defined in `.gitleaks.toml`):
- `.env.example` - Contains empty/safe values
- `.venv/` - Virtual environment
- `__pycache__/` - Python cache

**If secrets are detected:**
1. Do NOT commit the file
2. Remove the secret and recommit
3. If the secret was already pushed, rotate it immediately

### Linting Configuration

Ruff is configured with:
- Line length: 140 characters
- Rules: E, F, I, N, W, UP
- E501 (line length) is set to warn-only to allow gradual compliance

## Development Workflow

1. **Write code** with proper type annotations
2. **Run locally**: `uv run ruff check . && uv run mypy . && uv run pytest .`
3. **Fix all warnings/errors** reported by linters
4. **Commit** - pre-commit hook will validate
5. **Push** - pre-push hook will run full validation

## Package Structure

```
bridge_core/          # Main FastAPI application
ingress_sdk/          # SDK for ingress adapters
renderer_sonos/        # Sonos/SYMFONISK renderer adapter
adapters/synthetic/   # Synthetic test source
adapters/linux_audio/ # Linux audio adapter
adapters/linux_bluetooth/ # Linux Bluetooth adapter
shared/               # Shared types and utilities
ui_web/               # Web UI package
integration_homeassistant/ # Home Assistant integration
```

## Running Tests

```bash
# All tests
uv run pytest .

# With coverage
uv run pytest --cov=. --cov-report=term-missing
```

## Type Checking

All code should pass strict mypy type checking. Use type annotations for:
- Function parameters and return values
- Class attributes
- Module-level variables

```python
def process_audio(data: bytes) -> AudioFrame:
    ...
```

## Git Ignore Guidelines

The `.gitignore` file is critical for repository hygiene. Changes to it require thorough review.

### Required Exclusions

The following patterns **must** always be excluded:

| Pattern | Purpose |
|---------|---------|
| `__pycache__/` | Python bytecode cache |
| `*.py[cod]` | Python compiled files (.pyc, .pyo) |
| `.pytest_cache/` | Pytest cache directories |
| `.coverage` | Coverage reports |
| `*.egg-info/` | Python package metadata |
| `.env` | Environment variables (may contain secrets) |
| `uv.lock` | Lockfile (use project lockfile only) |

### Review Checklist for .gitignore Changes

Before approving a PR that modifies `.gitignore`:

1. **Verify __pycache__ exclusion** - Must always be present
2. **Verify *.pyc/*.pyo exclusion** - Must always be present  
3. **Verify test artifacts** - `.pytest_cache/`, `.coverage`, `htmlcov/`
4. **Verify secrets protection** - `.env`, `config/` with credentials
5. **No over-broad exclusions** - Don't exclude entire languages/frameworks
6. **Documentation** - Add comment explaining non-obvious entries

### CI Gate

PRs modifying `.gitignore` trigger a `gitignore-review` job that posts a review checklist as a CI notice.

## Agent Skills

See [SKILLS.md](./SKILLS.md) for available skills and usage guides.

### If You Commit __pycache__ Files

If you accidentally commit `__pycache__` files:

```bash
# Remove from git tracking (but keep locally)
git rm -r --cached __pycache__/
git rm -r --cached *.pyc

# Commit the removal
git commit -m "chore: remove accidentally committed cache files"

# Push
git push
```

Do NOT add `__pycache__` removal to `.gitignore` changes - the damage is already done.

## Web UI Module

The `ui_web/` directory contains the web UI package. See [ui_web/AGENTS.md](ui_web/AGENTS.md) for frontend design guidelines.
