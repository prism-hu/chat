# chat.prism-hu.org

Visit https://chat.prism-hu.org

## 環境

NVIDIA DGX Spark (128GB 統合メモリ)

## スタック

- OpenWebUI
- Ollama
- LiteLLM


## LiteLLM API (外部アクセス)

Tailscale経由でOpenAI互換APIとして利用可能。

**エンドポイント:** `http://<HOST>:4000/v1`
**APIキー:** `.env` の `LITELLM_MASTER_KEY`

### 利用可能なモデル

| model_name | 内容 |
|---|---|
| `claude-opus-4-6` | Claude Opus 4.6 |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 |
| `gpt-oss-20b` | GPT-OSS 20B |
| `gpt-oss-120b` | GPT-OSS 120B |
| `sip-jmed-13b` | SIP-jmed 13B |
| `sip-jmed-8x13b-q8` | SIP-jmed 8x13B Q8 |
| `nemotron-3-nano` | Nemotron-3 Nano |
| `nemotron-3-super` | Nemotron-3 Super |
| `qwen3.5-9b` | Qwen3.5 9B |
| `qwen3.5-27b` | Qwen3.5 27B |

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

## Models

### モデルの追加方法

前提: `pip install huggingface-hub`

1. GGUF ファイルを `./models/` にダウンロード (`hf download`)
2. `models/<name>.Modelfile` を作成 (`FROM /models/<gguf-file>`)
3. Ollama に取り込み:

```
docker exec open-webui ollama create <name> -f /models/<name>.Modelfile
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
docker exec open-webui ollama create sip-jmed-8x13b -f /models/sip-jmed-8x13b.Modelfile
```

#### Q5_K_M (~36GB)

```
hf download hiratagoh/SIP-jmed-llm-3-8x13b-AC-32k-instruct-GGUF \
  SIP-jmed-llm-3-8x13b-AC-32k-instruct-Q5_K_M.gguf \
  --local-dir ./models
```

```
docker exec open-webui ollama create sip-jmed-8x13b-q5 -f /models/sip-jmed-8x13b-q5.Modelfile
```

#### Q8_0 (~78GB)

```
hf download hiratagoh/SIP-jmed-llm-3-8x13b-AC-32k-instruct-GGUF \
  SIP-jmed-llm-3-8x13b-AC-32k-instruct-Q8_0.gguf \
  --local-dir ./models
```

```
docker exec open-webui ollama create sip-jmed-8x13b-q8 -f /models/sip-jmed-8x13b-q8.Modelfile
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
docker exec open-webui ollama create gpt-oss-swallow-20b-sft -f /models/gpt-oss-swallow-20b-sft.Modelfile
```

### [tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1](https://huggingface.co/tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1)

日英バイリンガル 120B パラメータモデル（GPT-OSS ベース、RLVR 学習済み）。コンテキスト長 32K。

```
hf download tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1 \
  --local-dir ./models/GPT-OSS-Swallow-120B-RL-v0.1
```

```
docker exec open-webui ollama create gpt-oss-swallow-120b-rl -f /models/gpt-oss-swallow-120b-rl.Modelfile
```

### [hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF](https://huggingface.co/hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF)

#### BF16 (~27GB)

```
hf download hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF \
  SIP-jmed-llm-3-13b-OP-32k-R0.1-BF16.gguf \
  --local-dir ./models
```

```
docker exec open-webui ollama create sip-jmed-13b -f /models/sip-jmed-13b.Modelfile
```

#### Q8_0 (~15GB)

```
hf download hiratagoh/SIP-jmed-llm-3-13b-OP-32k-R0.1-GGUF \
  SIP-jmed-llm-3-13b-OP-32k-R0.1-Q8_0.gguf \
  --local-dir ./models
```

```
docker exec open-webui ollama create sip-jmed-13b-q8 -f /models/sip-jmed-13b-q8.Modelfile
```
