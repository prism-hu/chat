# Qwen3.8-27B をこの箱で serve する（DGX Spark / GB10）

調査日 **2026-09-22**。対象は **Qwen3.8-27B**（27B dense VLM / vision encoder 付き / 262,144 native ctx）を
OpenAI 互換エンドポイントとして DGX Spark 1 台で常時稼働させること。TP=2 に増設する場合は
[dgx-spark-tp2.md](./dgx-spark-tp2.md) を見る。

## TL;DR

**SGLang + NVFP4 + DFlash2 を使う。vLLM から乗り換える価値がある。**

理由は「SGLang の方が最適化すると速い」だけではなく、**上流 vLLM の arm64 リリースイメージが
sm_121 を明示ターゲットにしていない**という構造的な問題がある（後述）。実測でも同一チェックポイントで
SGLang+DFlash2 が vLLM+MTP の約 2.5 倍出ている。

## 前提（この箱の事実）

| 項目 | 値 |
|---|---|
| チップ | GB10 (Grace Blackwell), compute capability **12.1 / sm_121** |
| アーキ | **aarch64 / arm64** Linux |
| メモリ | **128GB 統合メモリ**（CPU/GPU 共有、OS からは 121GiB） |
| 実効メモリ帯域 | 公称 273 GB/s、**実測 triad 200–207 GB/s** |
| 要件 | KV cache 量子化（FP8）と最大コンテキスト長を**制御できること**（Ollama を却下した理由） |
| 要件 | OpenAI 互換 `/v1`。`/v1/models` に `max_model_len`、streaming に累積 usage |

## なぜ SGLang か（コンテナを直接検証した結果）

Docker Registry の manifest と config blob を直接読んで、各イメージのビルドターゲットを確認した。

| イメージ | arch | CUDA | `TORCH_CUDA_ARCH_LIST` | ビルド日 |
|---|---|---|---|---|
| `vllm/vllm-openai:v0.26.0`（現行） | arm64 | 13.0.2 | `8.0 8.7 8.9 9.0 10.0 11.0 12.0` — **12.1 なし** | — |
| `vllm/vllm-openai:v0.29.0`（最新） | arm64 | 13.0.2 | `8.0 8.7 8.9 9.0 10.0 11.0 12.0` — **12.1 なし** | — |
| `vllm/vllm-openai:qwen38` | arm64 | 13.0.1 | `8.7 8.9 9.0 10.0+PTX 12.0 12.1` ← **12.1 あり** | 2026-08-11 |
| `vllm/vllm-openai:cu130-nightly-aarch64` | arm64 | 13.0.1 | `… 12.0 12.1`（12.1 あり）だが **2026-04-23 ビルドで陳腐化** | 2026-04-23 |
| `lmsysorg/sglang:qwen38-27b` | **arm64** | 13.0.3 | SGLang 公式 CI ビルド | 2026-08 |
| `lmsysorg/sglang:dev-qwen38-27b-dflash2` | **arm64** | 13.0.3 | SGLang 公式 CI ビルド | 2026-08 |

ここから分かること:

1. **今 v0.26.0 が動いているのは sm_120 → sm_121 の Blackwell ファミリ互換のおかげ**であって、
   sm_121 向けにビルドされているからではない。
2. 明示的に 12.1 を含む「生きている」vLLM arm64 イメージは `:qwen38` だけ。しかもその
   `VLLM_IMAGE_TAG` は `inferactinc/dev:wtn-3a09141-arm64-cu13.0.1` — **サードパーティ（Inferact）の
   フォークビルド**が公式リポジトリに push されたもので、commit `3a09141` は v0.28 より前。
3. つまり **vLLM では「sm_121 明示ビルド」と「DFlash2（v0.28.0 以降が必要）」を同時に満たす
   既製イメージが存在しない**。ソースビルドするしかない。
4. SGLang 側は `lmsysorg/sglang:qwen38-27b` / `:dev-qwen38-27b-dflash2` とも **公式 CI の arm64 マルチアーチ**で
   両方入っている。

### API 契約（ソースで確認済み）

どちらもゲートウェイ要件は満たすが、念のためソースで確認した。

