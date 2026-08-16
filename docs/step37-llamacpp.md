# Step-3.7-Flash on DGX Spark (llama.cpp)

> **現状: 停止中（お蔵入り）。** 動作自体はすべて確認できたが、**速度が実用に届かない**
> と判断して `vllm-qwen35` に戻した (2026-08-05)。MTP を入れても decode 31〜35 t/s。
> 構成は残してあるので、下記の手順で切り替えれば再開できる。実測値は
> 「計測結果」に全部残してある。

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

llama.cpp は **上流 ggml-org の master** を使う (build arg の既定)。StepFun の fork
(`step3.7` ブランチ) でも本体は動くが、MTP のドラフトが読めない (下記)。fork に戻す
場合は `LLAMACPP_REPO` / `LLAMACPP_REF` を上書きする。

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

# prism-gw 経由 (gateway/config.yaml を触ったなら: docker compose up -d --no-deps prism-gw)
# step-3.7-flash は透過ルート — LiteLLM は通らない (docs/gateway.md)
curl -s localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $PRISM_GW_API_KEY" -H 'Content-Type: application/json' \
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

## MTP (投機デコード)

**+30% 程度は稼げるが、vision と排他。**

### 使うドラフトは notSnix のもの

StepFun 公式の `Step3.7-flash-mtp-Q8_0.gguf` は**使えない**。fork の llama.cpp で
ドラフトとして読ませると起動時に落ちる:

```
error loading model: missing tensor 'blk.0.attn_norm.weight'
srv load_model: [spec] failed to measure draft model memory: failed to load model
```

MTP-tail のドラフト読み込みに対応しているのは**上流 master 側**。ドラフト本体も
[notSnix/Step-3.7-Flash-Q4_K_M-MTP-GGUF](https://huggingface.co/notSnix/Step-3.7-Flash-Q4_K_M-MTP-GGUF)
の `Step-3.7-Flash-MTP-Q8_0.gguf` (3.7GB) を使う。同 repo には古いビルド向けの
パッチも置いてあるが、上流 master でビルドするなら不要。

```bash
hf download notSnix/Step-3.7-Flash-Q4_K_M-MTP-GGUF Step-3.7-Flash-MTP-Q8_0.gguf \
  --local-dir ~/models/step37-flash-iq4xs

# イメージを上流 master でビルドし直す (fork ではダメ)
LLAMACPP_REPO=https://github.com/ggml-org/llama.cpp.git LLAMACPP_REF=master \
  BUILD_JOBS=12 docker compose build llamacpp-step37
```

`--spec-draft-n-max 2 --spec-draft-p-min 0.60` は notSnix がスイープして出した推奨値。

### vision とは併用できない

mmproj と MTP を両方有効にすると、**画像を投げた瞬間に 500**:

```
decode() failed: failed to process speculative batch
```

`tools/server/server-context.cpp` の `common_speculative_process(spec, batch_view)` は
すべてのバッチに対して走るが、MTP ドラフトは画像埋め込み (token ではなく embd の
batch) を処理できない。リクエスト単位で `speculative.n_max: 0` を渡しても回避できない
(サーバ側で常に呼ばれるため)。**どちらを取るかを選ぶしかない。**

## 計測結果 (2026-08-05, IQ4_XS / ctx 64K / KV q8_0)

| 構成 | prefill (4K prompt) | decode | メモリ |
|---|---|---|---|
| fork step3.7 + vision, MTP なし | 614 t/s | **26.5 t/s** | 109GB |
| 上流 master + vision + MTP | — | **500 エラー** | — |
| 上流 master + MTP, vision なし | 577 t/s | **31〜35 t/s** | 110GB |

- ビルド 142 秒 (`BUILD_JOBS=12`)、モデルロード ~115 秒
- 日本語・tool calling は全構成で正常。vision も MTP なしなら正常
  (画像内の図形と文字を正確に読む)
- 参考: GB10 のメモリ帯域から見た理論上限は ~45 t/s 程度 (active 11B × 4.25bit)。
  つまり MTP ありで既にロードラインの 7〜8 割で、llama.cpp 側でこれ以上の伸びは薄い

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
