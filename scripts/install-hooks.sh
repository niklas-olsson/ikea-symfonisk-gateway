#!/bin/bash
set -e

cd "$(git rev-parse --show-toplevel)"

echo "Installing git hooks..."

HOOKS_DIR=".git/hooks"
PRECOMMIT="$HOOKS_DIR/pre-commit"
PREPUSH="$HOOKS_DIR/pre-push"

if [ ! -f "$PRECOMMIT" ]; then
    echo "Error: $PRECOMMIT not found"
    exit 1
fi

if [ ! -f "$PREPUSH" ]; then
    echo "Error: $PREPUSH not found"
    exit 1
fi

chmod +x "$PRECOMMIT"
chmod +x "$PREPUSH"

echo "Git hooks installed successfully!"
echo "  - pre-commit: $PRECOMMIT"
echo "  - pre-push: $PREPUSH"
