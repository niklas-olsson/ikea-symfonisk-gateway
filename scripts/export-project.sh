#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/export-project.sh [--ref <git-ref>] [--name <label>]

Creates a sanitized project export from a committed git snapshot (default: HEAD),
writes it into .exports/ (gitignored), and produces a timestamped zip.

Examples:
  scripts/export-project.sh
  scripts/export-project.sh --ref HEAD~1
  scripts/export-project.sh --name shareable
EOF
}

ref="HEAD"
name_suffix=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref)
      [[ $# -ge 2 ]] || {
        echo "error: --ref requires a value" >&2
        exit 1
      }
      ref="$2"
      shift 2
      ;;
    --name)
      [[ $# -ge 2 ]] || {
        echo "error: --name requires a value" >&2
        exit 1
      }
      name_suffix="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

command -v git >/dev/null 2>&1 || {
  echo "error: git is required" >&2
  exit 1
}
command -v tar >/dev/null 2>&1 || {
  echo "error: tar is required" >&2
  exit 1
}
command -v zip >/dev/null 2>&1 || {
  echo "error: zip is required" >&2
  exit 1
}

repo_root="$(git rev-parse --show-toplevel)"
project_name="$(basename "$repo_root")"
commit_sha="$(git -C "$repo_root" rev-parse --verify "${ref}^{commit}")"
commit_short="$(git -C "$repo_root" rev-parse --short "${ref}^{commit}")"
timestamp="$(date '+%Y%m%d-%H%M%S')"

safe_suffix=""
if [[ -n "$name_suffix" ]]; then
  safe_suffix="-$(printf '%s' "$name_suffix" | tr -cs 'A-Za-z0-9._-' '-')"
fi

exports_root="$repo_root/.exports"
run_name="${project_name}-export-${timestamp}-${commit_short}${safe_suffix}"
run_dir="$exports_root/$run_name"
staging_dir="$run_dir/$project_name"
zip_path="$exports_root/${run_name}.zip"

mkdir -p "$staging_dir"

git -C "$repo_root" archive --format=tar "$commit_sha" | tar -xf - -C "$staging_dir"

find "$staging_dir" -type f \
  \( -name '.env' -o -name '.env.*' -o -name '*.local' \) \
  ! -name '.env.example' \
  -delete

find "$staging_dir" -type f \
  \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' -o -name '*.sqlite-wal' -o -name '*.sqlite-shm' \) \
  -delete

find "$staging_dir" -type f \
  \( -name '*.pem' -o -name '*.key' -o -name '*.p12' -o -name '*.pfx' -o -name 'id_rsa' -o -name 'id_ed25519' \) \
  -delete

find "$staging_dir" -type f -name 'gitleaks-report*.json' -delete

cat >"$run_dir/EXPORT_INFO.txt" <<EOF
Project: $project_name
Source ref: $ref
Source commit: $commit_sha
Export created: $(date)
Export root: $run_dir
Zip path: $zip_path

Notes:
- Export is generated from committed snapshot ($commit_short), not working tree files.
- Common local secrets/runtime paths are removed after archive extraction.
EOF

(
  cd "$run_dir"
  zip -qry "$zip_path" "$project_name" EXPORT_INFO.txt
)

echo "Created sanitized export from commit: $commit_short"
echo "Folder: $run_dir"
echo "Zip:    $zip_path"
