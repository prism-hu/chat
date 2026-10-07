# Spark 2 台構成（enda-spark + enda-gx10）を立てた記録

作業日 **2026-10-06〜07**。[dgx-spark-tp2.md](./dgx-spark-tp2.md)（机上調査）と
[qwen3.8-flash-next-tp2.md](./qwen3.8-flash-next-tp2.md) の続きで、実機で 2 台にしたときの
配線・事故・決めた値・比較結果をまとめる。各モデルの起動手順と値の詳細は起動ラッパーの README
（`glm53-flash/` `deepseek-v4-flash/` `qwen38-flash-next-dual/` `qwen38-27b-tp2/`）。

## TL;DR

- **常用は GLM-5.3-Flash（2 台、思考 low）。** 23 問 237 s・打ち切り 0。`glm53-flash/start.sh`、
  prism-gw が `reasoning_effort: low` を既定で補う。
- **DeepSeek-V4-Flash は正確さで一番だが 5 倍遅い**（同 1,293 s）。`low` にしても短くならない。
- **TP=2 の効き方は dense と MoE で全く違う。** 27B（dense）は decode ×1.79、
  Flash-Next（MoE）は ×1.15（問ごとの比の中央値）。ただし Flash-Next 2 台は
  **nvidia 版チェックポイントで品質がはっきり上がった**（難読語 4/10 → 9/10）。
- **事故 3 つ:** (1) CX7 の片方向が 13.5 Gb/s に落ちた → enda-spark 再起動で直った（原因不明）。
  (2) GB10 のページキャッシュで DeepSeek の worker がロード中に固まる → 起動前にキャッシュを捨てる。
  (3) Flash-Next 用の vm sysctl が MemAvailable を ~9 GiB 食って DeepSeek が起動できない → 全体から外した。
- **大学の回線は 1〜2 分ごとに TLS が切れる。** 大きいファイルは `scripts/hf-resumable.py` で落とす。

## 1. ハードウェア

| | enda-spark | enda-gx10 |
|---|---|---|
| 機種 | NVIDIA DGX Spark (GB10) | ASUS Ascent GX10 (GB10) |
| 役割 | head（rank 0 + API）。chat スタック一式もここ | worker（rank 1）。デスクトップセッションあり |
| 統合メモリ（`free`） | **121.7 GiB** | **119.6 GiB**（小さい方。KV はこちらで決まる） |
| ディスク | 3.7 TB（空き 2.2 TB） | 916 GB（**空き 162 GB**、2026-10-08） |
| CX7 IP | 10.0.0.1/24 | 10.0.0.2/24 |
| タイムゾーン | JST | **UTC**（gx10 側のログ・`uptime` は UTC で読む） |

GPU には technoplasm の `compute.jobs`（282 MiB）が spark 側に常駐している。レシピの
「GPU が空いているか」チェックは `REQUIRE_IDLE_GPU=false` 等で外している。

## 2. CX7 直結

- QSFP ケーブル 1 本で **両ノードとも port 0** 同士（`enp1s0f0np0` / RDMA デバイス `rocep1s0f0`）。
  リンク 200 Gb/s（`ethtool`）。
- GB10 は CX7 の 1 ポートを **PCIe x4 の 2 function** に分けて見せる
  （`0000:01:00.x` と `0002:01:00.x`）。port 0 なら `enp1s0f0np0` と twin の `enP2p1s0f0np0`。
  **IPv4 を振ったのは前者だけ**なので、使っているのは 1 rail。
- **実測 `ib_write_bw` 109 Gb/s（両方向）**、1 function のみ。机上調査の ~197 Gb/s は 2 function を
  束ねた値と見られる（twin にも IPv4 を振れば 2 rail にできるが sudo が要り、未実施）。
- RoCE v2 の IPv4 GID は **両ノードとも index 5**。index 3 は IPv6 リンクローカルの RoCE v2 なので、
  レシピ既定の `GID_INDEX=3` では合わない（`/sys/class/infiniband/rocep1s0f0/ports/1/gids/` で確認）。
