#!/bin/bash
set -e

cd "$(git rev-parse --show-toplevel)"

echo "Installing git hooks..."

HOOKS_DIR=".git/hooks"
PRECOMMIT="$HOOKS_DIR/pre-commit"
PREPUSH="$HOOKS_DIR/pre-push"

cat > "$PRECOMMIT" <<'EOF'
#!/bin/bash
exec scripts/secrets-scan.sh
EOF

cat > "$PREPUSH" <<'EOF'
#!/bin/bash
set -e

cd "$(git rev-parse --show-toplevel)"

echo "Running pre-push hooks..."

echo "  Linting with ruff..."
uv run ruff check .

echo "  Type checking with mypy..."
uv run mypy --exclude "tests|test_|integration_homeassistant|scripts" .

echo "  Running tests in parallel..."
uv run pytest -n auto .

echo "Pre-push hooks passed!"
EOF

chmod +x "$PRECOMMIT"
chmod +x "$PREPUSH"

echo "Git hooks installed successfully!"
echo "  - pre-commit: $PRECOMMIT"
echo "  - pre-push: $PREPUSH"