| | `/v1/models` の `max_model_len` | チャンク毎の累積 usage |
|---|---|---|
| SGLang | あり（`ModelCard.max_model_len`） | `stream_options.continuous_usage_stats` で**毎チャンク**出る |
| vLLM | あり（`vllm/entrypoints/serve/engine/protocol.py`） | `continuous_usage_stats`（`include_usage` と併用） |
| llama.cpp | **なし**（`GET /props` の `n_ctx` にある） | 末尾チャンクのみ |

llama.cpp の `/v1/models` に `max_model_len` が無い件は Hermes Agent 側の既知問題
（hermes-agent issue #64897: 取れないと 131072 のファミリ既定にフォールバックする）。
**ふぐの backend にするなら llama.cpp は避けた方が良い。**

## 起動レシピ（SGLang）

MiaAI-Lab のレシピがそのまま使える。3 モードが同一エンドポイントに差し替え可能。

- <https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark>

```bash
git clone https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark
cd Qwen3.8-27B-SGLang-DGX-Spark
cp .env.sample .env
./start-dflash.sh          # DFlash2（最速）/ ./start-dspark.sh / ./start.sh (MTP)
curl http://127.0.0.1:8888/v1/models
```

主要なつまみ（`.env`）:

| 変数 | 既定 | 意味 |
|---|---|---|
| `CONTEXT_LENGTH` | `262144` | 262144–1000000。1M 超は `YARN=1` |
| `QUANT` | `nvfp4` | `nvfp4` / `nvfp4-fp4` / `fp8` / `bf16` |
| `MAX_CONCURRENT_REQUESTS` | `10` | `--max-running-requests` と mamba プール（値×5）を決める |
| `CHUNKED_PREFILL` | `8192` | prefill チャンク |
| `CPUSET` | `5-9,15-19` | **Cortex-X5 の大コアのみに pin**。無しだと小コアに落ちて decode -2〜7% |
| `PORT` | `8888` | |

素の CLI で組むなら主要フラグは以下（フラグ名は SGLang の
`ServerArgs` フィールド名から自動導出 = `tp_size` → `--tp-size`。**pin したイメージの
`--help` で最終確認すること**）:

```
--kv-cache-dtype fp8_e4m3
--context-length 262144
--mem-fraction-static 0.90
--chunked-prefill-size 8192          # ← --chunked-prefill ではない
--max-running-requests 10
--max-mamba-cache-size 50            # = 同時実行数 × 5（GDN の状態プール）
--speculative-algorithm DFLASH       # 大文字。EAGLE/EAGLE3/NEXTN/STANDALONE/NGRAM/DFLASH/DSPARK
--speculative-draft-model-path /models/qwen38-27b-dflash2-draft
--reasoning-parser qwen3
--tool-call-parser qwen3_coder
--enable-metrics --enable-cache-report
```

### このモデル特有の注意

- 64 層中 **16 層だけが KV を持つ**（`full_attention_interval: 4`）。残り 48 層は Gated DeltaNet の
  固定サイズ再帰状態で、`--max-mamba-cache-size` が別枠で効く。**同時実行数を増やすならここも増やす。**
  **1 リクエストあたり 5 スロット**（当初 4 と書いていたが誤り。起動ログが
  `5 state slots per request` と明示する）。足りないと `--max-running-requests` が
  黙って切り詰められる: `max_running_requests is capped to 8 by the mamba state cache`。
- **DSpark と DFlash2 は YaRN と併用不可**（draft config に漏れて起動時クラッシュ）。
  262144 超が要るなら **MTP モード一択**。
- DFlash2 の SSE は 1 イベントに平均 3.75 トークン乗るので、**イベント数で tok/s を測ると 4 倍過小評価**する。
  必ず `stream_options.include_usage` の `completion_tokens` で数える。

## チェックポイント選定

