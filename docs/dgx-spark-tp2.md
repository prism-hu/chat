# DGX Spark を 2 台にして TP=2 にする（GB10 クラスタ）

調査日 **2026-09-22**。[qwen3.8-27b-serving.md](./qwen3.8-27b-serving.md) の続き。
「Qwen3.8-27B は 1 台に収まるのに 2 台にする意味があるのか」に答える。

## TL;DR

**意味はある。実測 1.4–1.8 倍。** GB10 は容量ではなく**メモリ帯域**が律速なので、
1 台に収まるモデルでも重みの読み出しを 2 台に割ると速くなる。

ただし **TP=2 にするとエンジンの優劣が 1 台のときより開く**。SGLang は Ray 不要で
`--nnodes` を足すだけ、vLLM は Ray 必須のうえ **MTP ドラフタが TP≥2 で壊れる既知バグ**がある。
1 台の時点で SGLang を選んでおくのが、そのまま TP=2 への最短経路になる。

## 1. このモデルは TP=2 / TP=4 で綺麗に割れる

`config.json` から実際に確認した。パディング等の小細工は不要。

| パラメータ | 値 | TP=2 | TP=4 |
|---|---|---|---|
| `num_attention_heads` | 24 | 12 | 6 |
| `num_key_value_heads` | **4** | 2 | 1 |
| `linear_num_value_heads` (GDN) | 48 | 24 | 12 |
| `linear_num_key_heads` (GDN) | 16 | 8 | 4 |
| `intermediate_size` | 17408 | 8704 | 4352 |
| vision `num_heads` | 16 | 8 | 4 |

**KV ヘッドが 4 なので TP=4 が上限**（TP=8 は KV 複製が必要）。
他所の DGX Spark レシピで TP=3 のときヘッド数をパディングしている例があるが、TP=2/4 なら不要。
**チェックポイントを変える必要も、再量子化も要らない。**

## 2. 相互接続の実力

### 公式サポート範囲

- **2〜3 台まではスイッチレス直結が NVIDIA 公式サポート**（4 台以上はスイッチ必須）。
- NVIDIA Sync の **Cluster Assistant** が inter-device SSH とネットワークまで自動構成する。
  ただし **NCCL / vLLM / PyTorch のオーケストレーションは対象外**（そこは自分でやる）。
- ケーブルは **QSFP112 DAC / 400GbE / Ethernet-mode-only** 指定
  （Amphenol `NJAAKK-N911`、Luxshare `LMTQF022-SD-R`）。ポート 0 同士を繋ぐ。
- Cluster Assistant の合格閾値は **184 Gbit/s**。

### 実効帯域は公称の 4 割

| 測定 | 値 |
|---|---|
| 理論値 200GbE | 25 GB/s |
| 生 RDMA (`ib_write_bw`) | ~24.6 GB/s (~197 Gb/s) |
| **NCCL all-reduce** | **~10.2 GB/s** |
| NCCL send/recv | ~9 GB/s |

原因は NCCL ログの `use ring PXN 0 GDR 0`（**GDR 0** に注目） — **GPUDirect RDMA が無効**で、
テンソルが一度システムメモリを経由する。**設計は 9–10 GB/s 前提で。**

### 通信予算の試算（実測ではなく計算）

hidden 5120 / 64 層 / 層あたり all-reduce 2 回 で **1.28 MB/token**。

- 帯域だけなら 10.2 GB/s で **7,800 tok/s 相当** → 全く律速しない。
- all-reduce 1 回の遅延を 20 / 40 / 80 µs と仮定すると上限は **391 / 195 / 98 tok/s**。

目標の 20–80 tok/s より十分上なので、理屈の上でも TP=2 は成立する。
※ GB10 の RoCE の point-to-point レイテンシ実測値は見つけられなかったので、これは仮定計算。

## 3. 実測スケーリング（効く）

同一モデル・同一構成の 1x vs 2x 比較（SGLang + NVFP4）:

| ワークロード | 1× Spark | 2× Spark TP=2 | 倍率 |
|---|---|---|---|
| コード生成（DFlash2） | 52–61 tok/s | 76–87 tok/s | 1.44–1.55× |
| 散文 | 24–26 tok/s | 38–41 tok/s | ~1.6× |
| 集約 @C8 | — | 116 tok/s | — |

TP 段階スケーリング（SGLang + NVFP4 + DSpark, C1）:

| | TP=1 | TP=2 | TP=4 |
|---|---|---|---|
| tok/s | 36.6 | **51.8** | 77.3 |

参考（別モデル・投機なし、Qwen3.6-27B-NVFP4 + vLLM）: TP1 12.63 → TP2 22.57（**1.79×**）→ TP4 33.11。

