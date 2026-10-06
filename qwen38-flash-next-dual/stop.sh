#!/usr/bin/env bash
# Qwen3.8-Flash-Next 2 台構成を止める (vendor/qwen38-flash-next-dual/stop.sh: worker → head の順に vllm-fn を消す)。
# レシピは `docker rm -f` (SIGKILL) なので、その前に head → worker の順で `docker stop` して正常終了させる
# (--ipc host のため、SIGKILL だと /dev/shm にセグメントが残る)。
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
recipe="$here/../vendor/qwen38-flash-next-dual"
[[ -f "$recipe/.env" ]] || { echo "[qwen38-flash-next-dual/stop.sh] ERROR: $recipe/.env がない" >&2; exit 1; }
W=$(cd "$recipe" && bash -c 'source .env >/dev/null 2>&1; echo "${WORKER_USER:+$WORKER_USER@}$WORKER_IP"')
docker stop -t 30 vllm-fn >/dev/null 2>&1 || true
ssh -o BatchMode=yes "$W" "docker stop -t 30 vllm-fn >/dev/null 2>&1 || true" || true
cd "$recipe"
exec ./stop.sh "$@"