- MTU 9000、NetworkManager の接続名は両ノード **`cx7`**（Netplan ではなく nmcli）。

gx10 側で実行したコマンド（履歴より）:

```bash
ssh -t ken@enda-gx10 'sudo nmcli con mod "Wired connection 4" connection.id cx7 ipv4.method manual \
  ipv4.addresses 10.0.0.2/24 802-3-ethernet.mtu 9000 && sudo nmcli con up cx7'
```

enda-spark 側は `nmtui` で同じ値（`cx7`、10.0.0.1/24、manual、MTU 9000、`enp1s0f0np0`）を入れた。

確認:

```bash
nmcli -g ipv4.addresses,802-3-ethernet.mtu,connection.interface-name con show cx7
rdma link                                 # rocep1s0f0/1 ACTIVE, netdev enp1s0f0np0
cat /sys/class/infiniband/rocep1s0f0/ports/1/gids/5   # ...ffff:0a00:0001 (spark) / 0a00:0002 (gx10)
# 帯域: 片側で ib_write_bw -d rocep1s0f0 -x 5 --report_gbits、もう片側で同じ引数 + 相手の IP
```

**ssh head → worker は鍵で通ること**（全レシピの前提。`ssh -o BatchMode=yes ken@10.0.0.2 true`）。
再起動後に agent 転送の鍵が使えなくなったので、enda-spark の `~/.ssh/id_rsa.pub` を gx10 の
`~/.ssh/authorized_keys` に入れた。

## 3. 事故と原因

### 3.1 spark → gx10 の RDMA が 13.5 Gb/s に落ちた（原因不明）

2026-10-06 16:30〜17:15 頃。ケーブルを差し替えた後、**spark → gx10 だけ ~13.5 Gb/s**、
gx10 → spark は 109 Gb/s のまま。

- **どのポートの組み合わせでも同じ**（spark 側の `cx7` を一時 `enp1s0f1np1` に付け替えても）。
- エラー / PFC / CNP カウンタは動かない。
- 効かなかったもの: ケーブルの挿し直し、gx10 の再起動（17:02）、spark の mlx5 ドライバ再ロード
  （17:05、`modprobe -r mlx5_ib mlx5_fwctl mlx5_core && modprobe mlx5_core && modprobe mlx5_ib`。
  **`mlx5_ib` が載らないまま**になった）。
- **enda-spark の再起動（17:13）で直った。** port 0 ↔ port 0 で両方向 109 Gb/s に戻った。

原因は分かっていない。同じ症状が出たら、まず spark の再起動を試す。

### 3.2 GB10 のページキャッシュで DeepSeek の worker が固まる

DeepSeek の worker（gx10）が重みのロード中に **GPU 78,100 MiB で止まり、ログが進まない**。
2026-10-06 23:18（util 0.80）、23:38（0.80）、10-07 04:40（0.82）の 3 回。どの回も gx10 の
ページキャッシュに他のモデルのファイルが数十 GiB 載っていた。

- 最初は 0.80 のせいだと思い 0.82 に戻したら 23:47 は通ったので「util の問題」と誤認していた。
  04:40 に 0.82 でも固まって、キャッシュが共通因子だと分かった。
- `posix_fadvise(DONTNEED)` でモデルファイルのキャッシュを捨ててから起動すると、毎回通った
  （04:52 以降）。sudo 不要。`deepseek-v4-flash/drop-model-cache.py`、
  `deepseek-v4-flash/start.sh` が両ノードで先に実行する。
- 止めてすぐ起動しても固まったことがある。止めたら両ノードの `MemAvailable` が戻るのを待つ。

他のモデル（GLM / Flash-Next 2 台 / 27B TP=2）では起きていないが、同じ統合メモリなので
**どのモデルでも起動前に両ノードで `drop-model-cache.py` を流すのが無難**。

### 3.3 vm sysctl が MemAvailable を ~9 GiB 減らしていた

