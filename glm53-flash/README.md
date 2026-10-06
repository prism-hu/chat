# GLM-5.3-Flash EXL3 (TensorFold, Spark 2 台)

レシピは submodule `vendor/glm53-flash-exl3`（MiaAI-Lab, v1.8）。ここにあるのは、この 2 台用の値を
環境変数で渡すラッパーだけなので、submodule には何も書かない（`scripts/local.sh` も `.env` も作らない）。

- head = enda-spark（rank 0 + API）、worker = enda-gx10（rank 1, `ken@10.0.0.2`）。CX7 port 0 で直結
- API: `http://172.28.0.1:8888/v1`、モデル ID **`GLM-5.3-Flash-EXL3`**（`/v1/models` の id）
- prism-gw でのクライアント名は `glm-5.3-flash`（`gateway/config.yaml`）
- **172.28.0.1:8888 を使う他のモデル（compose の `vllm-qwen38-fn`、DeepSeek、Qwen の 2 台構成）とは排他**

## 起動・停止

```bash
glm53-flash/start.sh              # 起動。動いていれば何もしない。初回や設定が変わったときは prepare.sh も走る
glm53-flash/start.sh restart      # 設定を変えたあとの再起動 (引数は tensorfold serve に追加で渡る)
DRY_RUN=1 glm53-flash/start.sh    # 各 rank の docker run を表示するだけ。何も止めず、起動しない
glm53-flash/stop.sh               # 両ノードを止める (ログは ~/.cache/tensorfold-glm53/logs/ に gzip で残る)

docker logs -f glm53-flash-tf                 # rank 0
ssh ken@10.0.0.2 docker logs -f glm53-flash-tf  # rank 1
curl -s http://172.28.0.1:8888/health
```

値はどれも実行時に上書きできる（`CONTEXT=524288 glm53-flash/start.sh restart` など）。
`HOST` / `PORT` だけは zsh が `HOST` にホスト名を入れているので、`GLM53_HOST` / `GLM53_PORT` で変える。

## 選んだ値（`env.sh`）

| 変数 | 値 | 理由 |
|---|---|---|
| `WORKER` | `ken@10.0.0.2` | CX7 のアドレスなので、link の検出（`ip route get` → `enp1s0f0np0` / `rocep1s0f0` / GID 5）がそのまま通る。`FABRIC_PEER` は不要 |
| `HOST` / `PORT` | `172.28.0.1` / `8888` | docker bridge の GW だけで listen。prism-gw は届き、LAN と tailscale からは見えない。smoke test と `stop.sh` の `/health` 確認もこのアドレスを使う |
| `WORKER_WEIGHTS` | `copy` | gx10 に自前のコピー（下の手順で手で入れる） |
| `MEMORY_RESERVE_GIB` | `23`（既定 14.5） | 下記 |
| `CONTEXT` | `262144`（既定 1,048,576） | 下記 |
| ピン | 既定のまま | v1.8 の既定が指定のリビジョンと一致（checkpoint `078455ff…`、DFlash2 `bf582e4e…`）。submodule を上げてピンが変わったら `start.sh` が止まる（`env.sh` の `GLM53_EXPECT_*`） |
| NCCL | 自動検出のまま | `nodes.sh` は worker へのルートから netdev・HCA・RoCE v2 GID を引く。この箱では `enp1s0f0np0` / `rocep1s0f0` / 5 になることを確認済み（読み取りのみ）。twin（`enP2p1s0f0np0`）は IPv4 が無いので 1 rail |

**メモリ。** TensorFold は「起動時の MemAvailable − `MEMORY_RESERVE_GIB`」を予算にし、モデルの推定を
引いた残りを KV プールに回す（上限 `KV_POOL_GIB` 12.5）。実際にはピークで推定より 4〜10 GiB 多く使う
（README: 195k プロンプトで ~4、1M プロンプトで ~10）ので、**配信中の空き ≈ reserve − (4〜10)**。
23 なら 13〜19 GiB 残り、technoplasm/vision の ~8 GiB を足しても数 GiB 余る。

