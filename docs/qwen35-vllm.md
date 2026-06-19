# Qwen3.5-122B カスタム vLLM（DGX Spark / SM121）

このスタックは Qwen3.5-122B-A10B を **自前ビルドの vLLM イメージ**で配信している。その全体像・構築・
チェック・配布(GHCR)をまとめる。OpenWebUI からは LiteLLM 経由で `qwen3.5-122b` として見える。

## 概要 / なぜカスタムか

- DGX Spark の GPU は **GB10 / SM121（consumer Blackwell）**。vLLM の prebuilt wheel が無く、
  **ソースから SM121 向けにコンパイルが必須**（初回 ~25分）。
- [albond/DGX_Spark_Qwen3.5-122B-A10B-AR-INT4](https://github.com/albond/DGX_Spark_Qwen3.5-122B-A10B-AR-INT4)
  を **prism-hu に fork** し `vendor/qwen35-spark`（submodule）として取り込み。
  最適化: **INT4+FP8 hybrid / MTP-2 投機デコード / FlashInfer / INT8 LM Head** で ~52 tok/s。
- 役割分担:

  | 物 | 取得 | 置き場 |
  |---|---|---|
  | 実行イメージ | **GHCR pull**（`ghcr.io/prism-hu/vllm-qwen35-v2`） | docker |
  | モデル(72G) | `scripts/fetch-model.sh`（Steps 0-2） | `~/models`（実行時マウント） |
  | ビルド再現 | fork + `artifacts/docker-images/*.tar.gz` | — |

## 使う（通常運用 = GHCR から pull）

```bash
docker compose pull vllm-qwen35                  # GHCR からイメージ取得（ビルド不要）
MODELS_DIR=~/models ./scripts/fetch-model.sh     # モデル準備（DL+hybrid化+MTP）→ ~/models/qwen35-122b-hybrid-int4fp8
docker compose up -d vllm-qwen35                 # 起動（初回ロード+ウォームアップ ~13分）
```

- `docker logs -f vllm-qwen35` で `Application startup complete` を待つ。
- **`--gpu-memory-utilization=0.85`**（compose 設定済み）。128GB は Ollama 等と共有のため 0.90 は
  起動時に `ValueError: Free memory ... less than desired` で落ちることがある。
- **`litellm/config.yaml` を編集したら `docker compose restart litellm`**（起動中は reload しない）。

## 動作チェック

> ✅ **2026-06-20 動作確認済み**: `/health` 200 / LiteLLM 経由で `reasoning_content`（thinking）出力 /
> 「9.11 と 9.9」に正答（finish_reason=stop）。**thinking が LiteLLM 経由で通る**ため OpenWebUI 直結は不要。

```bash
# 1) vLLM 自体
curl -s localhost:8000/health        # 200 ならロード完了

# 2) 直接（served-model-name=qwen）
curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"qwen","messages":[{"role":"user","content":"こんにちは"}],"max_tokens":64}'

# 3) LiteLLM 経由（= OpenWebUI と同じ経路）＋ thinking(reasoning_content) 確認
KEY=$(grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2)
curl -s http://localhost:4000/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.5-122b","messages":[{"role":"user","content":"9.11と9.9はどちらが大きい？"}],"max_tokens":512}' \
  | python3 -c 'import sys,json;m=json.load(sys.stdin)["choices"][0]["message"];print("reasoning_content:",bool(m.get("reasoning_content")))'

# 4) チャット疎通（open-webui コンテナ → litellm の実経路）
docker exec open-webui sh -c "curl -s -o /dev/null -w '%{http_code}\n' http://litellm:4000/v1/models -H 'Authorization: Bearer $KEY'"
```

### thinking が出ない / 機能が落ちる場合 → OpenWebUI 直結
LiteLLM を1段挟むため `reasoning_content` や tool calling が劣化するなら、OpenWebUI を vLLM に直結:
1. `litellm/config.yaml` から `qwen3.5-122b` を削除（外部 `:4000` API からは外れる点に注意）
2. `open-webui` の environment を直結用に:
   ```yaml
   - OPENAI_API_BASE_URLS=http://litellm:4000/v1;http://vllm-qwen35:8000/v1
   - OPENAI_API_KEYS=${LITELLM_MASTER_KEY};dummy
   ```
3. `open-webui` の `depends_on` に `vllm-qwen35` を足して `docker compose up -d`

## イメージをビルドし直す（install.sh）

GHCR のイメージは DGX Spark 上でこの手順で作った。

```bash
git submodule update --init --recursive
( cd vendor/qwen35-spark && ./install.sh --no-launch )   # vllm-sm121 + vllm-qwen35-v2 を build (~25分)
```

ビルド後の**起動時 確定要件**（満たさないと crash、いずれも実機確認済み。1・3 は `docker/Dockerfile.v2`、2 は compose）:
1. **torchaudio 除去**。cu130 nightly の torchaudio は新しい torch(2.14) 向けにビルドされ、torch 2.13
   base では `libtorchaudio.abi3.so: undefined symbol: torch_exception_get_what_without_backtrace`
   で import 失敗 → 起動時 transformers が読んで crash。LLM 配信に torchaudio は不要・不在は許容。
2. **`--gpu-memory-utilization=0.85`**。0.90 は共有メモリ不足で `ValueError`。
3. **fastapi `<0.137`**。vLLM 0.19.0 は `fastapi>=0.115`（上限なし）なので base は 0.137 を引くが、
   0.137 は `_IncludedRouter` を app.routes に追加し、vLLM の `prometheus_fastapi_instrumentator`
   (8.0.0) がそれを走査できず **全リクエストが 500**（`'_IncludedRouter' object has no attribute
   'path'`、`/health` 含む）。0.136 で解消し、starlette は 1.3.1 のまま（sse-starlette が要求する
   `>=0.49.1` を維持）。0.115 まで下げると starlette が落ちて sse-starlette が壊れるので不可。

保存と配布:
```bash
# 蒸発対策アーティファクト（nightly が消えても docker load で復元可）
docker save vllm-sm121:latest vllm-qwen35-v2:latest | gzip \
  > artifacts/docker-images/vllm-sm121-qwen35-v2-<date>.tar.gz

# GHCR へ push（SSH不可・要 PAT scope=write:packages）
echo <PAT> | docker login ghcr.io -u <github-username> --password-stdin
docker tag vllm-qwen35-v2:latest ghcr.io/prism-hu/vllm-qwen35-v2:latest
docker tag vllm-qwen35-v2:latest ghcr.io/prism-hu/vllm-qwen35-v2:v0.19.0-torch2.13-20260620
docker push ghcr.io/prism-hu/vllm-qwen35-v2:latest
docker push ghcr.io/prism-hu/vllm-qwen35-v2:v0.19.0-torch2.13-20260620
# base も配るなら（再ビルド短縮用・任意）: 同様に vllm-sm121 を tag/push
```

## バージョン / ピン（再現条件）

| 対象 | 値 |
|---|---|
| albond submodule（fork: prism-hu） | `1db76a6` |
| eugr/spark-vllm-docker | `49d6d9fefd7cd05e63af8b28e4b514e9d30d249f` |
| vLLM ref | `v0.19.0`（ビルド結果 `0.19.1.dev0+g2a69949bd`） |
| ビルドフラグ | `TORCH_CUDA_ARCH_LIST=12.1a` / `--vllm-ref v0.19.0 --tf5` |
| Python / OS | 3.12.3 / aarch64 / Ubuntu 24.04 / CUDA 13.x |

### 採用 torch（fork の `install.sh` にピン済み）
```
torch==2.13.0.dev20260422+cu130
torchvision==0.27.0.dev20260423+cu130
torchaudio==2.11.0.dev20260619+cu130   # ※ build 用に install されるがイメージでは除去
```

**なぜ 2.13**: albond 検証版の torch `2.12.0.dev20260408` は nightly retention 切れで消滅。現存の
**2.14 は ATen ヘッダが C++20 必須**（`std::integral`）だが **vLLM 0.19.0 は C++17 ビルド**のため
`NumericUtils.h` で全滅。間の **2.13（C++20 化前）**に下げたら C++17 のまま通った（torchvision も
albond と同じ 0.27 系で、同世代である裏付け）。

### nightly 期限切れ時の再解決（DGX Spark 上で）
```bash
printf 'torch>=2.13,<2.14\ntorchvision\ntorchaudio\n' > /tmp/tr.in   # <2.14 が C++20 回避の肝
uv pip compile /tmp/tr.in --index-url https://download.pytorch.org/whl/nightly/cu130 \
  --prerelease=allow --python-version 3.12 -o /tmp/tr.txt
grep -iE '^(torch|torchvision|torchaudio)==' /tmp/tr.txt
```
> ⚠️ `--extra-index-url https://pypi.org/simple` は付けない（安定版 非cu130 を拾い SM121 で使えない）。
> 得た3版を `vendor/qwen35-spark/install.sh` の `TORCH_VERSION` 等に貼り、fork に commit → 再ビルド。
> **uv が決められないのは「どの版が vLLM 0.19.0 とコンパイル互換か」だけ**（メタデータに無い）。
> `<2.14` の上限は人間が与える。

### 再現性について
nightly wheel は ~35日で index から消えるため、**版ピンだけでは再現できない**（lock の URL も 404 になる）。
本物の再現性アーティファクトは **`docker save`（`artifacts/docker-images/`）** と **GHCR のイメージ**。

## モデル準備の詳細
- `Intel/Qwen3.5-122B-A10B-int4-AutoRound`（snapshot `3045d02bb737effc4581da91bddbad3be02934e4`）— INT4 本体 + MTP
- `Qwen/Qwen3.5-122B-A10B-FP8` — hybrid 用 FP8 ソース
- 出力: `~/models/qwen35-122b-hybrid-int4fp8`（14 shards + MTP 785 テンソル）
- host 依存は uv で固定（fork の `pyproject.toml` + `uv.lock`、`uv sync --frozen`）
- `scripts/fetch-model.sh` は `install.sh --model-only` を呼ぶラッパ（Steps 0-2 のみ実行して脱出）