enda-spark に Flash-Next 用の `/etc/sysctl.d/90-spark-gpu.conf` が入っていた
（`vm.min_free_kbytes=4194304`、`vm.watermark_scale_factor=300`、`vm.swappiness=30`。
中身はレシピの `vendor/qwen38-flash-next/files/sysctl-spark3.conf` と同じ 3 値。
ファイル名がどの手順由来かは未確認）。watermark が上がる分 **MemAvailable が ~9 GiB 低く出る**。

- DeepSeek util 0.78 で head の起動チェックが落ちた:
  `Free memory on device cuda:0 (90.51/121.69 GiB) on startup is less than desired GPU memory utilization (0.78, 94.92 GiB)`。
- **ken 判断（2026-10-06 22:56）: 全体から外して既定値に戻す。**

```bash
sudo rm /etc/sysctl.d/90-spark-gpu.conf && \
  sudo sysctl -w vm.min_free_kbytes=45166 vm.watermark_scale_factor=10 vm.swappiness=60
```

gx10 にも入っていない（2026-10-08 確認、両ノード既定値）。1 台構成の Flash-Next（compose の
`vllm-qwen38-fn`）は sysctl に頼らず固定の予算（GMU 0.728、cgroup 93g、`HOST_RESERVE_GIB=33`）で動く。
README と compose のコメントにはまだ「起動前に `sysctl -p files/sysctl-spark3.conf`」とある
（再起動で消える一時適用）。適用したまま 2 台構成を上げると同じ問題になるので、上のコマンドで戻す。

### 3.4 codex/* が Cloudflare 403

ホスト再起動後、`codex/*` が全部 403（Cloudflare のチャレンジページ）。litellm が起動時に GitHub から
取るモデル表の取得が DNS より先に走って失敗し、同梱の古い表に gpt-5.5 / gpt-5.6-* が無いため
`/backend-api/codex/chat/completions`（存在しない）を叩いていた。`chatgpt/responses/<model>` で
Responses API に固定した（cb60f8f、`litellm/config.yaml` のコメント）。

### 3.5 大学の回線で大きいファイルが落とせない

HF / ghcr のダウンロードが **1〜2 分ごとに TLS `bad record mac`**
（`SSL: DECRYPTION_FAILED_OR_BAD_RECORD_MAC`）で切れる。

- 配線を疑って `netwatch.sh` で見張った（2026-10-06 18:23〜21:56、DeepSeek の取得中）:
  毎秒ルータに ping、10 秒ごとに NIC カウンタ。**失敗 184 回、平均 ~70 s に 1 回。**
  rx/tx エラーは全期間 0、ping 損失は 20:24 の 1 回（12 秒、carrier 2→4 のリンク断）だけで、
  それ以外の失敗の直前 120 s はどれも損失 0。**手元の配線ではなく上流（学内 or 先方）の問題。**
  （判定スクリプトは `rx_mac_missed` の増加でも "LOCAL" と出していたが、これは破棄カウンタでエラーではない）
- 今の `hf` CLI / huggingface_hub は、失敗したファイルを**新しいランダム名の `.incomplete` で最初から
  やり直す**（resume しない）。~3 GiB を超えるファイルは事実上終わらない。
  - GLM（shard ≤ 2.0 GiB）は `hf download` のリトライで 213 回目に完了（22:51 → 01:35）。
  - DeepSeek（shard ≤ 3.4 GiB）は DSpark イメージ内の古い huggingface_hub（名前で resume する）で 185 回目に完了（3.5 時間）。
  - nvidia Flash-Next（PLE 単独ファイル 50 GiB）は無理なので **`scripts/hf-resumable.py`** を書いた。
    blob ごとに固定名の `.part` へ `curl -C -` で継ぎ足し、sha256（LFS）とサイズを確かめ、HF キャッシュの
    形（`blobs/` + `snapshots/<rev>/` の相対リンク + `refs/main`）に置く。123.6 GiB を 53 分、curl の切断 138 回で完了。

```bash
python3 scripts/hf-resumable.py nvidia/Qwen3.8-Flash-Next-NVFP4 fc694b54fb0174e0913e6adf86691ef85a4ead47
```

