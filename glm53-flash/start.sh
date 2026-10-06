#!/usr/bin/env bash
# GLM-5.3-Flash EXL3 を enda-spark (rank 0 + API) と enda-gx10 (rank 1) で起動する。
# 中身は vendor/glm53-flash-exl3/start.sh に env.sh の値を渡すだけ。引数もそのまま渡る。
#   glm53-flash/start.sh                 # 起動 (動いていれば何もしない)
#   glm53-flash/start.sh restart         # 設定を変えたあとの再起動
#   DRY_RUN=1 glm53-flash/start.sh       # 各 rank の docker run を表示するだけ
# **172.28.0.1:8888 の他のモデル (vllm-qwen38-fn / DeepSeek / Qwen 2 台構成) とは排他。**
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
recipe=$(cd "$here/../vendor/glm53-flash-exl3" && pwd)
source "$here/env.sh"

die() { echo "[glm53-flash/start.sh] ERROR: $*" >&2; exit 1; }
[[ -x "$recipe/start.sh" ]] || die "$recipe/start.sh がない (git submodule update --init vendor/glm53-flash-exl3)"

# submodule 側の local.sh / .env は環境より弱いが、ここに無い値を黙って足すので知らせる
for f in scripts/local.sh .env; do
  [[ ! -e "$recipe/$f" ]] || echo "[glm53-flash/start.sh] WARN: $recipe/$f がある。env.sh に無い値はそこから入る" >&2
done

# ピンの確認 (config.sh をそのまま読む。環境変数で MODEL_REVISION 等を渡していればそれも反映される)
pins=$(cd "$recipe" && bash -c 'source scripts/config.sh >/dev/null; echo "$MODEL_ID@$MODEL_REVISION $DFLASH2_ID@$DFLASH2_REVISION"')
[[ "$pins" == "$GLM53_EXPECT_MODEL $GLM53_EXPECT_DRAFTER" ]] ||
  die "レシピのピンが変わった: $pins
    期待: $GLM53_EXPECT_MODEL $GLM53_EXPECT_DRAFTER
    gx10 のコピーを入れ直してから glm53-flash/env.sh の GLM53_EXPECT_* を更新すること (README.md)"

cd "$recipe"
exec ./start.sh "$@"