**TP=4 は単一ユーザ最速だが高並行で破綻する。** TP=2+DP=2 は単一ユーザ速度を約 29% 犠牲にする代わりに
C32 を捌ける、という報告がある。

### 最大のリスク: PYNCCL フォールバック

2 ノード Spark は **MNNVL multicast 非対応**のため、`NCCL_SYMM_MEM` / `QUICK_REDUCE` / FlashInfer の
高速 all-reduce 経路が全部無効化される。PYNCCL に落ちると **逆に 2 倍遅くなった**報告がある
（103 → 216 ms/step）。**導入時は必ず 1 台構成と A/B すること。**

## 4. TP=2 でエンジンの優劣が開く

### SGLang: Ray 不要

```bash
# node 0 (head)
python3 -m sglang.launch_server --model-path <ckpt> \
  --tp-size 2 --nnodes 2 --node-rank 0 --dist-init-addr <head-fabric-ip>:20000 ...

# node 1 (worker)
python3 -m sglang.launch_server --model-path <ckpt> \
  --tp-size 2 --nnodes 2 --node-rank 1 --dist-init-addr <head-fabric-ip>:20000 ...
```

投機デコード（NEXTN / MTP / DFlash2）も TP=2 で動作報告あり。

### vLLM: Ray 必須 + MTP が壊れる

マルチノードは Ray がデフォルトランタイム（公式ドキュメント明記）。`run_cluster.sh` で
head/worker を立ててから head でのみ `vllm serve --tensor-parallel-size 2`。

**致命的**: vLLM issue **#52480** — `qwen3_5_mtp` ドラフタが `--tensor-parallel-size >= 2` で
weight shape mismatch により読み込み失敗。Qwen3.8-27B は Qwen3.5 系ハイブリッドを共有するため、
**vLLM の唯一の速度レバーである MTP が TP=2 で失われる可能性が高い**。
加えて #37754（FlashInfer + MTP が sm_121 で illegal memory access → Triton バックエンド必須）。

## 5. TP=2 でメモリがどう増えるか

このモデルは **64 層中 16 層だけが KV を持つ**（`full_attention_interval: 4`）。計算すると:

| KV dtype | per token | 262,144 ctx | 1M ctx |
|---|---|---|---|
| **fp8** | **32.0 KB** | **8.0 GiB/seq** | 30.5 GiB/seq |
| bf16 | 64.0 KB | 16.0 GiB/seq | 61.0 GiB/seq |

（MiaAI-Lab の実測「約 32.8 KB/token、1M ≈ 33GB」と一致するので、この式は信頼できる）

1 台の ~75GB プールで 262K を 8〜9 本、または 1M を 2 本。TP=2 で KV は KV ヘッド単位（4→2）に
分割されプールは概ね倍。**ただし増えるのは 16 層分だけ**なので、フルアテンション型モデルほど
容量メリットは大きくない。**TP=2 の価値は容量ではなく速度。**

## 6. 失敗モード チェックリスト

1. **`NCCL_IB_HCA` 未設定 → 無出力のままデッドロック**（sm_121 で最頻の無言故障）
2. **MTU 9000 必須** — 未設定だと NCCL ウォームアップで GPU 96% のままハング。
   `nmcli` で設定する（**Netplan は DGX Spark で黙って効かない**）
3. **Docker の bridge ネットワークは NCCL を全断** → `--network host --ipc=host`、`--device /dev/infiniband`
4. **`NCCL_IB_GID_INDEX=3` をハードコードしない** — 同一機種でも GID テーブル配置が違う個体がある。
   `NCCL_IB_ROCE_VERSION_NUM=2` に任せる
5. **デフォルト拒否ファイアウォール → SGLang の ZMQ 制御プレーンがデッドロック**。
   `SGLANG_HOST_IP=<fabric IP>` で fabric 側に固定し、peer からの in を許可
6. `NCCL_MIN_NCHANNELS=NCCL_MAX_NCHANNELS=4`（自動交渉の 64 でハング実績）、`NCCL_CROSS_NIC=0`
7. **Ray の OOM killer が統合メモリで worker を殺す** → `RAY_memory_monitor_refresh_ms=0`、
   object store を 4GiB に上限設定（既定だと GPU 予算を食う）
8. **ページキャッシュが統合メモリで GPU テンソルと競合** → ロード前に drop_caches
9. **再起動前に両ランクで Ray/NCCL を完全停止**。残骸が次回起動を固める
10. `gpu-memory-utilization` / `mem-fraction-static` は 0.84 前後（0.90 は OOM 実績）
11. CUDA graph の batch-size ラダーは**連続**にする（穴があると投機デコード下で wedge する）

