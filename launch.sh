#!/bin/bash
REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
cd "$REPO_ROOT"
exec ./scripts/launch.sh "$@"
