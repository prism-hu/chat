# Qwen3.8-27B を SGLang TP=2 で（Spark 2 台）

compose の `sglang-qwen38`（1 台）と同じイメージ・チェックポイント・DFlash2・サーバ引数を、
enda-spark（rank 0 + API）と enda-gx10（rank 1, `ken@10.0.0.2`）の TP=2 で立てる。
背景は `docs/dgx-spark-tp2.md`（実測 1.4–1.8 倍の報告。**1 台構成と A/B すること**）。

- API: `http://172.28.0.1:8888/v1`、served name **`qwen38`**（1 台構成と同じ）
- prism-gw: 上流 `sglang-qwen38-tp2` / ルート **`qwen3.8-27b-tp2`**（1 台の `qwen3.8-27b` と並べて比べられる）
- コンテナ名は両ノード `sglang-qwen38-tp2`。`--network host --ipc host --device /dev/infiniband`
- **排他**: compose の `sglang-qwen38`（spark のメモリ）と、172.28.0.1:8888 の他のモデル
  （`vllm-qwen38-fn` / DeepSeek / GLM / Flash-Next 2 台）。`start.sh` はどちらかが居ると止まる

## 起動・停止

```bash
qwen38-27b-tp2/start.sh             # worker → head の順に起動し、/health を最大 20 分 (WAIT_TIMEOUT) 待つ
DRY_RUN=1 qwen38-27b-tp2/start.sh   # 両ノードの docker run を表示するだけ
qwen38-27b-tp2/stop.sh              # head → worker の順に docker stop (30 s) して rm

docker logs -f sglang-qwen38-tp2                   # head
ssh ken@10.0.0.2 docker logs -f sglang-qwen38-tp2  # worker
```

タイムアウトやコンテナの停止で失敗すると、両ノードのログ末尾を出して exit 1。コンテナは調べられるよう
残すので、`stop.sh` で片付ける。

## 1 台構成との違い

サーバ引数は compose のコピー（`QWEN38_*` の環境変数で同じように上書きできる）。違いは:

| | 1 台 (compose) | TP=2 |
|---|---|---|
| 並列 | — | `--tp-size=2 --nnodes=2 --node-rank=N --dist-init-addr=10.0.0.1:20000` |
| API | `0.0.0.0:8000`（docker network 内、`sglang-qwen38:8000`） | head `172.28.0.1:8888`（host network。worker は 127.0.0.1:8888 にダミーの死活確認だけ立つので、gx10 に無い 172.28.0.1 を避けて後勝ちで上書き） |
| `--mem-fraction-static` | 0.80 | **0.75**（下記） |
| 重みのマウント | `${MODELS_DIR}` rw | `~/models` を ro |
| NCCL など | — | `SGLANG_HOST_IP`（各ノードの CX7 IP）、`NCCL_SOCKET_IFNAME` / `GLOO_SOCKET_IFNAME` / `TP_SOCKET_IFNAME=enp1s0f0np0`、`NCCL_IB_HCA==rocep1s0f0`、`NCCL_IB_GID_INDEX=5`、`NCCL_MIN/MAX_NCHANNELS=4`、`NCCL_CROSS_NIC=0` |

`cpuset` 5-9,15-19 はそのまま。gx10 も同じく大コア（3.9 GHz）がこの番号なのを確認した。
`shm_size` は `--ipc host` で意味が無いので外した。MTU は両ノードとも 9000（確認済み）。

TP=2 で効かなくなる引数は無い見込み。DFlash2 のドラフト（32 heads / 8 KV heads）も本体（24 / 4）も 2 で割り切れる。
ただし DFLASH + TP=2 + `extra_buffer` の組み合わせはこのイメージでは未確認。

**メモリ。** `--mem-fraction-static` は GPU 総量に対する静的確保（重み + KV プール）の割合で、
TP=2 で重みが半分（~11 GB/ノード）になっても総量は同じで、浮いた分は KV プールに回る。
1 台構成の実測では 0.85 で空き ~11 GB、0.80 で ~16 GB（C16 負荷時 ~12 GB）だった。0.75 なら
~21 GB（負荷時 ~17 GB）の見込みで、technoplasm/vision の ~8 GiB を足しても余裕がある。それでも KV プールは
1 台構成（0.80、~78 万トークン）より大きい。

## gx10 に要るもの（2026-10-07 にコピー済み）

- `/home/ken/models/qwen38-27b-nvfp4/`（22.1 GiB）と `/home/ken/models/qwen38-27b-dflash2-draft/`（3.6 GiB）
  （HF キャッシュではなく平置きのディレクトリ。compose が `MODELS_DIR=/home/ken/models` をマウントしているのと同じ形）
- イメージ `lmsysorg/sglang:dev-qwen38-27b-dflash2`（30.4 GB）。`start.sh` は層の一致で比べる

```bash
W=ken@10.0.0.2
SSH='ssh -c aes128-gcm@openssh.com -o Compression=no'
for d in qwen38-27b-nvfp4 qwen38-27b-dflash2-draft; do
  ssh $W "mkdir -p models/$d"
  # 大きいファイル (safetensors) を 1 本ずつ並列に、残りはまとめて
  (cd ~/models/$d && find . -type f -name '*.safetensors' -printf '%P\n') |
    xargs -P4 -I{} rsync -a --partial -e "$SSH" ~/models/$d/{} $W:models/$d/{}
  rsync -a --partial --exclude='*.safetensors' -e "$SSH" ~/models/$d/ $W:models/$d/
done
# 照合 (何も出なければ OK)
for d in qwen38-27b-nvfp4 qwen38-27b-dflash2-draft; do
  diff <(cd ~/models/$d && find . -type f -printf '%P %s\n' | LC_ALL=C sort) \
       <(ssh $W "cd models/$d && find . -type f -printf '%P %s\n' | LC_ALL=C sort") && echo "$d OK"
done

docker save lmsysorg/sglang:dev-qwen38-27b-dflash2 | ssh -c aes128-gcm@openssh.com ken@10.0.0.2 docker load
```

## 注意

- `docs/dgx-spark-tp2.md` §6 の失敗モード。特に PYNCCL フォールバックで 2 倍遅くなった報告があるので、
  初回は `NCCL_DEBUG=INFO qwen38-27b-tp2/start.sh` で `NET/IB` が `rocep1s0f0` を使っているか見る。
- ファイアウォールは見ていない（`SGLANG_HOST_IP` で制御プレーンは CX7 側に寄せてある）。止まったまま
  ログが出ないときは、両ノードの 10.0.0.0/24 が inbound で許可されているかを疑う。
- ログは `docker rm` で消える。`stop.sh` の前に必要なら `docker logs` を保存する。
