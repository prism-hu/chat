#!/usr/bin/env bash
# GLM-5.3-Flash EXL3 を両ノードで止める (vendor/glm53-flash-exl3/stop.sh に env.sh の値を渡すだけ)。
# 各 rank のログは止める前に ~/.cache/tensorfold-glm53/logs/ に gzip で保存される (両ノード)。
#   glm53-flash/stop.sh            /   DRY_RUN=1 glm53-flash/stop.sh   # 何を止めるかの表示だけ
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
source "$here/env.sh"
cd "$here/../vendor/glm53-flash-exl3"
exec ./stop.sh "$@"