| チェックポイント | 形式 | サイズ | 向き先 |
|---|---|---|---|
| `nvidia/Qwen3.8-27B-NVFP4` | NVFP4(MLP+lm_head) + FP8(attn) | 21.9GB | **vLLM 公式レシピが `dgx_spark_gb10: verified` を付けている唯一の NVFP4** |
| `RadixArk/Qwen3.8-27B-NVFP4` / `-BF16-LMHead` | NVFP4 W4A4、**vision は BF16 維持** | 24GB | **SGLang レシピの既定。これを使う** |
| `unsloth/Qwen3.8-27B-NVFP4` | NVFP4、lm_head が FP8 | 22.6GB | vLLM 向け。SGLang なら **v0.5.19 以降必須** |
| `Qwen/Qwen3.8-27B-FP8` | block-scaled FP8 | 28.7GiB | 安全な fallback。NVFP4 比で約 30% 遅い |
| `cyankiwi/Qwen3.8-27B-AWQ-INT4` | AWQ W4A16 | 21GB | 動くが Blackwell の FP4 テンソルコアを活かせない |
| `RedHatAI/Qwen3.8-27B-INT4` | compressed-tensors W4A16 | 19.5GB | Hopper も要るとき用 |
| `*-GGUF` + `mmproj-F16.gguf` | GGUF | 17.6GB〜 | llama.cpp / Ollama 専用 |
| `z-lab/Qwen3.8-27B-DFlash2` | draft | 2.6GB | SGLang の DFlash2 ドラフト |
| `RadixArk/Qwen3.8-27B-DSpark` | draft | 2.7GB | DSpark ドラフト |

`gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090` は 5090 向けチューニングで GB10 の実績が無い。選ぶ理由は無い。

## sm_121 固有のハマりどころ

vLLM を使う場合 / 一般論として効くもの:

- **`--attention-backend triton_attn` を使い FlashInfer を避ける。** FlashInfer は Blackwell の FP8 モデルで
  精度バグが確認されており、MTP + sm_121 で illegal memory access も報告あり（vLLM #37754）。
- **`--moe-backend marlin`**（CUTLASS FP4 は sm_121 で無言で壊れた出力を出す）、`VLLM_USE_FLASHINFER_MOE_FP4=0`。
- **`--gpu-memory-utilization` は 0.90 が上限**、実用 0.82–0.87。統合メモリを OS ページキャッシュと奪い合うため。
  OOM したら `sync; echo 3 > /proc/sys/vm/drop_caches`。
- **`--enforce-eager` を使わない**（CUDA Graph が切れてスループット -55%）。sm_121 では CUDA Graph は必須。
- **ドライバは 580.x 系に留める**。590.x は GB10 で CUDA Graph デッドロックの報告。
- ページキャッシュと GPU テンソルが統合メモリを奪い合うので、**ロード前に drop_caches**。
  SGLang レシピ側は `posix_fadvise(DONTNEED)` 常駐で回避している。

## 他ランタイムの評価（結論だけ）

| ランタイム | 判定 | 理由 |
|---|---|---|
| **SGLang** | **採用** | 公式 arm64 CI イメージ、vision 稼働、KV FP8、API 契約を満たす、実測最速 |
| vLLM | 次点 | 現行資産は活きるが sm_121 明示ビルドと DFlash2 が両立しない |
| TensorRT-LLM | 不可 | GDN prefill を sm120/121 で有効化する PR が**未マージ**。v1.2.1 以降 GA 無し（1.3 が RC 28 本）。MTP 下で ptxas が `sm_121a` を拒否（#14575）。`/v1/models` に `max_model_len` 無し |
| llama.cpp | 別用途で継続 | `:server-cuda13` を使うこと（`:server-cuda` は CUDA 12.8 で sm_121 cubin 無し）。**80K ctx 超で decode が約24倍劣化する未修正バグ #27623**、`max_model_len` も出ない |
| ExLlamaV3 | 見送り | MiaAI-Lab フォークは GB10 対応かつ 47.5 tok/s だがソースビルド必須。**ARM 向け量子化には vision tower が入っていない** |
| mistral.rs | 要観察 | **唯一 aarch64+sm_121 のビルド済み wheel を配布**。ただし `max_model_len` 非公開、27B の vision 未検証、GB10 実測なし |
| TokenSpeed | 見送り | Qwen 公式が推す第 3 のエンジンだが **SM120 対応が `not_planned` でクローズ**。GB10 では vLLM の 70–74% |
| LMDeploy | 不可 | aarch64 の CMake が sm_72/87 決め打ち。TurboMind は Qwen3.5 系の vision encoder 非対応 → **vision と KV量子化が排他** |
| MLC-LLM | 不可 | **KV cache 量子化がエンジンに存在しない**。vision PR は未マージクローズ。リリース実績なし |
| KTransformers | 対象外 | Qwen3.8 非対応、**x86 AVX2/AMX 必須**。そもそも「MoE を host DRAM から流す」前提が統合メモリの dense 27B に無意味 |
| Atlas / veloGB10 / eider / GB10-Engine / ds4 | 時期尚早 | GB10 ネイティブな Rust 製が複数あるが、**27B + vision の実績が確認できない** |

