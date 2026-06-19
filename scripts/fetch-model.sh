#!/usr/bin/env bash
#
# fetch-model.sh — download + prepare the Qwen3.5-122B hybrid INT4+FP8 + MTP
# checkpoint into $MODELS_DIR (default ~/models). Steps 0-2 only; the serving
# image is pulled from GHCR, not built here.
#
# Thin wrapper over the vendored install.sh --model-only so the download/build
# logic stays single-source in vendor/qwen35-spark (no duplication).
#
# Usage: scripts/fetch-model.sh [--no-launch is implied] [other install.sh flags]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT}/vendor/qwen35-spark/install.sh" --model-only "$@"