参考: vLLM / TensorRT-LLM 双方に影響する **NCCL all-reduce デッドロック**の
NVIDIA フォーラムスレッド（#366127）は修正済みか未確認。

## 7. 今（1 台のうちに）決めておくこと

1. **SGLang を選ぶ。** Ray 依存が無いので TP=2 移行が
   `--tp-size / --nnodes / --node-rank / --dist-init-addr` の追加だけで済む。
2. **チェックポイントはそのままで良い**（ヘッド数が TP=2/4 とも割り切れる）。
3. **ケーブルを先に確保する。** QSFP112 DAC / 400GbE / Ethernet-mode-only の指定品。
   汎用 200G ケーブルだと Cluster Assistant の 184 Gb/s を通らない可能性がある。
4. **起動設定を env 化しておく。** `TP_SIZE` / `CONTEXT_LENGTH` / `MEM_FRACTION_STATIC` /
   `MAX_MAMBA_CACHE_SIZE`（同時実行数×4）を外出ししておけば、TP=2 では値を差し替えるだけ。
5. **TP=2 と DP=2 の両方を測る。** 1 台に収まるモデルなので、
   **独立した 2 レプリカを prism-gw で振り分ける（DP）**選択肢も有効:
   - **TP=2** → 単一ストリーム遅延が改善（1.4–1.8×）。NCCL リスクを負う。
   - **DP=2** → 集約スループットが倍。NCCL リスクゼロ、片肺運転も可能。
   - ふぐのようなエージェント用途は**単一ストリーム遅延**が効くので TP=2 寄り。ただし実測で決める。

## 8. 参考にした 2 台構成レシピ

| リポジトリ | エンジン | 対象モデル | 備考 |
|---|---|---|---|
| `beastllama/dgx-spark-qwen38-flash-next-recipe` | SGLang TP=2 | Qwen3.8-Flash-Next (180B) | ファイアウォールデッドロックの `SGLANG_HOST_IP` 修正、ページキャッシュ warden |
| `MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks` | vLLM TP=2 + MTP | 同上 | 54.4 tok/s @C1 / 207 @C8 |
| `MiaAI-Lab/GLM-5.3-Flash-NVFP4-Dual-DGX-Spark` | vLLM + Ray TP=2 | GLM-5.3-Flash | **Ray TP=2 のリファレンス実装**。CX7 NIC/IB HCA pinning |
| `MiaAI-Lab/sparkring` | vLLM | — | スイッチレス用の独自 RDMA collective（SIRCL）。ALPHA |
| `MiaAI-Lab/sparkDash` | — | — | 複数 Spark の監視ダッシュボード |

**注意**: これらの 2 台構成レシピは**すべて 1 台に収まらない大型 MoE 向け**であって、
Qwen3.8-27B（dense、1 台に収まる）の TP=2 レシピは現時点で存在しない。
上記の実測値は NVIDIA フォーラムのスレッドに依拠している。

## 9. 未検証

- 2 ノード間の point-to-point レイテンシ（µs）実測値。
- PYNCCL フォールバックを確実に回避する条件。
- NCCL デッドロックスレッド #366127 の修正状況。
- Qwen3.8-27B の 1x vs 2x 実測はフォーラム 2 スレッドに依拠しており、第三者再現は未確認。

## 追記（2026-10-07）: 実機で確かめたこと

2 台目（ASUS GX10）を入れて実測した。詳細は [two-sparks-2026-10.md](./two-sparks-2026-10.md)。

- **27B の TP=2 は decode ×1.79**（SGLang + DFlash2、23 問の問ごとの比の中央値、1.23〜2.57）。
  §3 の 1.4–1.8 倍を裏付けた。PYNCCL フォールバックによる減速は出ていない。
- **RDMA は 1 function で 109 Gb/s**（`ib_write_bw`、両方向）。GB10 は CX7 の 1 ポートを PCIe x4 の
  2 function に分けて見せ、IPv4 を振ったのは片方だけ。§2 の ~197 Gb/s は 2 function 分と見られる。
- **§6-4 の GID はこの 2 台では index 5**（index 3 は IPv6 リンクローカルの RoCE v2）。レシピ既定の 3 では
  合わない。こちらのラッパーは両ノードで確かめたうえで 5 を書いている（個体ごとに要確認、は変わらない）。
- **§6-8 のページキャッシュは実際に踏んだ。** DeepSeek の worker がロード中に固まり、
  `posix_fadvise(DONTNEED)` で捨ててから起動すると通った（drop_caches の sudo は不要）。
- MoE（Flash-Next）は TP=2 で ×1.15 しか伸びなかった。§7-5 の「TP=2 寄り」は dense の話。
