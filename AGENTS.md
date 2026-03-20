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

## Web UI Design Guidelines

This project uses the **Impeccable** design system for frontend work. These guidelines apply to all UI components, pages, and styling.

### Design Philosophy

**Commit to a BOLD aesthetic direction.** Avoid generic "AI slop" aesthetics:
- Pick a clear conceptual direction and execute with precision
- Choose distinctive fonts, not Inter/Roboto/system defaults
- Create visual rhythm through varied spacing, not uniform padding
- Embrace asymmetry—left-aligned text feels more designed than centered everything

**The AI Slop Test**: If you showed this UI to someone and said "AI made this," would they believe you immediately? If yes, that's the problem.

### Typography

| Guideline | Do | Don't |
|-----------|-----|-------|
| Fonts | Instrument Sans, Plus Jakarta Sans, Fraunces, Newsreader | Inter, Roboto, Arial, system defaults |
| Scale | Fluid `clamp()` for headings, fixed `rem` for UI | px for body text |
| Hierarchy | 5 sizes with high contrast (xs, sm, base, lg, xl+) | Too many sizes too close together |
| Numbers | `font-variant-numeric: tabular-nums` for data | Variable-width numbers in tables |

### Color & Theme

| Guideline | Do | Don't |
|-----------|-----|-------|
| Color Space | OKLCH for perceptually uniform palettes | HSL |
| Neutrals | Tint toward brand hue (chroma: 0.01) | Pure gray (#808080) |
| Contrast | WCAG AA minimum 4.5:1 body, 3:1 large text | Gray text on colored backgrounds |
| Dark Mode | Lighter surfaces for depth, reduce weight | Inverted light mode with shadows |
| Accents | Use sparingly (10% of visual weight) | Accent color everywhere |

**Never use pure black (#000) or pure white (#fff)**—always tint.

### Spatial Design

| Guideline | Do | Don't |
|-----------|-----|-------|
| Base Unit | 4pt grid (4, 8, 12, 16, 24, 32, 48, 64px) | Arbitrary spacing values |
| Layout | Container queries for components | Viewport-only responsiveness |
| Cards | Use only when content needs distinct boundaries | Wrap everything in cards |
| Nesting | Never nest cards inside cards | Visual noise, flattened hierarchy |

### Motion & Animation

| Guideline | Do | Don't |
|-----------|-----|-------|
| Duration | 100-150ms (instant), 200-300ms (state), 300-500ms (layout) | >500ms for UI feedback |
| Easing | `ease-out-quart` for entrances, `ease-in` for exits | `ease`, bounce, elastic |
| Properties | Animate `transform` and `opacity` only | Animate width, height, padding |
| Reduced Motion | Support `prefers-reduced-motion` | Ignore accessibility |
| Height Animations | Use `grid-template-rows: 0fr → 1fr` | Animate `height` directly |

### Interaction Design

| State | Implementation |
|-------|---------------|
| Default | Base styling |
| Hover | Subtle lift, color shift (pointer only) |
| Focus | Visible ring for keyboard (`:focus-visible`) |
| Active | Pressed appearance, darker |
| Disabled | Reduced opacity, `cursor: not-allowed` |
| Loading | Skeleton screens > spinners |
| Error | Red border, icon, message below field |

**Focus rings are mandatory**—never `outline: none` without `:focus-visible` alternative.

### UX Writing

| Guideline | Example |
|-----------|---------|
| Buttons | "Save changes" not "OK"; "Delete message" not "Yes" |
| Errors | "Email needs @ symbol" not "Invalid input" |
| Empty states | "No projects yet. Create your first one to get started." not "No items" |
| Consistency | Pick one term and stick with it (Delete, not Remove/Trash) |

**Never blame the user**: "This field is required" not "You made an error".

### Framework-Specific Notes

For **htmx** interfaces:
- Use semantic HTML with appropriate ARIA attributes
- Progressive enhancement: work without JS, enhance with htmx
- Skeleton loading states during htmx transitions
- Focus management after content swaps

For **Alpine.js** components:
- Keep logic minimal in `x-data`—delegate to functions
- Use `x-show` with CSS transitions for smooth state changes
- `x-init` for component initialization
- Prefer `@click.window` for global event handlers
