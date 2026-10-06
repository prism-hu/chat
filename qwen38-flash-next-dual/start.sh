#!/usr/bin/env bash
# Qwen3.8-Flash-Next 2 台構成 (vendor/qwen38-flash-next-dual, vLLM TP2+EP+MTP3) を起動する。
# 設定は vendor/qwen38-flash-next-dual/.env (submodule の .gitignore で管理外。差分は README.md)。
#
# レシピの start.sh は API を `--host 0.0.0.0` に決め打ちしていて、bind を変える knob が無い
# (EXTRA_VLLM_ARGS は --host 0.0.0.0 より前に入るので負ける)。そこで start.sh を一時ファイルに
# 写し、次の 3 か所だけ置き換えて実行する。submodule には何も書かない。
#   - SCRIPT_DIR を submodule の絶対パスに (写しは /tmp にあるため)
#   - head の `--host 0.0.0.0` → `--host $BIND`
#   - 起動待ちの `http://localhost:$PORT/health` → `http://$BIND:$PORT/health`
# どれかが 1 回ちょうどで一致しなければ (レシピ更新で形が変わったら) 起動せずに止まる。
#
#   qwen38-flash-next-dual/start.sh             # = レシピの ./start.sh --no-download
#   qwen38-flash-next-dual/start.sh --launch    # 重みの確認・同期を飛ばして起動だけ
#   DRY_RUN=1 qwen38-flash-next-dual/start.sh   # 置き換えの diff を見せて終わる (何も起動しない)
# 重みはピン (fc694b5…) を手で入れる前提なので download.sh (main を取りに行く) は常に飛ばす。
# **172.28.0.1:8888 の他のモデル (vllm-qwen38-fn / DeepSeek / GLM) とは排他。**
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
repo=$(cd "$here/.." && pwd)
recipe="$repo/vendor/qwen38-flash-next-dual"

BIND="${QWEN_DUAL_BIND:-172.28.0.1}"
REV=fc694b54fb0174e0913e6adf86691ef85a4ead47
TEMPLATE_SRC="$repo/vendor/qwen38-flash-next/files/chat-template/chat_template.jinja"
TEMPLATE_NAME=prism-chat-template.jinja        # .env の EXTRA_VLLM_ARGS と揃える (/root/.cache/vllm/ に入る)

die()  { echo "[qwen38-flash-next-dual/start.sh] ERROR: $*" >&2; exit 1; }
info() { echo "[qwen38-flash-next-dual/start.sh] $*"; }

[[ -f "$recipe/start.sh" ]] || die "$recipe/start.sh がない (git submodule update --init vendor/qwen38-flash-next-dual)"
[[ -f "$recipe/.env" ]] || die "$recipe/.env がない (README.md の手順で作る)"
# 必要な値だけ .env から読む (レシピも bash で source している)
eval "$(cd "$recipe" && bash -c 'source .env >/dev/null 2>&1
  printf "MODEL_ID=%q PORT=%q WORKER_IP=%q WORKER_USER=%q EXTRA_VLLM_ARGS=%q\n" \
    "$MODEL_ID" "$PORT" "$WORKER_IP" "${WORKER_USER:-}" "${EXTRA_VLLM_ARGS:-}"')"
W="${WORKER_USER:+$WORKER_USER@}$WORKER_IP"
[[ "$EXTRA_VLLM_ARGS" == *"/root/.cache/vllm/$TEMPLATE_NAME"* ]] ||
  die ".env の EXTRA_VLLM_ARGS が --chat-template /root/.cache/vllm/$TEMPLATE_NAME を指していない"

# --- 起動用の写しを作る --------------------------------------------------------------
tmp=$(mktemp /tmp/qwen38-dual-start.XXXXXX.sh)
trap 'rm -f "$tmp"' EXIT
sub() {  # sub <ERE: 元の行> <sed 置換式>: 元の行がちょうど 1 回あることを確かめてから置き換える
  local n; n=$(grep -cE -- "$1" "$tmp" || true)
  [[ "$n" == 1 ]] || die "レシピの start.sh に '$1' が ${n} 回 (1 回のはず)。レシピが変わったので wrapper を直すこと"
  sed -i -E -- "$2" "$tmp"
}
cp "$recipe/start.sh" "$tmp"
sub '^SCRIPT_DIR="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)"$' \
    "s|^SCRIPT_DIR=.*\$|SCRIPT_DIR=\"$recipe\"|"
