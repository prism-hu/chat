#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
cd "$here/../vendor/deepseek-v4-flash-dspark"
exec ./stop-deepseek-v4-flash-dspark.sh "$@"