## 4. メモリの決め方（2 台構成）

**KV は小さい方のノード（gx10、119.6 GiB）で決まる。** head（spark）は vision 等の同居分
（technoplasm/vision ~8 GiB）を残す必要がある。

| モデル | 値 | 実測（起動ログ） |
|---|---|---|
| DeepSeek | `GPU_MEMORY_UTILIZATION_TEXT=0.82` | 重み 80.04 GiB/rank。KV spark 14.81 / gx10 12.64 GiB → **1,877,175 tok、1M 窓で 1.79 本**。0.74 は gx10 KV 3.96 GiB で 524k に 5.47 GiB 足りず起動失敗 |
| GLM | `MEMORY_RESERVE_GIB=23`、`CONTEXT=262144` | 起動推定 83.15 GiB（予算 91.46）、ピーク ~88 GiB/GPU。プール 1,130,496 tok（4 並列 × 262k） |
| Flash-Next 2 台 | `GPU_MEMORY_UTILIZATION=0.67`、KV bf16 | 重み 64.57 GiB/rank、KV 14.75 GiB → **861,108 tok（262k の 3.28 本）** |
| 27B TP=2 | `--mem-fraction-static 0.75` | 起動後 spark の MemAvailable 16.3〜19.4 GiB。起動 256〜277 s |

## 5. モデルごとの 2 台構成（要約）

詳細・手順は各ディレクトリの README。どれも submodule は無改変で、ラッパーが値を渡す。
**全部 `172.28.0.1:8888` に立つので互いに排他**（加えて compose の `vllm-qwen38-fn` とも）。

| モデル | ラッパー | エンジン / 重み | 量子化 | prism-gw 名 | 起動時間 |
|---|---|---|---|---|---|
| **GLM-5.3-Flash** | `glm53-flash/` | TensorFold v0.6.0 / `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold@078455ff` + DFlash2 `incoai/...@bf582e4e` | EXL3 4bpw、KV fp8 | `glm-5.3-flash` | ~2 分 |
| DeepSeek-V4-Flash Vision-Exp | `deepseek-v4-flash/` | vLLM DSpark（`ghcr.io/anemll/dspark-vllm-gx10:0.1.1`）/ `deepseek-ai/DeepSeek-V4-Flash-Vision-Exp`（156.3 GiB）、MTP 6 | NVFP4、KV `nvfp4_ds_mla` | `deepseek-v4-flash` | ~6 分 |
| Flash-Next 2 台 | `qwen38-flash-next-dual/` | vLLM TP2+EP+MTP3 / `nvidia/Qwen3.8-Flash-Next-NVFP4@fc694b54` | BF16 dense + NVFP4 experts + FP8 PLE | `qwen3.8-flash-next`（1 台版と共用） | ~13 分 |
| 27B TP=2 | `qwen38-27b-tp2/` | SGLang `dev-qwen38-27b-dflash2` / `~/models/qwen38-27b-nvfp4` + DFlash2 | NVFP4、KV fp8 | `qwen3.8-27b-tp2` | ~4.5 分 |

- 1 台版の Flash-Next は `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4`（MXFP8 dense）。2 台版の nvidia 版は
  dense が BF16 のまま、experts だけ NVFP4。量子化が軽い分、品質の差になった（§6）。
- API の bind は全部 `172.28.0.1`（docker bridge GW）。prism-gw だけが届き、LAN / tailscale には出ない
  （vLLM / TensorFold に API キーは無い）。Flash-Next 2 台はレシピが `0.0.0.0` 決め打ちなのでラッパーが置換する。
- TensorFold の `/v1/models` は `max_model_len` を返さないので、gateway の `glm-5.3-flash` に
  `context: 262144` を書いてある。

### 思考の強さ（reasoning_effort）の既定

