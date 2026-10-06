# Qwen3.6-35B-A3B on DGX Spark (vLLM / NVFP4)

[Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) を DGX Spark
(GB10 / 128GB 統合メモリ / sm121 / aarch64) で配信する構成。LiteLLM からは
`qwen3.6-35b` として見える。

| | |
|---|---|
| モデル | 総 36B / **アクティブ 3B** の MoE (256 エキスパート中 8 個)、40 層 |
| 機能 | vision (画像・動画) / tool calling / thinking、ctx 262144 |
| 重み | NVIDIA 公式 NVFP4 (modelopt MIXED_PRECISION) **20.4GiB** |
| KV cache | FP8。util 0.55 で 300 万トークン超 |
| **実測 decode** | **78.5 t/s** |
| ライセンス | Apache-2.0 |

## なぜこの組み合わせなのか

同じモデルを ollama (llama.cpp / Q4_K_M) でも配信できるが **57 t/s** 止まりで、
KV cache 量子化もデーモン全体設定 (`OLLAMA_KV_CACHE_TYPE`) しかなく、ホストの
systemd を触る必要がある。vLLM + NVFP4 なら **+38%** 速く、設定もサービス単位で閉じる。

### イメージは上流公式 (`vllm/vllm-openai`)

このスタックには以前 Qwen3.5-122B 用のカスタム SM121 ビルド
(`ghcr.io/prism-hu/vllm-qwen35-v2`、撤去済み) があったが、**Qwen3.6 には使えなかった**。

| イメージ | vLLM | 結果 |
|---|---|---|
| カスタム SM121 ビルド | 0.19.1.dev0 | ✗ NVFP4 の MoE を読めず `KeyError: layers.0.mlp.experts.w2_input_scale` |
| NGC `nvcr.io/nvidia/vllm:26.07-py3` | 0.24.0 | △ 動くが 76.8 t/s、DSpark スペキュレータ非対応 |
| **上流 `vllm/vllm-openai:v0.26.0`** | 0.26.0 | ✓ **78.5 t/s**。採用 |

上流イメージは **arm64 マニフェストがある**ので自前ビルド不要。`TORCH_CUDA_ARCH_LIST`
は `12.0` 止まりで sm_121 を含まないが、Blackwell は sm_120 の cubin が sm_121 で
動くため問題ない (`torch.cuda.get_arch_list()` → `sm_120` / device cc `(12,1)` で実機確認)。

> **ENTRYPOINT の違いに注意。** 上流は `["vllm","serve"]`、旧カスタムビルドは `["vllm"]`。
> compose の `command:` に `serve` を書くと上流では `vllm serve serve ...` になって
> 起動しない。**モデルは `--model=` で渡す**（旧 vllm-qwen35 の位置引数スタイルとは別）。

## モデルの取得

```bash
hf download nvidia/Qwen3.6-35B-A3B-NVFP4 --local-dir ~/models/qwen36-35b-a3b-nvfp4
```

23.4GB (safetensors 3 分割)。`hf_quant_config.json` に `quant_algo: MIXED_PRECISION` と
`kv_cache_quant_algo: FP8` が入っており、vLLM は `quantization=modelopt_mixed` として認識する。

## 起動

```bash
# 他の大物とは排他。動いていれば先に止める
docker compose stop llamacpp-step37 sglang-qwen38 vllm-qwen38-fn
docker compose up -d --no-deps vllm-qwen36     # ロード ~140s + エンジン初期化 ~145s
docker compose logs -f vllm-qwen36             # `Application startup complete` を待つ
```

**`--force-recreate` を使わないこと。** 短時間に recreate を重ねると warmup 中断で
GPU が汚れ、次回起動が `CUDA error: an illegal instruction was encountered` で落ちる
(旧 Qwen3.5-122B 運用で踏んだ)。起動が失敗した後は `docker compose rm -f vllm-qwen36` で
コンテナを消してから、`up -d` を 1 回だけ。

## パラメータ

- `--tool-call-parser=qwen3_coder` — **Qwen3.5 の `qwen3_xml` とは別物**。model card 準拠
- `--reasoning-parser=qwen3` — thinking を `reasoning_content` に分離
- サンプリングは thinking モードの推奨値 (temp 1.0 / top_p 0.95 / top_k 20 /
  presence_penalty 1.5)。**コード用途は temp 0.6 / presence_penalty 0.0 が推奨**なので、
  常用するなら LiteLLM に `qwen3.6-35b-coder` を別エントリで足す
