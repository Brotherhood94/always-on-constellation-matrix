#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
if ! command -v uv >/dev/null; then
    printf 'Install uv first from https://docs.astral.sh/uv/getting-started/installation/ then open this launcher again.\n'
    exit 1
fi
exec uv run --locked --no-project --script ./connect-plan.py