| モデル | テンプレートの既定 | 取れる値 | ここでの既定 |
|---|---|---|---|
| GLM-5.3-Flash | **max**（止まらないことがある。5 問で 1 打ち切り） | low / high / max | **low**（prism-gw の `defaults`） |
| Qwen3.8-27B | **xhigh** | xhigh / medium / low | テンプレート既定のまま（xhigh） |
| DeepSeek V4 | — | low / high / max（**medium は無い**） | `DEFAULT_THINKING=high`。`low` でも思考は短くならない（§6） |
| Flash-Next（1 台・2 台） | 同梱は xhigh | — | froggeric 版テンプレートで **medium** |

prism-gw の `defaults`（1c607d7）は、クライアントが送らなかったキーだけを補う。送られた値はそのまま通る。

## 6. 比較評価（2026-10-07）

`evals/`（23 問: 日本語 6・知識 5・推論 3・コード 2・エージェント 5・画像 2）。全行は
[`evals/results/compare-20261007-0521.md`](../evals/results/compare-20261007-0521.md)。中央値。

| 実行 | decode tok/s | 思考 s | 回答まで s | 合計 s | 打ち切り | spec accept / len |
|---|---:|---:|---:|---:|---|---|
| DeepSeek（high） | 40.3 | 23.7 | 13.2 | 1,293 | 2 | 29.2% / 2.75 |
| DeepSeek（low） | 41.0 | 28.6 | 29.3 | 1,336 | 1 | 31.3% / 2.88 |
| Flash-Next 1 台（Mia） | 42.6 | 17.4 | 11.6 | 968 | 0 | 53.6% / 2.61 |
| Flash-Next 2 台（nvidia） | 50.6 | 10.7 | 9.2 | 782 | 0 | 56.9% / 2.71 |
| GLM（high） | 52.4 | 1.5 | 1.6 | 456 | 1 | 65.5% / 2.43 |
| **GLM（low）** | 53.5 | 0 | 0.5 | **237** | **0** | 65.7% / 2.26 |
| 27B 1 台（xhigh） | 28.6 | 87.8 | 62.7 | 2,655 | 4 | – / 3.70 |
| 27B TP=2（xhigh） | 48.3 | 31.0 | 21.7 | 1,774 | 6 | – / 3.66 |
| 27B TP=2（medium） | 50.5 | 14.2 | 11.6 | 781 | 1 | – / 3.81 |

GLM の既定（max）は 5 問だけの部分実行（`glm-5.3-flash-...-max-default-partial`）で、思考 27 s・1 打ち切り。

**読み取り:**

- **GLM low が速さ・安定・品質のバランスで最良**（→ 常用）。エージェント 5 問とも期待どおりの
  ツール呼び出し・確認・拒否。
- **DeepSeek は日本語の語彙・医療知識で一番正確**（難読語 10/10）。ただし合計 5.5 倍遅い。
  `low` は思考も合計も短くならない（1,293 → 1,336 s）ので、使うなら high のまま。
- **Flash-Next は 2 台（nvidia 版）で品質がはっきり上がった**。難読語（`ja_kanji`）は 1 台版
  4/10（ぼんれい・ずつのう・ごうか・いれかえ・うんうん 等）→ 2 台版 9/10（えんか のみ誤り）。
  速度は decode ×1.15（問ごとの比の中央値、1.01〜1.42）、合計 968 → 782 s。
- **27B は TP=2 で decode ×1.79**（問ごとの比の中央値、1.23〜2.57）。机上調査の 1.4〜1.8 倍と合う。
  ただし既定の xhigh は考えすぎて 6 問打ち切り。medium で 1 問に減り合計 781 s。品質は難読語 4/10。
- **MoE は TP=2 で伸びない、dense は伸びる。** Flash-Next（10/512 experts 活性）は 1 トークンで読む
  重みが少なく、帯域を 2 台に割る取り分が小さい。
- **投機デコードの受理率は日本語とそれ以外でほぼ同じ**（どのモデルも差は数ポイント）。
  Flash-Next 1 台で日本語の draft 語彙が効いたのは、同梱の 47k 語彙が日本語の 32% しか覆わない
  穴を埋めたから（[qwen3.8-flash-next-tp2.md](./qwen3.8-flash-next-tp2.md)）。DeepSeek / GLM の
  ドラフタは全語彙なので、同じ手は無い。

