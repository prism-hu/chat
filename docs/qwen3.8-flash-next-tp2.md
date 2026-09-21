# Qwen3.8-Flash-Next を Spark 2 台（TP=2）で serve する

調査日 **2026-09-22**。質問は「Flash-Next は TP=2 でいけるか」。**答えは「いける。
というより 2 台でないと載らない」。** 1 台構成の Qwen3.8-27B は
[qwen3.8-27b-serving.md](./qwen3.8-27b-serving.md)、TP=2 の一般論は
[dgx-spark-tp2.md](./dgx-spark-tp2.md)。

## TL;DR

**TP=2 は動く。が、やる価値は薄い。** 結論は「**1 台 (TP=1) で回し、2 台目は 27B に使う**」。

- **1 台に載る。** チェックポイント 123.6 GiB のうち **50.0 GiB は PLE (n-gram 埋め込み表)
  が単独ファイル** (`model-fp8-mtp-ple.safetensors`) で入っている。これは行列積ではなく
  疎なルックアップなので **NVMe から mmap できる**。外すと常駐は **73.6 GiB** で、
  121 GiB に KV ごと収まる。
- **TP=2 の実利は約 +10% しかない。** 同一著者・同一エンジン・同一 40 プロンプトで
  PLE をディスクに置いたまま比較すると 1 台 32.5 → 2 台 35.8 tok/s。
  **そこから 53.7 tok/s への跳ね上がりは「PLE をメモリに置けた」効果**であって
  並列化の効果ではない。**買っているのは並列度ではなく 50 GiB のアドレス空間。**
- **TP=2 はちょうど上限。** `num_key_value_heads=2` なので TP=3 も TP=4 も割れない。
  実測でも **TP=4 は TP=2 より遅い** (40.5 対 53.7)。3 台目はこのモデルには効かない。
- **`--kv-cache-dtype fp8` が使えない可能性がある。** 27B で確立したパターンが
  そのまま通らない (下記の矛盾点を参照)。
- 未解決の 2 ノード固有バグが複数あり、**うち 2 件は電源断が必要なホストロック**、
  1 件は**このスタックの用途に直撃する decode 停止**。

## モデルの素性（config.json 実測）

`Qwen/Qwen3.8-Flash-Next` の `config.json` を直接読んだ値。

| param | 値 | TP=2 | TP=3 | TP=4 |
|---|---|---|---|---|
| `num_attention_heads` | 24 | ○ | ○ | ○ |
| **`num_key_value_heads`** | **2** | ○ | **×** | **×** |
| `linear_num_value_heads` (GDN) | 48 | ○ | ○ | ○ |
| `linear_num_key_heads` (GDN) | 16 | ○ | × | ○ |
| `moe_intermediate_size` | 640 | ○ | × | ○ |
| `num_experts` | 512 | ○ | × | ○ |

- `model_type: qwen4_exp` / `Qwen4ExpForConditionalGeneration`
- 48 層、`hidden_size` 2560、**MoE 512 エキスパート中 10 活性**、`full_attention_interval: 4`
  （= 27B と同じく GDN と full attention のハイブリッド）
- native context **262,144**（YaRN で ~1M まで）

**KV ヘッドが 2 しかないので TP=2 が上限。** MoE なので 3 台目を足すなら TP ではなく
EP（エキスパート並列、512 は 2/4/8 で割れる）側で考えることになる。

## チェックポイントとサイズ（HF API 実測）

| repo | サイズ | shards |
|---|---|---|
| `Qwen/Qwen3.8-Flash-Next` (BF16) | 360.0 GB / 335.3 GiB | 131 |
| `Qwen/Qwen3.8-Flash-Next-FP8` | 185.5 GB / 172.8 GiB | 131 |
| `RadixArk/Qwen3.8-Flash-Next-NVFP4` | 135.2 GB / 125.9 GiB | 206 |
| **`nvidia/Qwen3.8-Flash-Next-NVFP4`** | **132.7 GB / 123.6 GiB** | 11 |
| `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4` | 98.6 GiB | 37 |

