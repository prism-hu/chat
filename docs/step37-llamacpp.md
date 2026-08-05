# Step-3.7-Flash on DGX Spark (llama.cpp)

StepFun [Step-3.7-Flash](https://huggingface.co/stepfun-ai/Step-3.7-Flash) を
DGX Spark (GB10 / 128GB 統合メモリ / sm121 / aarch64) で動かすための構成。

| | |
|---|---|
| モデル | 198B スパース MoE VLM (196B LM + 1.8B ViT)、**active ~11B/token** |
| コンテキスト | 256K |
| 特徴 | ネイティブ画像入力、tool calling、`reasoning_effort: low/medium/high` |
| ライセンス | Apache-2.0 |
| 公開 | 2026-05-29 |

## なぜ vLLM ではなく llama.cpp なのか

`vllm-qwen35` と同じ流儀で vLLM に載せることは**できない**。配布されている量子化の
実サイズが 128GB 統合メモリ (実際に使えるのは ~119GB) に収まらない:

| 配布形式 | サイズ | 判定 |
|---|---|---|
| BF16 | 403GB | ✗ |
| FP8 | 212GB | ✗ |
| NVFP4 (vLLM 向け最小) | 124GB + MTP 4.9GB | ✗ |
| **GGUF IQ4_XS (採用)** | **105GB** + mmproj 4GB | ○ |
| GGUF IQ3_XXS | 74GB | ○ (余裕はあるが品質を落とす) |

llama.cpp の Step3.7 対応は上流 ggml-org/llama.cpp の
[PR #23845](https://github.com/ggml-org/llama.cpp/pull/23845) が 2026-06-02 に
マージ済み (arch は `step35` を再利用、vision は `PROJECTOR_TYPE_STEP3VL`)。
ただし StepFun 公式の手順は今も fork の `step3.7` ブランチを指しており、DGX Spark
での実績報告も fork 側なので、**既定は fork**。上流も試せるように build arg にしてある。

## メモリ配分の現実

weights 105GB + mmproj 4GB で **109GB**。残りは ~10GB しかなく、そこに KV cache と
compute buffer と OS (Xorg も動いている) が同居する。だから:

- **`vllm-qwen35` とは同時に起動できない。** 片方を止めてもう片方を上げる。
- KV cache は `q8_0` に量子化して詰める。
- 既定のコンテキストは **64K**。256K まで上げられるが、下の「256K まで伸ばす」を参照。
- **`--parallel 1` は事実上必須。** 統合メモリ機では llama.cpp が新旧のコンテキスト
  キャッシュを二重に保持してホストごと落ちる報告がある。

## モデルの取得

`vllm-qwen35` と同じく、host の `${MODELS_DIR}` (既定 `~/models`) に置く。

```bash
hf download stepfun-ai/Step-3.7-Flash-GGUF \
  --include "IQ4_XS/*" \
  --include "mmproj-step3.7-flash-f16.gguf" \
  --local-dir ~/models/step37-flash-iq4xs
```

配置後:

```
~/models/step37-flash-iq4xs/
├── IQ4_XS/Step-3.7-flash-IQ4_XS-00001-of-00003.gguf   # 46.5GB (これを --model に渡す)
├── IQ4_XS/Step-3.7-flash-IQ4_XS-00002-of-00003.gguf   # 47.0GB (自動で拾われる)
├── IQ4_XS/Step-3.7-flash-IQ4_XS-00003-of-00003.gguf   # 11.5GB (同上)
└── mmproj-step3.7-flash-f16.gguf                      #  4.0GB (vision)
```

投機デコード用の `Step3.7-flash-mtp-Q8_0.gguf` (3.7GB) も取ってあるが、fork 側で
MTP が使えるか未確認なので今は繋いでいない。

## ビルドと起動

イメージは `llamacpp/Dockerfile` から自前ビルド (CUDA 13.1 / `CMAKE_CUDA_ARCHITECTURES=121`)。
ベースは [stevibe/step37-flash-dgx-spark](https://github.com/stevibe/step37-flash-dgx-spark) (MIT)。
モデル自動 DL の entrypoint は落とし、起動フラグは `docker-compose.yml` の `command` に出してある。

```bash
# ビルドは qwen を止めてから (nvcc がホスト RAM を食う。122B が ~100GB 保持している)
docker compose stop vllm-qwen35
docker compose build llamacpp-step37          # 初回 ~20-40 分
docker compose up -d --no-deps llamacpp-step37

# 105GB のロードに数分かかる。ログで待つ
docker compose logs -f llamacpp-step37
```

qwen に戻す:

```bash
docker compose stop llamacpp-step37
docker compose up -d --no-deps vllm-qwen35
```

`--no-deps` を付けるのは、他サービスを巻き込んで再作成しないため。

## 動作確認

```bash
# コンテナ内から
docker exec llamacpp-step37 curl -s localhost:8000/v1/models

# LiteLLM 経由 (litellm も入れ直しておくこと: docker compose up -d --no-deps litellm)
curl -s localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"step-3.7-flash","messages":[{"role":"user","content":"日本語で自己紹介して"}]}' \
  | jq -r '.choices[0].message.content'
```

推論深度 (`low` / `medium` / `high`、既定 `medium`) は chat template の変数なので、
OpenAI の `reasoning_effort` ではなく `chat_template_kwargs` で渡す:

```bash
  -d '{"model":"step-3.7-flash","chat_template_kwargs":{"reasoning_effort":"high"},
       "messages":[...]}'
```

速度の目安 (DGX Spark 実測報告): 短いコンテキストで **~30 t/s**、262K フルで **~11 t/s**。
vLLM + MTP-2 の qwen3.5 より遅い。得るものは vision と agent/coding 性能。

## 256K まで伸ばす

`--ctx-size 262144` に上げるなら、DGX Spark で報告されている追加フラグを
`command` に足す (いずれも新しめの llama.cpp のフラグなので、fork のブランチに
無ければ諦めて 64K〜128K で使う):

```
--ctx-checkpoints 1 --checkpoint-min-step 128 --cache-ram 1024
```

このとき **並列リクエストを増やさないこと**。フルコンテキストの推論を連続で回すと
落ちる報告があり、原因は「統合メモリでないことを前提にした」キャッシュ保持。

## メモリが足りずに落ちたら

上から順に試す:

1. `--mmproj` の 2 行を消す (vision を諦めて 4GB 浮かせる)
2. `--ctx-size` を 32768 に下げる
3. `--ubatch-size 256` を足す (compute buffer が縮む)
4. 量子化を落とす — unsloth の `UD-Q3_K_XL` (89GB) / `UD-IQ4_XS` (95GB)。
   `STEP37_MODEL_PATH` を `.env` で差し替えれば compose は触らずに済む

## 出典

- [stepfun-ai/Step-3.7-Flash](https://huggingface.co/stepfun-ai/Step-3.7-Flash) / [GGUF](https://huggingface.co/stepfun-ai/Step-3.7-Flash-GGUF)
- [Step-3.7-Flash on single Spark (llama.cpp only) — NVIDIA Developer Forums](https://forums.developer.nvidia.com/t/step-3-7-flash-on-single-spark-llama-cpp-only/371804)
- [llama.cpp PR #23845](https://github.com/ggml-org/llama.cpp/pull/23845)
- [stevibe/step37-flash-dgx-spark](https://github.com/stevibe/step37-flash-dgx-spark) (取り込み元の Dockerfile / MIT)
- [StepFun Docs — step-3.7-flash](https://platform.stepfun.ai/docs/en/guides/models/step-3.7-flash)
