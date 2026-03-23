# chat.prism-hu.org

Visit https://chat.prism-hu.org

- OpenWebUI
- Ollama
- LiteLLM


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

### [tokyotech-llm/GPT-OSS-Swallow-20B-RL-v0.1](https://huggingface.co/tokyotech-llm/GPT-OSS-Swallow-20B-RL-v0.1)

日英バイリンガル 21B パラメータモデル（GPT-OSS ベース、RLVR 学習済み）。コンテキスト長 32K。

GGUF: [mmnga-o/GPT-OSS-Swallow-20B-RL-v0.1-gguf](https://huggingface.co/mmnga-o/GPT-OSS-Swallow-20B-RL-v0.1-gguf)

#### MXFP4_MOE (~24GB)

```
hf download mmnga-o/GPT-OSS-Swallow-20B-RL-v0.1-gguf \
  GPT-OSS-Swallow-20B-RL-v0.1-MXFP4_MOE.gguf \
  --local-dir ./models
```

```
docker exec open-webui ollama create gpt-oss-swallow-20b-rl -f /models/gpt-oss-swallow-20b-rl.Modelfile
```

### [tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1](https://huggingface.co/tokyotech-llm/GPT-OSS-Swallow-120B-RL-v0.1)

日英バイリンガル 120B パラメータモデル（GPT-OSS ベース、RLVR 学習済み）。コンテキスト長 32K。

GGUF: [mmnga-o/GPT-OSS-Swallow-120B-RL-v0.1-gguf](https://huggingface.co/mmnga-o/GPT-OSS-Swallow-120B-RL-v0.1-gguf)

#### MXFP4_MOE (~63GB, 6分割)

```
hf download mmnga-o/GPT-OSS-Swallow-120B-RL-v0.1-gguf \
  --include "MXFP4_MOE/*" \
  --local-dir ./models
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
