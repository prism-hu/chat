# Qwen3.8-Flash-Next を Spark 2 台（TP=2）で serve する

調査日 **2026-09-22**。質問は「Flash-Next は TP=2 でいけるか」。**答えは「いける。
というより 2 台でないと載らない」。** 1 台構成の Qwen3.8-27B は
[qwen3.8-27b-serving.md](./qwen3.8-27b-serving.md)、TP=2 の一般論は
[dgx-spark-tp2.md](./dgx-spark-tp2.md)。

## TL;DR

- **1 台には載らない。** NVFP4 でも 132.7GB あり、1 台の 121GiB(≈130GB) を超える。
  27B のときと逆で、**TP=2 は速度のためでなく「載せるため」に必須**。
- **TP=2 はちょうど上限。** `num_key_value_heads=2` なので TP=4 も TP=3 も割れない。
  3 台目は TP には使えない（27B と同じ結論）。
- **実測 54.4 tok/s @単一ストリーム / 207.0 tok/s @並列 8。** 1 台の 27B(37 tok/s) より
  速く、しかもモデルははるかに大きい。
- エンジンは **vLLM**（SGLang ではない）。ただし**イメージ内の vLLM ソースに
  パッチを当てる**レシピなので、運用の重さは 27B の比ではない。

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

| repo | サイズ | 1 台(≈130GB)に載るか |
|---|---|---|
| `Qwen/Qwen3.8-Flash-Next` (BF16) | 360.0 GB | × |
| `Qwen/Qwen3.8-Flash-Next-FP8` | 185.5 GB | × |
| **`nvidia/Qwen3.8-Flash-Next-NVFP4`** | **132.7 GB** (11 shards) | **×（わずかに超過）** |
| `RadixArk/Qwen3.8-Flash-Next-NVFP4` | 135.2 GB (206 shards) | × |
| `local-inference-lab/Qwen3.8-Flash-Next-NVFP4` | 99 GB (37 shards)※ | 要検証 |

※ サイズはレシピの `.env.sample` の注記（MXFP8 attention + NVFP4 PLE）。**これだけは
1 台に載る可能性があるが未検証。** 1 台で済むなら話が根本から変わるので、2 台構成に
進む前にここを確かめる価値がある。

TP=2 なら重みは 2 台に分かれるので、メモリ上は 1 台あたり ~66GB。**ただしディスクは
各ノードに ~126GiB のフルコピーが要る**（レシピは head から rsync、NFS 共有も可）。

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

## 性能の目安（レシピ README の実測値）

単一ストリーム、greedy:

| 構成 | decode |
|---|---|
| MTP なし | 24.5 tok/s |
| **MTP あり** | **52.1 tok/s（2.13×）** |
| 最良構成 | **54.4 tok/s**（TTFT 160 ms） |

並列時（集約 / 1 ストリームあたり / TTFT）:

| 並列 | 集約 | per stream | TTFT |
|---|---|---|---|
| ×1 | 54.4 | 54.4 | 160 ms |
| ×2 | 86.5 | 45.1 | 426 ms |
| ×4 | 128.1 | 34.2 | 471 ms |
| ×8 | **207.0** | 26.7 | 432 ms |

prefill は 16k〜64k で **~2.96k tok/s** とほぼ平坦、128k で 8% 低下。

KV 予算（既定値での実測ログ）:

```
Available KV cache memory: 32.02 GiB
GPU KV cache size: 3,652,200 tokens
Maximum concurrency for 262,144 tokens per request: 13.93x
```

`fp8` と `auto`(bf16) の比較（同一 GMU・同一チェックポイント、KV dtype だけ変更）:

| | auto (bf16) | **fp8** |
|---|---|---|
| Cache size | 2,131,159 tok | **3,652,200 tok** |
| 262,144 での並列度 | 8.13× | **13.93×** |
| tokens/GiB | 67,229 | **114,060（1.70×）** |

> 注: 公式 FP8 チェックポイント（`./start-fp8.sh`）は別物で、KV は **約 500k tokens** しか
> 取れない。上の 3.65M は NVFP4 の話。

## 判断材料（採用する前に）

**良い点**

- **1 台の 27B(37 tok/s) より速い**（54.4 tok/s）のに、モデルははるかに大きい
- KV が 365 万トークン確保でき、262K 文脈を 13.9 本同時に持てる
- `/v1/models` の `max_model_len` と streaming の累積 usage は vLLM も出すので、
  prism-gw の透過ルートにそのまま載る

**重い点**

- **エンジンが SGLang ではなく vLLM。** このスタックは今 SGLang に寄せたばかりで、
  2 つのエンジンを併用することになる
- **レシピがイメージ内の vLLM ソースにパッチを当てる**（`modelopt.py` / `model.py` /
  `hyperconnection.py` / `mtp.py` / `ops/qsa.py` を差し替え）。**素の docker run + フラグでは
  動かない。** 上流が動いたときの追従コストが高い
- 各ノードにフルコピーで ~126GiB のディスクが要る
- [dgx-spark-tp2.md](./dgx-spark-tp2.md) の 2 ノード固有の地雷が全部かかる
  （NCCL の無言デッドロック、MTU 9000、`--network host`、PYNCCL フォールバックで逆に 2 倍遅くなる等）

**先に確かめるべきこと**

1. **`local-inference-lab/Qwen3.8-Flash-Next-NVFP4`（99GB）が 1 台に載るか。**
   載るなら 2 ノードの複雑さを丸ごと回避できる。最優先で確認する価値がある。
2. 2 台の物理結線（QSFP112 DAC / 400GbE / Ethernet-mode-only）が手元にあるか。
3. 上の実測値はすべて**レシピ作者の自己申告**。第三者の再現は確認できていない。

## 未確認

- このレシピを実機で回していない（本ドキュメントは一次資料の読み取りのみ）。
- `local-inference-lab` 版の実サイズと 1 台での可否。
- vLLM の `qwen3_5_mtp` が TP>=2 で壊れる件（issue #52480）が `qwen4_exp` にも及ぶか。
  レシピは MTP3 を TP=2 で使えていると主張しているので、少なくともこの
  イメージ＋パッチの組み合わせでは回避されているとみられる。