sub '^    --host 0\.0\.0\.0 \\$' "s|^    --host 0\\.0\\.0\\.0 \\\\\$|    --host $BIND \\\\|"
sub 'http://localhost:\$PORT/health' "s|http://localhost:\\\$PORT/health|http://$BIND:\\\$PORT/health|"

if [[ "${DRY_RUN:-0}" == 1 ]]; then
  info "DRY_RUN=1: レシピの start.sh との差分 (これを bash で --no-download $* 付きで実行する)"
  diff "$recipe/start.sh" "$tmp" || true
  exit 0
fi

# --- 前提の確認 ------------------------------------------------------------------------
# ポート: レシピは確認しないので、他のモデルが居ると 10 分ロードしたあとで bind に失敗する
if ss -ltn "sport = :$PORT" 2>/dev/null | grep -q LISTEN; then
  die "port $PORT は使用中: $(ss -ltnp "sport = :$PORT" 2>/dev/null | tail -n +2)
    vllm-qwen38-fn / DeepSeek / GLM のどれかが動いている。先に止めること"
fi
# bind 先のブリッジ (prism-gw の compose network) があること
ip -o -4 addr show | grep -q " inet $BIND/" || die "$BIND がこのホストに無い (compose の network が無い? docker compose up -d prism-gw)"

# ピン: vLLM は MODEL_ID を HF_HUB_OFFLINE で refs/main から解決するので、refs/main = ピン を両ノードで保証する
# (hf download --revision <sha> は refs/main を書かない。改行入りの refs はレシピが直す)
hub_dir="models--${MODEL_ID//\//--}"
check_ref() {  # check_ref <run-prefix...>: そのノードの refs/main をピンに揃える。違うピンなら止める
  "$@" bash -s "$hub_dir" "$REV" <<'EOS'
d="${HF_HOME:-$HOME/.cache/huggingface}/hub/$1"; rev=$2
[ -d "$d/snapshots/$rev" ] || { echo "missing: $d/snapshots/$rev"; exit 3; }
if [ -f "$d/refs/main" ]; then
  cur=$(tr -d '[:space:]' < "$d/refs/main")
  [ "$cur" = "$rev" ] || { echo "refs/main is $cur, not the pin $rev"; exit 4; }
else
  mkdir -p "$d/refs" && printf %s "$rev" > "$d/refs/main" && echo "wrote $d/refs/main"
fi
EOS
}
out=$(check_ref env) || die "head: $out"
[[ -z "$out" ]] || info "head: $out"
if [[ " $* " != *" --launch "* ]] && ! ssh -o BatchMode=yes "$W" "test -d \"\$HOME/.cache/huggingface/hub/$hub_dir/snapshots/$REV\""; then
  info "worker に $hub_dir/snapshots/${REV:0:8} が無い: レシピが head から rsync する (~124 GiB)"
else
  out=$(check_ref ssh -o BatchMode=yes "$W") || die "worker: $out"
  [[ -z "$out" ]] || info "worker: $out"
fi

# chat template (froggeric v22.5, 既定 reasoning_effort=medium): 両ノードの ~/.cache/vllm に置く
# (レシピが /root/.cache/vllm にマウントする唯一のホスト側ディレクトリ。head の API だけが読む)
[[ -r "$TEMPLATE_SRC" ]] || die "$TEMPLATE_SRC がない (git submodule update --init vendor/qwen38-flash-next)"
mkdir -p "$HOME/.cache/vllm"
install -m 0644 "$TEMPLATE_SRC" "$HOME/.cache/vllm/$TEMPLATE_NAME"
ssh -o BatchMode=yes "$W" "mkdir -p ~/.cache/vllm && cat > ~/.cache/vllm/$TEMPLATE_NAME" < "$TEMPLATE_SRC"

cd "$recipe"
bash "$tmp" --no-download "$@"
