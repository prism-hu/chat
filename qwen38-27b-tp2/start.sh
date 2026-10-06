#!/usr/bin/env bash
# Qwen3.8-27B (NVFP4 + DFlash2) を SGLang TP=2 で enda-spark (rank 0 + API) と enda-gx10 (rank 1) に立てる。
# サーバ引数は compose の sglang-qwen38 と同じ (served name も qwen38)。違うのは TP=2 の 4 引数と、
# host network で API を 172.28.0.1:8888 (ローカル大物の排他ポート) に出すことだけ。
#   qwen38-27b-tp2/start.sh            # worker → head の順に起動し、/health を最大 20 分待つ
#   DRY_RUN=1 qwen38-27b-tp2/start.sh  # 両ノードの docker run を表示するだけ
# **compose の sglang-qwen38 (spark のメモリ)、および 172.28.0.1:8888 の他のモデル
# (vllm-qwen38-fn / DeepSeek / GLM / Qwen Flash-Next 2 台) とは排他。**
set -euo pipefail

NAME=sglang-qwen38-tp2
IMAGE="${SGLANG_QWEN38_IMAGE:-lmsysorg/sglang:dev-qwen38-27b-dflash2}"
WORKER="${QWEN38_TP2_WORKER:-ken@10.0.0.2}"
HEAD_IP=10.0.0.1                 # CX7 port 0 (enp1s0f0np0)
WORKER_IP=10.0.0.2
IFACE=enp1s0f0np0                # 両ノード同名
HCA=rocep1s0f0                   # 両ノード同名
GID=5                            # 両ノードとも RoCE v2 IPv4 は index 5
BIND=172.28.0.1                  # docker bridge GW。prism-gw だけが届く
PORT=8888
DIST_PORT=20000
MODELS_DIR="${MODELS_DIR:-$HOME/models}"   # 両ノード同じパス (worker は ken の $HOME/models)
WAIT_TIMEOUT="${WAIT_TIMEOUT:-1200}"       # /health を待つ秒数 (1 台構成で起動 ~5 分。重み読み + graph capture)
# spark に ~20 GiB 残す。1 台構成の実測: 0.85 で空き ~11 GB、0.80 で ~16 GB (C16 負荷で ~12)。
# TP=2 でも静的確保 (重み + KV プール) の総量は fraction で決まるので、0.75 で ~21 GB (負荷時 ~17) の見込み。
MEM_FRACTION="${QWEN38_MEM_FRACTION:-0.75}"

die()  { echo "[qwen38-27b-tp2/start.sh] ERROR: $*" >&2; exit 1; }
info() { echo "[qwen38-27b-tp2/start.sh] $*"; }
wssh() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$WORKER" "$@"; }
DRY=0; [[ "${DRY_RUN:-0}" == 1 ]] && DRY=1

# --- compose の sglang-qwen38 と同じサーバ引数 (コメントの理由は docker-compose.yml を参照) ---
SERVER_ARGS=(
  python3 -m sglang.launch_server
  --model-path="${QWEN38_MODEL_PATH:-/models/qwen38-27b-nvfp4}"
  --served-model-name=qwen38
  --context-length="${QWEN38_CONTEXT_LENGTH:-262144}"
  --kv-cache-dtype=fp8_e4m3
  --max-running-requests="${QWEN38_MAX_RUNNING:-16}"
  --max-mamba-cache-size="${QWEN38_MAMBA_CACHE:-80}"
  --mem-fraction-static="$MEM_FRACTION"
  --chunked-prefill-size=8192
  --speculative-algorithm="${QWEN38_SPEC_ALGO:-DFLASH}"
  --speculative-draft-model-path="${QWEN38_DRAFT_PATH:-/models/qwen38-27b-dflash2-draft}"
  --speculative-num-draft-tokens="${QWEN38_SPEC_DRAFT:-8}"
  --mamba-radix-cache-strategy=extra_buffer
  --enable-cache-report
  --reasoning-parser=qwen3
  --tool-call-parser=qwen3_coder
  --enable-metrics
  # ここから TP=2
  --tp-size=2 --nnodes=2 --dist-init-addr="$HEAD_IP:$DIST_PORT"
  --host="$BIND" --port="$PORT"
)

# docker run (両ノード共通)。node_ip は SGLANG_HOST_IP (ZMQ 制御プレーンを fabric 側に固定する)
docker_run() {  # docker_run <node_ip> <node_rank> [追加の server 引数...]
  local a=(docker run -d --name "$NAME"
    --gpus all --network host --ipc host --device /dev/infiniband
    --cap-add IPC_LOCK --ulimit memlock=-1 --ulimit stack=67108864
    --cpuset-cpus 5-9,15-19          # 両ノードとも大コア (3.9 GHz) はここ
    -e TZ="${TZ:-Asia/Tokyo}"
    -e SGLANG_HOST_IP="$1"
    -e NCCL_SOCKET_IFNAME="$IFACE" -e GLOO_SOCKET_IFNAME="$IFACE" -e TP_SOCKET_IFNAME="$IFACE"
    -e NCCL_IB_DISABLE=0 -e NCCL_IB_HCA="=$HCA" -e NCCL_IB_GID_INDEX="$GID"
    -e NCCL_MIN_NCHANNELS=4 -e NCCL_MAX_NCHANNELS=4 -e NCCL_CROSS_NIC=0
    ${NCCL_DEBUG:+-e NCCL_DEBUG=$NCCL_DEBUG}
    -v "$MODELS_DIR:/models:ro"
    -v "$HOME/.cache/huggingface:/root/.cache/huggingface"
    "$IMAGE" "${SERVER_ARGS[@]}" --node-rank="$2" "${@:3}")
  printf '%q ' "${a[@]}"
}
# rank 1 も --host:--port に死活確認用のダミー HTTP を立てる (engine.py の launch_dummy_health_check_server)。
# gx10 には 172.28.0.1 が無いので、worker だけ 127.0.0.1 に寄せる (後勝ち)。
# worker の $HOME も /home/ken なので -v のパスはそのまま通る (重みの有無は下で確かめる)
WORKER_CMD=$(docker_run "$WORKER_IP" 1 --host=127.0.0.1)
HEAD_CMD=$(docker_run "$HEAD_IP" 0)