### 1 台に載るか → **載る**

`nvidia` 版の内訳を HF API で実測した結果:

```
model-fp8-mtp-ple.safetensors      50.0 GiB   ← PLE (n-gram 埋め込み表) が単独ファイル
model-0000N-of-00010.safetensors   73.6 GiB   ← 残り 10 shard の合計
                                  ------
                                  123.6 GiB
```

**PLE は 51B パラメータのルックアップ表**で、行列積ではないので NVMe から mmap する / 
ホストにオフロードするのが現実的。外せば**常駐 73.6 GiB** となり、121 GiB に KV ごと収まる。
1 台構成のレポート (madeye) も *"Model loading uses 76.48 GiB"* と一致する。

**FP8 版 (172.8 GiB) は PLE を外しても 1 台には載らない。**
2 台が本当に要るのは FP8 を使いたいときだけ。

> **NVMe に置く代償**: PLE テーブルは**再起動のたびに書き直しが要る**。
> 古いファイルを消してからなら ~10 分、消さないと ~55 分という報告がある。

## レシピ

**[MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks)**
（[getrefined/...](https://github.com/getrefined/Qwen3.8-Flash-Next-NVFP4-vLLM-DGX-Spark) 派生）。
**vLLM + TP2 + EP + MTP3**。README の数値は動作中のコンテナのログから読んだ実測、と明記されている。

```bash
git clone https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Dual-DGX-Sparks
cd Qwen3.8-Flash-Next-Dual-DGX-Sparks
cp .env.sample .env && vim .env     # IP / IFACE / IB_HCA を埋める
./download.sh                        # head に重みを取得
./start.sh --no-download             # worker へ rsync → パッチ適用 → 起動
```

前提: 2 ノードが ConnectX の RoCE/IB で接続、ノード間パスワードなし SSH、各ノードに
~126GiB の空きディスク。

### 主要な設定値（`.env.sample` の既定）

| 変数 | 既定 | 意味 |
|---|---|---|
| `IMAGE` | `vllm/vllm-openai:qwen38-flash-next` | arm64 マニフェストあり（確認済み） |
| `MODEL_ID` | `nvidia/Qwen3.8-Flash-Next-NVFP4` | BF16 dense + NVFP4 experts + FP8 PLE |
| `TENSOR_PARALLEL_SIZE` | 2 | 1 GPU/Spark × 2 ノード |
| `ENABLE_EXPERT_PARALLEL` | true | **NVFP4 では必須** |
| `MTP_NUM_SPECULATIVE_TOKENS` | 3 | 0 で無効 |
| `KV_CACHE_DTYPE` | `fp8` | bf16 比で 1.70 倍のトークンが載る（下記） |
| `MAMBA_SSM_CACHE_DTYPE` | `bfloat16` | GDN の再帰状態 |
| `GPU_MEMORY_UTILIZATION` | **0.835** | このキットの実測既定 |
| `MAX_MODEL_LEN` | 262144 | YaRN で ~1M（`YARN_ENABLE=true`, factor 4.0） |
| `MAX_NUM_SEQS` | 8 | |
| `IB_GID_INDEX` | 3 | 上がらなければ 5 を試す |

生成される vLLM 引数の要点:

```
--tensor-parallel-size 2 --nnodes 2 --master-addr <HEAD_IP> --master-port 50000
--enable-expert-parallel --all2all-backend allgather_reducescatter
--kv-cache-dtype fp8 --mamba-ssm-cache-dtype bfloat16
--gpu-memory-utilization 0.835 --max-model-len 262144 --max-num-seqs 8
--enable-chunked-prefill --distributed-executor-backend mp
--speculative-config '{"method":"mtp","num_speculative_tokens":3,"use_local_argmax_reduction":true}'
--compilation-config '{"mode":0,"cudagraph_mode":"FULL_DECODE_ONLY"}'
--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder
--load-format safetensors --safetensors-load-strategy lazy
```

## 性能の目安

### 一番効く測定（tonyd615、同一著者・同一エンジン・同一 40 プロンプト）

**PLE をディスクに置いたまま台数だけ変えた比較。これが TP=2 の正味の効果。**

| 構成 | median | prose | peak | 集約@6 | prefill |
|---|---|---|---|---|---|
| 1 Spark（表はディスク） | 32.5 | 21.7 | 43.8 | 194 | ~1,650 |
| 2 Sparks CONTEXT（表はディスク） | **35.8** | — | 50.8 | 234 | — |
| 2 Sparks SPEED（表は常駐） | **53.7** | 37.2 | 63.7 | 309 | 2,784 |
| 4 Sparks TP4+EP | **40.5** | — | 54.2 | 262 | 2,450 |

**32.5 → 35.8 = +10%。これだけが並列化の取り分。** 35.8 → 53.7 は PLE がメモリに
載ったこと。**TP=4 が TP=2 より遅い**のは `num_key_value_heads=2` の帰結で、
上の割り切れ表と整合する。

### 各所の報告（ばらつきが大きい）

| 構成 | エンジン | C1 | 集約 | 出典 |
|---|---|---|---|---|
| 2× NVFP4 | vLLM TP2+EP+MTP3 | 54.4 | 207 @C8 | MiaAI-Lab（自己申告） |
| 2× NVFP4 | vLLM, MTP **off** | 24.5 | — | MiaAI-Lab（MTP = 2.13×, 受理率 72.8%） |
| 2× NVFP4 | SGLang TP2+NEXTN | 62.9–63.7 | 306.6 @6 | beastllama |
| 2× NVFP4 | vLLM | 40–45 | — | oxbyte（NVIDIA フォーラム） |
| 2× FP8 | vLLM TP2+EP | 24.4 | 176.8 @C64 | tsarihan（フォーラム、bf16 KV） |
| **1× NVFP4** | vLLM | 36.8 prose / 45.8 code | 100.2 @4 | madeye |
| 1× Q4 | llama.cpp | 19–24 | — | フォーラム |

**同じ 2 台構成で 37〜64 tok/s と開く。** GitHub のレシピ群は大半が MiaAI-Lab 派生で
独立検証になっていない。独立性が高いのは NVIDIA フォーラムの 2 本で、そちらの数字は
軒並み低い（37〜45）。中立の第三者ベンチ（`kreuzhofer/dgx-manager#91`）は立案されたが
**実行されないまま**。

prefill は 16k〜64k で ~2.96k tok/s とほぼ平坦、128k で 8% 低下（MiaAI-Lab）。

## 資料どうしが食い違っている点（要実機確認）

**どちらが正しいか決められなかった。実機で確かめること。**

| 論点 | MiaAI-Lab のレシピ | 別系統の報告 |
|---|---|---|
| **KV dtype** | `.env.sample` が `KV_CACHE_DTYPE=fp8` を既定にし、README に fp8 で 3,652,200 tok / bf16 で 2,131,159 tok という**実測ログ**を載せている | vLLM が *"Qwen3.8-Flash-Next QSA requires a BF16 main KV cache"* で **fp8 を拒否する**（PR #55557 / #54846 が保留中）という報告 |
| **tool parser** | `--tool-call-parser qwen3_coder` | **`qwen3_xml` が正**で、間違えるとツール呼び出しが**無言で全失敗**する |
| **EP の効果** | `ENABLE_EXPERT_PARALLEL=true` を NVFP4 必須として同梱 | TP2 の上に EP を足すと **−4%**。EP が必須になるのは TP=4 のときだけ |

**27B で確立した `--kv-cache-dtype fp8_e4m3` の型がそのまま通らない可能性がある**のが
一番痛い。KV は最大の確保物なので、bf16 強制なら文脈予算がほぼ半減する。

## 投機デコード

- **ドラフトはチェックポイント内蔵の MTP ヘッドだけ**（4B、NVFP4 版でも BF16 のまま同梱）。
  **Flash-Next 用の DFLASH / DSpark / EAGLE3 チェックポイントは存在しない。**
  27B の DFLASH 構成はそのままでは持ち込めない。
- **TP=2 で動く**（MTP=3 で 2.13×、受理率 72.8%）。27B で問題になった
  vLLM #52480 の TP≥2 ドラフタ破損は**このモデルには当てはまらない**。
- **ただし上限は 3/1/4。** `speculative_num_draft_tokens > 4` はエンジンが拒否する
  （*"Qwen QSA requires speculative_num_draft_tokens ≤ 4"*）。
- 受理率は**プロンプト依存で ±40 ポイント振れる**（コード 94.7% / 散文 5.8%）。
  自分のトラフィックで測らないと意味がない。

## 既知の失敗モード（2 ノード GB10）

**深刻な順。**

1. **統合メモリのページキャッシュで箱が固まる。** safetensors が 135GB を mmap し、
   ページキャッシュと GPU が同じプールを食い合う。**エラーは出ず、電源ボタンも
   効かなくなる。** 複数人が独立に踏んでいる。対策は `posix_fadvise(DONTNEED)` を
   ~20 秒ループで回す常駐（起動のたびに必須）。
2. **TP2 の重みロードで両ノードがホストロック**（未解決）。PLE のホスト pin が
   ~61.5 GiB を unevictable で掴む。**電源断が必要になった報告あり。**
3. **長い prefill が decode を 3〜7 分止める**（vLLM #54919、**未解決**）。集約 decode が
   0.5〜5 tok/s に落ちて戻る。**このスタックの用途（ゲートウェイ越しの長短混在
   エージェント трафик）に直撃する。**
4. **MTP の受理率が突発的に 0% になり、thinking ブロックで max_tokens まで
   繰り返す**（vLLM #55357、未解決）。1 回あたり ~120 万トークンを無駄にし、
   テレメトリが無いと気づけない。
5. **FlashInfer の autotune デッドロックが GPUDirect RDMA 無効のホストで発火する**
   （#52291）。**この箱がまさにそれ。** 回避フラグは **throughput を ~35% 落とす**。
6. **NCCL all-reduce デッドロック**。`NCCL_SOCKET_IFNAME` と `GLOO_SOCKET_IFNAME` の
   明示 pin で直るが、原因の説明が資料間で食い違っている。
7. 既定の 64 チャネルでハング → `NCCL_MIN/MAX_NCHANNELS=4`、`NCCL_CROSS_NIC=0`。
8. **greedy が非決定的**（temperature 0 で 50 問中 13 問が回ごとに変わる）。
9. 起動に **10〜11 分**。

## この箱での実測（2026-09-22、1 台 / TP=1）

`vendor/qwen38-flash-next` のレシピで実際に立てて測った値。**27B を止めてから**起動している。

構成: `Mia-AiLab/Qwen3.8-Flash-Next-NVFP4` (99 GiB) / `vllm/vllm-openai:qwen38-flash-next` /
TP=1 / context 262,144 / **KV bf16** / MTP=3 / GMU 0.786（予算 95.65 GiB）。

| 回 | decode (gw 越し) | TTFT |
|---|---|---|
| 1 | 87.0 | 9.19 s |
| 2 | 42.9 | 2.60 s |
| 3 | 69.4 | 5.57 s |
| 4 | 56.5 | 4.11 s |
| 5 | 55.9 | 4.42 s |
| 6 | 61.4 | 4.97 s |
| 7 | 50.1 | 3.37 s |
| 8 | 45.0 | 1.65 s |

**平均 58.5 tok/s（45–87）。** 同じベンチ・同じゲートウェイ越しで測った
**Qwen3.8-27B の 34–38 tok/s に対しておよそ 1.6 倍**。レシピの自己申告
（48.7 tok/s）も上回っている。SSE 1 イベントあたり 2.6–5.1 トークンで、MTP が
効いている。

起動時の実測:

```
Available KV cache memory: 17.27 GiB
GPU KV cache size: 630,856 tokens
Maximum concurrency for 262,144 tokens per request: 2.41x
```

- **PLE の packed テーブルは 27 GiB**（`~/.cache/vllm`）。初回起動時に構築され、以後再利用。
- 起動は約 12 分。ホストの使用メモリは 106 GiB、空き 15 GiB。
- `/v1/models` の `max_model_len: 262144` は **prism-gw 越しでも保持される**。

### KV を bf16 にした理由

レシピ自身が `KV_CACHE_DTYPE=fp8` について警告する:

> FP8 KV is a CAPACITY TRADE, not a free win. ... a long-reasoning benchmark
> falling from **6/6 to 2/6**. This is sparse attention: quantised keys perturb
> which blocks the indexer selects.

fp8 なら KV プールは約 1.85 倍（1M 文脈が射程に入る）。**「有力モデルを 1 つだけ
動かして最大化する」方針では容量より品質を取る**と判断して `auto`（bf16）にした。
容量が要るときは `.env` の `KV_CACHE_DTYPE` を `fp8` に戻すだけ。

なお TP=2 の調査時に「vLLM が BF16 KV を強制する」という報告と「レシピが fp8 の
実測ログを載せている」という食い違いがあったが、**この 1 台構成では fp8 も選べる**
（品質コスト付き）ことが分かったので、その点は決着した。

### 運用上の注意: `start.sh` が起動まで到達しない

`./start.sh` は **Step 6: Launch で何も起動せず exit 0 で終わる**（2 回再現）。
PLE の構築までは正常に進み、`docker run` コマンドも正しく生成されるのに、
スクリプト内から実行されない。回避策:

```bash
cd vendor/qwen38-flash-next
./start.sh --no-launch > /tmp/cmd.log 2>&1        # コマンドを生成させる
sed -e 's/\x1b\[[0-9;]*m//g' /tmp/cmd.log | sed -n '/^docker run/,$p' > /tmp/run.sh
docker rm -f vllm-fn-tp1 2>/dev/null; bash /tmp/run.sh
```

停止は `docker stop vllm-fn-tp1`（レシピの `./stop.sh` は watchdog も止める）。
**`--ipc host` なので、SIGKILL で落とすと `/dev/shm` にセグメントが残る。**

## 判断

**2 台 TP=2 を常用構成にはしない。** 理由は単純で、**並列化の取り分が +10% しかなく、
残りは「PLE をメモリに置けた」だけ**だから。同じ効果は 1 台 + NVMe で 36〜45 tok/s
として得られる。その差のために、障害ドメインを倍にし、未解決バグを 4 件抱え、
FP8 KV を諦め、35% の autotune ペナルティを飲み、2 台目を 27B に使えなくする。

**推奨する形:**

- `nvidia/Qwen3.8-Flash-Next-NVFP4` を **1 台・TP=1・PLE は NVMe・MTP=3** で回す
- **2 台目は Qwen3.8-27B に使う**（今動いている構成をそのまま残す）
- vLLM #54919 が閉じ、BF16 KV 制限（#55557 / #54846）が外れたら再検討する

TP=2 を試すなら**メンテナンス枠の実験として**やること。電源断が必要なホストロックの
報告が 2 件ある。

## 未確認

- 本ドキュメントは一次資料の読み取りのみで、**実機では一切回していない**。
- 上の「食い違っている点」3 件はどちらが正しいか決められなかった。
- 素の `lmsysorg/sglang:qwen38flashnext` が sm_121 で**パッチ無しに動くか**。
  SGLang のレシピはすべて SM121 QSA の Triton パッチを当てている。
- vLLM が prism-gw の要件（`/v1/models` の `max_model_len`、streaming の累積 usage）を
  満たすか。SGLang は 27B で満たすことを確認済みだが、vLLM は未確認。
- SGLang が Spark で vLLM より速いのが本物か、計測手法の差か。SGLang の `/metrics` は
  窓付きゲージと積算カウンタを混ぜると **~15% 過大**になる罠が指摘されている。