- 思考を切るには `chat_template_kwargs: {"enable_thinking": false}`

## 試して不採用にしたもの

### ネイティブ FP4 (`flashinfer_b12x`)

vLLM は GB10 をネイティブ FP4 非対応と判定し、MoE を Marlin の weight-only
フォールバックで動かす:

```
WARNING [marlin.py] Your GPU does not have native support for FP4 computation ...
```

NGC 版のコードに SM121 向けの記述があり、明示指定でネイティブ FP4 を使える:

```
# FLASHINFER_B12X is intentionally excluded from auto-selection until
# the upstream CUTLASS SM121 MMA op guard is resolved; use
# moe_backend="flashinfer_b12x" to opt in explicitly.
```

`--kernel-config '{"moe_backend":"flashinfer_b12x"}'` で実際に有効化できたが、
**decode は 78.4 t/s で変化なし**。decode (バッチ 1) はメモリ帯域律速で FP4 の
演算器が効かないため。prefill だけ僅かに改善 (4K プロンプトで 3.3s → 3.1s)。
上流に対応が入るまで既定のままでよい。

### 投機デコード

- **MTP なし**: 公式チェックポイント (base / FP8 / NVFP4) の config に
  `num_nextn_predict_layers` が無く、MTP 重みが同梱されていない
- **DSpark スペキュレータ**: [RedHatAI/Qwen3.6-35B-A3B-speculator.dspark](https://huggingface.co/RedHatAI/Qwen3.6-35B-A3B-speculator.dspark)
  (1.9GB、DGX Spark 向けに訓練、block_size 8) は上流 v0.26.0 が
  `Qwen3DSparkModel` として対応しているが、NVFP4 ベースだとドラフト生成で落ちる:
  ```
  File ".../v1/worker/gpu/spec_decode/dspark/speculator.py", line 145, in _sample_sequential
  torch.AcceleratorError: CUDA error: device-side assert triggered
  ```
  公式の起動例は BF16 ベース (70GB) 前提。スペキュレータの `draft_vocab_size: 32000` と
  NVFP4 側の語彙マッピングが噛み合っていない疑い。BF16 に替えれば動く可能性はあるが、
  1 トークンあたりの読み出しが 4 倍になり投機で得る分を食い潰すので追っていない

### 他の量子化

| 候補 | サイズ | 判断 |
|---|---|---|
| Qwen 公式 FP8 | 37.5GB | アクティブ 3B を FP8 で読むと帯域が倍 → 理論上 NVFP4 の約半分。品質を優先したい場合の保険 |
| unsloth NVFP4-Fast (compressed-tensors) | 23.7GB | 別コードパス。NVFP4 が動いたので未検証 |
| BF16 | 70GB | 帯域的に論外 (投機デコードを使う場合のみ検討価値) |

## 計測 (2026-08-14, ctx 65536 / util 0.5〜0.6)

| 構成 | decode | 4K プロンプト |
|---|---|---|
| ollama Q4_K_M (llama.cpp) | 57 t/s | — |
| NGC 0.24.0 + NVFP4 (Marlin) | 76.8 t/s | — |
| NGC 0.24.0 + NVFP4 (B12X ネイティブ) | 78.4 t/s | 3.1s |
| **上流 0.26.0 + NVFP4 (Marlin)** | **78.5 t/s** | 3.3s |

3〜4 回計測して ±0.1 t/s に収まる安定した値。ロード 138 秒、エンジン初期化 145 秒。
vision も確認済み (画像内の図形と文字を正確に記述)。

参考: アクティブ 3B を 4bit で読むと 1 トークンあたり ~1.5GB。78 t/s なら実効
~117GB/s で、GB10 の理論帯域 273GB/s の約 43%。MoE のエキスパート集約は
アクセスが散るので、この程度が現実的な上限。

## 出典

- [Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) / [nvidia/Qwen3.6-35B-A3B-NVFP4](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4)
- [vllm/vllm-openai (Docker Hub)](https://hub.docker.com/r/vllm/vllm-openai)