if (( DRY )); then
  info "DRY_RUN=1: worker ($WORKER):"; echo "$WORKER_CMD"
  info "DRY_RUN=1: head:"; echo "$HEAD_CMD"
  exit 0
fi

# --- 前提の確認 ---------------------------------------------------------------------
docker ps --format '{{.Names}}' | grep -qx sglang-qwen38 &&
  die "compose の sglang-qwen38 が動いている (spark のメモリが足りない)。先に docker compose stop sglang-qwen38"
if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
  die "port $PORT は使用中: $(ss -ltnp "sport = :$PORT" 2>/dev/null | tail -n +2)
    vllm-qwen38-fn / DeepSeek / GLM / Flash-Next 2 台のどれかが動いている。先に止めること"
fi
ip -o -4 addr show | grep -q " inet $BIND/" || die "$BIND がこのホストに無い (compose の network が無い? docker compose up -d prism-gw)"
wssh true || die "ssh $WORKER に鍵で入れない"
for p in "${QWEN38_MODEL_PATH:-/models/qwen38-27b-nvfp4}" "${QWEN38_DRAFT_PATH:-/models/qwen38-27b-dflash2-draft}"; do
  hp="$MODELS_DIR/${p#/models/}"
  [[ -f "$hp/config.json" ]] || die "head に $hp が無い"
  wssh "test -f '$hp/config.json'" || die "worker に $hp が無い (README.md のコピー手順)"
done
# イメージは中身 (層の diffID) で比べる。.Id は image store によって違うことがある
ident='{{.RootFS.Layers}}'
h=$(docker image inspect -f "$ident" "$IMAGE" 2>/dev/null) || die "head にイメージ $IMAGE が無い"
w=$(wssh "docker image inspect -f '$ident' '$IMAGE'" 2>/dev/null) ||
  die "worker にイメージ $IMAGE が無い: docker save $IMAGE | ssh $WORKER docker load"
[[ "$h" == "$w" ]] || die "head と worker の $IMAGE が違う: docker save $IMAGE | ssh $WORKER docker load"
for n in "$WORKER" here; do
  if [[ $n == here ]]; then c=$(docker ps -a --format '{{.Names}}' | grep -x "$NAME" || true)
  else c=$(wssh "docker ps -a --format '{{.Names}}' | grep -x '$NAME'" || true); fi
  [[ -z "$c" ]] || die "$NAME が既にある ($n)。先に qwen38-27b-tp2/stop.sh"
done

# --- 起動: worker (rank 1) → head (rank 0) -----------------------------------------
info "worker (rank 1) を起動: $WORKER"
wssh "$WORKER_CMD" >/dev/null
info "head (rank 0) を起動"
eval "$HEAD_CMD" >/dev/null

info "http://$BIND:$PORT/health を最大 ${WAIT_TIMEOUT}s 待つ (ログ: docker logs -f $NAME / ssh $WORKER docker logs -f $NAME)"
start=$SECONDS
fail() {
  echo; echo "===== head ($NAME) の末尾 =====" >&2; docker logs --tail 60 "$NAME" >&2 2>&1 || true
  echo "===== worker ($NAME) の末尾 =====" >&2; wssh "docker logs --tail 60 $NAME" >&2 2>&1 || true
  die "$1
    コンテナは残してある (調べたら qwen38-27b-tp2/stop.sh)"
}
while :; do
  code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://$BIND:$PORT/health" || true)
  [[ "$code" == 200 ]] && break
  [[ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" == true ]] || fail "head のコンテナが止まった"
  [[ "$(wssh "docker inspect -f '{{.State.Running}}' $NAME" 2>/dev/null)" == true ]] || fail "worker のコンテナが止まった"
  (( SECONDS - start < WAIT_TIMEOUT )) || fail "${WAIT_TIMEOUT}s 待っても /health が 200 にならない (最後: $code)"
  if (( (SECONDS - start) % 60 < 10 )); then
    info "待機中 $((SECONDS - start))s / MemAvailable here $(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo) GiB"
  fi
  sleep 10
done
info "LIVE: http://$BIND:$PORT/v1 (model: qwen38) — $((SECONDS - start))s。prism-gw では qwen3.8-27b-tp2"
info "MemAvailable here: $(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo) GiB"
