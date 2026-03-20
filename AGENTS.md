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

- **pre-commit**: Runs ruff format, ruff check (with auto-fix), and mypy
- **pre-push**: Runs ruff check, mypy, and pytest

To install hooks on a new machine:
```bash
./scripts/install-hooks.sh
```

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
