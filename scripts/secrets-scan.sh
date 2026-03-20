#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

run_gitleaks() {
  local -a cmd=("$@")
  if "${cmd[@]}" git --help >/dev/null 2>&1; then
    "${cmd[@]}" git --staged --no-banner --redact
    return 0
  fi
  if "${cmd[@]}" protect --help >/dev/null 2>&1; then
    "${cmd[@]}" protect --staged --no-banner --redact
    return 0
  fi
  return 1
}

run_detect_secrets() {
  local baseline_path=".secrets.baseline"
  local -a staged_files=()

  if [[ ! -f "$baseline_path" ]]; then
    echo "[secrets-scan] detect-secrets baseline not found; skipping detect-secrets scan."
    return 0
  fi

  mapfile -t staged_files < <(git diff --cached --name-only --diff-filter=ACMR)
  if [[ "${#staged_files[@]}" -eq 0 ]]; then
    echo "[secrets-scan] no staged files; skipping detect-secrets scan."
    return 0
  fi

  if ! command -v detect-secrets-hook >/dev/null 2>&1; then
    echo "[secrets-scan] detect-secrets-hook not available; skipping detect-secrets scan."
    return 0
  fi

  detect-secrets-hook --baseline "$baseline_path" "${staged_files[@]}"
}

scan_staged_files_with_fallback() {
  local failed=0
  local file
  local tmp
  local -a patterns=(
    'AKIA[0-9A-Z]{16}'
    'gh[pousr]_[A-Za-z0-9_]{20,}'
    'AIza[0-9A-Za-z\-_]{35}'
    'xox[baprs]-[A-Za-z0-9-]{10,}'
    'sk-[A-Za-z0-9]{20,}'
    '-----BEGIN (RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY-----'
  )

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    if ! git cat-file -e ":$file" 2>/dev/null; then
      continue
    fi

    if ! git show ":$file" | LC_ALL=C grep -Iq .; then
      continue
    fi

    tmp="$(mktemp)"
    git show ":$file" >"$tmp"
    for pattern in "${patterns[@]}"; do
      if rg -n --pcre2 "$pattern" "$tmp" >/tmp/secrets-scan-match.$$ 2>/dev/null; then
        echo "[secrets-scan] potential secret detected in staged file: $file" >&2
        cat /tmp/secrets-scan-match.$$ >&2
        failed=1
        break
      fi
    done
    rm -f "$tmp" /tmp/secrets-scan-match.$$
  done < <(git diff --cached --name-only --diff-filter=ACMR)

  if [[ "$failed" -ne 0 ]]; then
    echo "[secrets-scan] fallback scanner found potential secrets. Unstage or scrub them before committing." >&2
    return 1
  fi

  echo "[secrets-scan] gitleaks unavailable; fallback staged-content scan passed."
}

if command -v gitleaks >/dev/null 2>&1; then
  if ! run_gitleaks gitleaks; then
    scan_staged_files_with_fallback
  fi
elif command -v mise >/dev/null 2>&1; then
  if ! run_gitleaks mise exec -- gitleaks; then
    scan_staged_files_with_fallback
  fi
else
  scan_staged_files_with_fallback
fi

run_detect_secrets
