#!/usr/bin/env bash
# DeepSeek-V4-Flash Vision-Exp を 2 台 (enda-spark head + enda-gx10 worker) で起動する。
# レシピの start を呼ぶ前に、両ノードでモデルファイルのページキャッシュを捨てる
# (溜まっていると worker が重みのロード中 78,100 MiB で固まる。README.md)。
# **172.28.0.1:8888 の他のモデルとは排他。**
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
recipe=$(cd "$here/../vendor/deepseek-v4-flash-dspark" && pwd)
python3 -I "$here/drop-model-cache.py"
scp -q "$here/drop-model-cache.py" ken@10.0.0.2:/tmp/drop-model-cache.py
ssh -o BatchMode=yes ken@10.0.0.2 'python3 -I /tmp/drop-model-cache.py'
cd "$recipe"
exec ./start-deepseek-v4-flash-dspark.sh "$@"