## 7. 運用メモ

- **ポート 8888 は排他。** 上げる前に `ss -ltnp 'sport = :8888'` と
  `ssh ken@10.0.0.2 docker ps` で何も居ないことを見る。各ラッパーの stop で止める。
- **起動前にページキャッシュを捨てる**（§3.2）。
  `python3 -I deepseek-v4-flash/drop-model-cache.py`（両ノード）。DeepSeek の `start.sh` は自動。
- **gx10 へのコピーは CX7 経由**、`ssh -c aes128-gcm@openssh.com -o Compression=no` で rsync を 8 並列
  （ssh 1 本は CPU 1 コアで ~55 MB/s 止まり）。手順は各ラッパーの README。
  イメージは `docker save <image> | ssh ken@10.0.0.2 docker load`。
- **重みの置き場所**（2026-10-08、`du`）:

| 重み | spark | gx10 |
|---|---|---|
| `models--deepseek-ai--DeepSeek-V4-Flash-Vision-Exp` | 157 G | 157 G |
| `models--Mia-AiLab--GLM-5.3-Flash-EXL3-4bpw-TensorFold` + `incoai--GLM-5.3-Flash-DFlash2` | 164 G + 2.2 G | 164 G + 2.2 G |
| `models--nvidia--Qwen3.8-Flash-Next-NVFP4` | 124 G | 124 G |
| `models--Mia-AiLab--Qwen3.8-Flash-Next-NVFP4`（1 台版）+ PLE packed table `~/.cache/vllm` | 99 G + 27 G | — |
| `~/models/qwen38-27b-nvfp4` + `qwen38-27b-dflash2-draft` | 23 G + 3.6 G | 23 G + 3.6 G |
| `~/models/step37-flash-iq4xs` | 105 G | — |

  gx10 のイメージ: `lmsysorg/sglang:dev-qwen38-27b-dflash2` 30.4 GB、`tensorfold-glm53:v0.6.0` 24.6 GB、
  `vllm/vllm-openai:qwen38-flash-next` 20.6 GB、タグ無し 18.8 GB（DSpark イメージと見られる）。
  DeepSeek の worker 用チェックアウトは gx10 の `~/deepseek-v4-flash-dspark-worker`。
- **gx10 のディスクは空き 162 GB（82% 使用）。** もう 1 モデル入れるなら何かを消す。
- ログ: GLM は停止時に両ノード `~/.cache/tensorfold-glm53/logs/` に gzip。27B TP=2 は `docker rm` で
  消える。作業ログは `~/.cache/dspark-logs/`（リポジトリ外）。

## 8. 未解決

- **§3.1 の片方向 13.5 Gb/s の原因。** spark 再起動で直ったが、何が残っていたのかは不明。
- **2 本目の rail。** twin function（`enP2p1s0f0np0`）に IPv4 を振れば 2 rail にできるはずだが未実施（sudo）。
  効果も未測定（GLM の all-gather 以外は 1 rail で律速していない可能性がある）。
- **ページキャッシュで固まる仕組み。** 回避策（捨ててから起動）は確実に効いたが、なぜ 78,100 MiB で
  止まるのかは調べていない。util 0.80 の 2 回もキャッシュが原因と見ている（util を変えずに捨てた後は通った）。
- **gx10 のディスク**（空き 162 GB）。
- **利用者側のモデル指定。** fugu / LLENS がまだ `qwen3.8-flash-next`（今は動いていない）や
  撤去済みの `qwen3.5-122b-custom` を指していないか未確認（fugu の既定モデルは volume 内の設定で、
  リポジトリの `config.yaml.example` は `qwen3.5-122b-custom` のまま）。
- 1 台版 Flash-Next の起動前 `sysctl -p`（README / compose のコメント）を、§3.3 の判断に合わせて
  外すかどうか。
- `vendor/deepseek-v4-flash-dspark/.env.dspark`（管理外）の末尾コメントは「0.80」と書いたままで、
  値（0.82）・ラッパー README と食い違っている。値は 0.82 が正。
