#!/usr/bin/env bash
set -euo pipefail

PROJECT_COMMAND="judicial-listings"

SCRIPT_DIR="$(
  CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
  pwd
)"
SKILL_DIR="$(
  CDPATH= cd -- "$SCRIPT_DIR/.." &&
  pwd
)"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: this skill requires uv on PATH" >&2
  exit 127
fi

if [[ -n "${SKILL_RUNTIME_ROOT:-}" ]]; then
  RUNTIME_ROOT="$SKILL_RUNTIME_ROOT"
elif [[ -n "${XDG_CACHE_HOME:-}" ]]; then
  RUNTIME_ROOT="$XDG_CACHE_HOME/codex-skills"
elif [[ -n "${HOME:-}" ]]; then
  RUNTIME_ROOT="$HOME/.cache/codex-skills"
else
  RUNTIME_ROOT="${TMPDIR:-/tmp}/codex-skills"
fi

SKILL_RUNTIME_DIR="$RUNTIME_ROOT/$PROJECT_COMMAND"
mkdir -p "$SKILL_RUNTIME_DIR"

export UV_PROJECT_ENVIRONMENT="$SKILL_RUNTIME_DIR/venv"
export UV_CACHE_DIR="$SKILL_RUNTIME_DIR/uv-cache"

exec uv run \
  --project "$SKILL_DIR" \
  --locked \
  --no-dev \
  "$PROJECT_COMMAND" "$@"
