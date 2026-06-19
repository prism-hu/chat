# chat.prism-hu.org

Visit https://chat.prism-hu.org

## 環境

NVIDIA DGX Spark (128GB 統合メモリ)

## スタック

- OpenWebUI
- Ollama (ホスト実行)
- LiteLLM
- vLLM (Qwen3.5-122B, DGX Spark SM121 最適化 / `vendor/qwen35-spark` submodule)

## Ollama 設定

Ollama はホストで動作し、Docker コンテナからは `host.docker.internal:11434` 経由でアクセスする。

セキュリティのため Ollama は Docker ブリッジ IP (`172.28.0.1`) にのみバインドする。
`/etc/systemd/system/ollama.service.d/override.conf` を作成:

```ini
[Service]
Environment="OLLAMA_HOST=172.28.0.1"
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Docker ネットワーク (`chat_default`) のサブネットは `docker-compose.yml` で `172.28.0.0/16` に固定済み。


## LiteLLM API (外部アクセス)

Tailscale経由でOpenAI互換APIとして利用可能。

**エンドポイント:** `http://<HOST>:4000/v1`
**APIキー:** `.env` の `LITELLM_MASTER_KEY`

### 利用可能なモデル

OpenWebUI からは2系統が見える。**重複を避けるため、Ollama モデルは OpenWebUI が Ollama 直結
（`OLLAMA_BASE_URL`）で出し、LiteLLM には登録しない**（LiteLLM に入れると同じモデルが UI に二重表示される）。

**LiteLLM (`:4000`) 経由 — 外部 API でも利用可**
| model_name | バックエンド | 内容 |
|---|---|---|
| `claude-opus-4-6` | Anthropic API | Claude Opus 4.6 |
| `claude-sonnet-4-6` | Anthropic API | Claude Sonnet 4.6 |
| `qwen3.5-122b-custom` | カスタム vLLM (SM121) | Qwen3.5 122B-A10B（INT4+FP8 hybrid / MTP-2 / ~52 tok/s）。詳細 [docs/qwen35-vllm.md](docs/qwen35-vllm.md) |

**Ollama（ホスト実行）— OpenWebUI 直結。`:4000` 外部 API には出ない**
`gpt-oss-20b` / `gpt-oss-120b` / `sip-jmed-13b` / `sip-jmed-8x13b-q8` / `nemotron-3-nano` / `nemotron-3-super` / `qwen3.5-9b` / `qwen3.5-27b`

### 使い方

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://<HOST>:4000/v1",
    api_key="<LITELLM_MASTER_KEY>",
)
response = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "こんにちは"}],
)
```

```bash
curl http://<HOST>:4000/v1/chat/completions \
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-oss-20b", "messages": [{"role": "user", "content": "こんにちは"}]}'
```

モデル一覧の確認:

```bash
curl http://<HOST>:4000/v1/models \
  -H "Authorization: Bearer <LITELLM_MASTER_KEY>"
```

## Qwen3.5-122B カスタム vLLM

`qwen3.5-122b` は SM121 向けに**自前ビルドした vLLM イメージ**（`ghcr.io/prism-hu/vllm-qwen35-v2`、
GHCR から pull）で配信している。albond のフォーク（`vendor/qwen35-spark` submodule）をベースに
INT4+FP8 hybrid / MTP-2 / FlashInfer で最適化（~52 tok/s）。

**セットアップ・動作チェック・GHCR 配布・バージョン経緯・トラブルシュートは
[docs/qwen35-vllm.md](docs/qwen35-vllm.md) に集約。**

submodule 取得: `git submodule update --init --recursive`

## Models

### モデルの追加方法

前提: `pip install huggingface-hub`

1. GGUF ファイルを `./models/` にダウンロード (`hf download`)
2. `models/<name>.Modelfile` を作成 (`FROM models/<gguf-file>`)
3. Ollama に取り込み:

```
ollama create <name> -f models/<name>.Modelfile
```

### [SIP-med-LLM/SIP-jmed-llm-3-8x13b-AC-32k-instruct](https://huggingface.co/SIP-med-LLM/SIP-jmed-llm-3-8x13b-AC-32k-instruct)

量子化:  [hiratagoh/SIP-jmed-llm-3-8x13b-AC-32k-instruct-GGUF](https://huggingface.co/hiratagoh/SIP-jmed-llm-3-8x13b-AC-32k-instruct-GGUF)

#### BF16 (~146GB)

```
hf download hiratagoh/SIP-jmed-llm-3-8x13b-AC-32k-instruct-GGUF \
  SIP-jmed-llm-3-8x13b-AC-32k-instruct-BF16.gguf \
  --local-dir ./models
```

```
ollama create sip-jmed-8x13b -f models/sip-jmed-8x13b.Modelfile
```

#### Q5_K_M (~36GB)

```
hf download hiratagoh/SIP-jmed-llm-3-8x13b-AC-32k-instruct-GGUF \
  SIP-jmed-llm-3-8x13b-AC-32k-instruct-Q5_K_M.gguf \
  --local-dir ./models
```

```
ollama create sip-jmed-8x13b-q5 -f models/sip-jmed-8x13b-q5.Modelfile
```

#### Q8_0 (~78GB)

```
hf download hiratagoh/SIP-jmed-llm-3-8x13b-AC-32k-instruct-GGUF \
  SIP-jmed-llm-3-8x13b-AC-32k-instruct-Q8_0.gguf \
  --local-dir ./models
```

```
ollama create sip-jmed-8x13b-q8 -f models/sip-jmed-8x13b-q8.Modelfile
```

### [tokyotech-llm/GPT-OSS-Swallow-20B-SFT-v0.1](https://huggingface.co/tokyotech-llm/GPT-OSS-Swallow-20B-SFT-v0.1)

日英バイリンガル 21B パラメータモデル（GPT-OSS ベース、SFT 学習済み）。コンテキスト長 32K。

量子化: [sashisuseso/GPT-OSS-Swallow-20B-SFT-v0.1-MXFP4_MOE-GGUF](https://huggingface.co/sashisuseso/GPT-OSS-Swallow-20B-SFT-v0.1-MXFP4_MOE-GGUF)

#### MXFP4_MOE (~12GB)

```
hf download sashisuseso/GPT-OSS-Swallow-20B-SFT-v0.1-MXFP4_MOE-GGUF \
  --local-dir ./models/GPT-OSS-Swallow-20B-SFT-v0.1-MXFP4_MOE-GGUF
```

```
ollama create gpt-oss-swallow-20b-sft -f models/gpt-oss-swallow-20b-sft.Modelfile
```

### [tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1](https://huggingface.co/tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1)

日英バイリンガル 120B パラメータモデル（GPT-OSS ベース、RLVR 学習済み）。コンテキスト長 32K。

```
hf download tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1 \
  --local-dir ./models/GPT-OSS-Swallow-120B-RL-v0.1
```

```
ollama create gpt-oss-swallow-120b-rl -f models/gpt-oss-swallow-120b-rl.Modelfile
```

### [hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF](https://huggingface.co/hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF)

#### BF16 (~27GB)

```
hf download hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF \
  SIP-jmed-llm-3-13b-OP-32k-R0.1-BF16.gguf \
  --local-dir ./models
```

```
ollama create sip-jmed-13b -f models/sip-jmed-13b.Modelfile
```

#### Q8_0 (~15GB)

```
hf download hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF \
  SIP-jmed-llm-3-13b-OP-32k-R0.1-Q8_0.gguf \
  --local-dir ./models
```

```
ollama create sip-jmed-13b-q8 -f models/sip-jmed-13b-q8.Modelfile
```
