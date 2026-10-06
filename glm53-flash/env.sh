# GLM-5.3-Flash EXL3 (vendor/glm53-flash-exl3, TensorFold) をこの 2 台で動かすための値。
# start.sh / stop.sh が source する。レシピの config.sh は「環境変数 > scripts/local.sh > .env > 既定」
# の順なので、ここで export すれば submodule には何も書かずに済む。
# どれも実行時に上書きできる (例: CONTEXT=524288 glm53-flash/start.sh restart)。理由は README.md。

# worker (rank 1) の ssh 先。CX7 のアドレスなので link の検出もそのまま通る (FABRIC_PEER 不要)
export WORKER="${WORKER:-ken@10.0.0.2}"
# API は docker bridge の GW だけで listen する (prism-gw が届き、LAN / tailscale からは見えない)。
# HOST は zsh が自分のホスト名を入れている変数なので、環境の値は使わず固定 (変えるなら GLM53_HOST)
export HOST="${GLM53_HOST:-172.28.0.1}"
export PORT="${GLM53_PORT:-8888}"
# worker は自前のコピーを持つ (rsync で手で入れる。prepare.sh は manifest が一致すればコピーしない)
export WORKER_WEIGHTS="${WORKER_WEIGHTS:-copy}"
# 起動時の MemAvailable からこれだけ残して TensorFold が予算を組む。ピークでは推定より数 GiB〜10 GiB
# 余分に使うので、配信中の空きはおおよそ 23 - (4〜10) = 13〜19 GiB (technoplasm/vision の ~8 GiB 用)
export MEMORY_RESERVE_GIB="${MEMORY_RESERVE_GIB:-23}"
# 1 リクエストの窓。1M (既定) は reserve 23 だと起動時 ~111 GiB の空きが要り、spark では足りない
export CONTEXT="${CONTEXT:-262144}"
# DRY_RUN=1 で docker run を表示するだけ (何も止めず、起動しない)
export DRY_RUN="${DRY_RUN:-0}"

# レシピ v1.8 の既定と同じピン。submodule を上げてピンが変わったら止める (gx10 のコピーが古いまま動くのを防ぐ)
GLM53_EXPECT_MODEL="Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold@078455ffe6472f9a52fbc1139f58b9db2881b25c"
GLM53_EXPECT_DRAFTER="incoai/GLM-5.3-Flash-DFlash2@bf582e4eacc1810f76656d1811693ff6c6737d2a"
