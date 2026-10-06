#!/usr/bin/env bash
# Qwen3.8-27B TP=2 (sglang-qwen38-tp2) を両ノードで止めて消す。head (API) → worker の順に docker stop
# (SIGTERM、30 秒で SIGKILL) してから rm。ログは rm で消えるので、残すなら先に docker logs を取ること。
#   qwen38-27b-tp2/stop.sh
set -uo pipefail
NAME=sglang-qwen38-tp2
WORKER="${QWEN38_TP2_WORKER:-ken@10.0.0.2}"
wssh() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$WORKER" "$@"; }

echo "[qwen38-27b-tp2/stop.sh] head: $NAME"
docker stop -t 30 "$NAME" >/dev/null 2>&1 && docker rm "$NAME" >/dev/null 2>&1 && echo "  stopped" ||
  { docker rm -f "$NAME" >/dev/null 2>&1 && echo "  removed" || echo "  not running"; }
echo "[qwen38-27b-tp2/stop.sh] worker ($WORKER): $NAME"
if wssh true; then
  wssh "docker stop -t 30 $NAME >/dev/null 2>&1 && docker rm $NAME >/dev/null 2>&1 && echo '  stopped' ||
        { docker rm -f $NAME >/dev/null 2>&1 && echo '  removed' || echo '  not running'; }"
else
  echo "  ssh できない: worker の $NAME は残っている可能性がある" >&2; exit 1
fi