## 実測値（コミュニティ報告、1 台）

| スタック | 単一ストリーム | 集約 |
|---|---|---|
| llama.cpp Q4_K_XL | 11.6 tok/s | — |
| llama.cpp + DFlash2 draft | 21–29 tok/s | — |
| vLLM FP8 | 8.2 tok/s | 57.9 @C10 |
| vLLM NVFP4 | 11.5 tok/s | 84.3 @C10 |
| vLLM + MTP (NVFP4) | 19.8 tok/s | — |
| SGLang FP8 | 7.7 tok/s | 54.3 @C10 |
| SGLang NVFP4 + DSpark | 34–51.5 tok/s (code) | — |
| **SGLang NVFP4 + DFlash2** | **50.9–65 tok/s (code)** | **227.6 @C16** |

dense GDN モデルは素の decode が 15–25 tok/s に張り付く（帯域律速）ので、
**性能の話はほぼ全部「投機デコードが効いているか」に還元される。**

## このスタックへの繋ぎ込み

**LiteLLM には載せない。prism-gw の透過ルートに載せる。** SGLang は OpenAI 互換なので
`gateway/config.yaml` の `upstreams:` に足して `models:` にルートを 1 本書くだけでよい:

```yaml
upstreams:
  sglang-qwen38:
    base_url: http://sglang-qwen38:8000/v1
    force_continuous_usage: true   # 累積 usage を上流に要求する

models:
  - name: qwen3.8-27b
    upstream: sglang-qwen38
```

**LiteLLM を通すと `max_model_len` とストリーム中の累積 usage が消える**（[gateway.md](./gateway.md)、
リポジトリ README の「なぜ prism-gw があるのか」）。このドキュメントが SGLang を選ぶ根拠に
挙げている 2 つが、そこで丸ごと失われる。LiteLLM の担当は形式変換が要る上流
（Anthropic / OpenAI / Gemini / ChatGPT）と Ollama の列挙だけ。

ふぐ側は `OPENAI_BASE_URL=http://host.docker.internal:4000/v1` のままでよい。
`/v1/models` に `max_model_len` が出るので Hermes のコンテキスト長自動検出も効く。

## この箱での実測（2026-09-22）

`lmsysorg/sglang:dev-qwen38-27b-dflash2` + `RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead`、
`--mem-fraction-static=0.80` / `--kv-cache-dtype=fp8_e4m3` / `--context-length=262144`。

起動時に確認できたこと:

- **0.80 で OOM せずに起動する。** KV プールは FP8 で **1,973,758 tokens**
  （K 30.12GB + V 30.12GB）、mamba state 5.88GB、確保後の空き 22.18GB。
  262,144 ctx なら約 7.5 本が同時に載る勘定。
- **FlashInfer の autotune が `sm121` 専用キャッシュを生成する**
  （`.cache/sglang/flashinfer/autotune/0.6.17/sm121/`）。sm_121 向けに効いている証拠。
- GDN は Triton カーネル（decode / extend / verify とも `TritonGDNKernel`）。
- CUDA graph のキャプチャに約 2 分（58 パターン）。起動全体で約 5 分。
- `/v1/models` に `max_model_len: 262144` が出て、prism-gw 越しでも保持される。

| 構成 | decode | 備考 |
|---|---|---|
| 投機デコードなし | **11.4 tok/s** | SSE 1 イベント = 1 トークン。TTFT 0.15s |

## 未検証・実機で決めるべきこと

- **`--mem-fraction-static` の実際の上限。** MiaAI-Lab は 0.90 を pin しているが、
  別の GB10 報告では 0.82 超で NVRM OOM。**必ず自分の箱で詰める。**
- **sm_121 での NVFP4 KV cache の可否。** フラグは受け付けるが実装は DeepSeek 系 sparse-MLA 向けの経路。
  **当面 `fp8_e4m3` を使う。**
- DFlash2 の上流未修正バグ 2 件: 高並行時のリクエスト間コンテキスト漏れ（sglang #36548）、
  thinking ON で greedy 出力が target-only と乖離（#38009）。
- vision tower を aarch64 で実際に回した実績は SGLang と llama.cpp 以外で確認できていない。