**コンテキスト。** 1M の窓は FP8 KV で約 7 GiB（README の数値から 12.5 GiB ≈ 1.87M トークン、
~7 KiB/トークン）。推定は 1M で 88.1 GiB なので 262,144 なら ~82.8 GiB（推算）。起動に要る
MemAvailable は概ね **262k: ~106 GiB / 524k: ~108.5 GiB / 1M: ~111 GiB**（reserve 23 込み）。
spark は 121.7 GiB で、vision が動いていると 1M は確実に入らず 524k も際どいので 262,144 にした。
足りないときは TensorFold が「入る最大の窓」を示して止まり、`start.sh` がその窓で 1 回だけ
起動し直す（ログに出る）。`start.sh` は空きが 110 GiB 未満だと警告するが、止まりはしない。

## gx10 に要るもの

- `~/.cache/huggingface/hub/models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold/`
  （`blobs/` と `snapshots/078455ffe6472f9a52fbc1139f58b9db2881b25c/`、~164 GiB）
- `~/.cache/huggingface/hub/models--incoai--GLM-5.3-Flash-DFlash2/`
  （`blobs/` と `snapshots/bf582e4eacc1810f76656d1811693ff6c6737d2a/`、~2.2 GiB）
- イメージ `tensorfold-glm53:v0.6.0`（~25 GB）、`rsync`、ken が docker group、`~/.cache/huggingface/hub` が書ける

**`prepare.sh` が再コピーしない条件。** worker の `snapshots/<ピン>` を `find -L . -type f -printf '%P %s\n'`
した一覧（ファイル名とリンク先のサイズ）が head と完全に一致すること。一致すればコピーを飛ばす。
イメージは層の diffID と config で比べるので `docker save | docker load` したものはそのまま通る。
head 側の重みは `hf download --revision <ピン>` が毎回走るが、揃っていれば Hub に確認するだけで何も落とさない。

## gx10 へのコピー（CX7 経由）

head のダウンロードが終わってから（`blobs/` に `*.incomplete` が無いこと）。

```bash
H=$HOME/.cache/huggingface/hub
W=ken@10.0.0.2
SSH='ssh -c aes128-gcm@openssh.com -o Compression=no'
for m in models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold:078455ffe6472f9a52fbc1139f58b9db2881b25c \
         models--incoai--GLM-5.3-Flash-DFlash2:bf582e4eacc1810f76656d1811693ff6c6737d2a; do
  d=${m%%:*}; r=${m##*:}
  ssh $W "mkdir -p .cache/huggingface/hub/$d/blobs .cache/huggingface/hub/$d/snapshots"
  # 1) スナップショットが指す blob だけを 8 並列で (-L: xet の blob がさらにリンクのことがある)
  (cd $H/$d && find snapshots/$r -type l -printf '%l\n' | sed 's#^\(\.\./\)*##' | sort -u) |
    xargs -P8 -I{} rsync -a -L --partial -e "$SSH" $H/$d/{} $W:.cache/huggingface/hub/$d/{}
  # 2) スナップショット (相対シンボリックリンク) と refs
  rsync -a -e "$SSH" $H/$d/snapshots/$r $W:.cache/huggingface/hub/$d/snapshots/
  [ -d $H/$d/refs ] && rsync -a -e "$SSH" $H/$d/refs $W:.cache/huggingface/hub/$d/
  # 3) prepare.sh と同じ照合 (何も出なければ OK)
  diff <(cd $H/$d/snapshots/$r && find -L . -type f -printf '%P %s\n' | LC_ALL=C sort) \
       <(ssh $W "cd .cache/huggingface/hub/$d/snapshots/$r && find -L . -type f -printf '%P %s\n' | LC_ALL=C sort") &&
    echo "$d OK"
done

# イメージ (head に無ければ、初回の glm53-flash/start.sh が ghcr から pull して tag する)
docker save tensorfold-glm53:v0.6.0 | ssh ken@10.0.0.2 docker load
```

## 注意

- DFlash2 は CC BY-NC-ND 4.0（非商用のみ）。避けるなら `DRAFTER=mtp`（1 リクエストずつになる）。
- `PARALLEL` は既定の 4 のまま。レシピ既定なら 8 にすると reserve が 14.5 → 19.6 に自動で増えるが、
  ここでは `MEMORY_RESERVE_GIB` を明示しているので増えない。8 にするなら reserve を ~28 に上げること。
- `/v1/models` が `max_model_len` を返すかは未確認。返さなければ `gateway/config.yaml` の
  `glm-5.3-flash` に `context: 262144` を書く（Nucllei のゲージ用）。
