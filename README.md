# chat

PRISM-HU の**共有推論スタック**。DGX Spark 1 台の上で複数のモデルサーバを動かし、
**`:4000` の OpenAI 互換エンドポイント 1 本**に束ねて配る。

リポジトリ名の `chat` は最初の利用者だった chat.prism-hu.org（Nucllei）に由来するが、
**今は Nucllei はこのスタックの 1 サービスに過ぎない**。現在の利用者は 3 つ:

| 利用者 | 何 | どこ |
|---|---|---|
| Nucllei | このスタック同梱のフロントエンド | https://chat.prism-hu.org |
| ふぐ (fugu) | Hermes Agent。LINE / Discord に出る | 別リポジトリ [HokuMedAI/fugu](https://github.com/HokuMedAI/fugu)。本スタックの network / volume に相乗り |
| LLENS | 病院内の医療情報アシスタント | 別リポジトリ [prism-hu/llens](https://github.com/prism-hu/llens) |

**だから `:4000` を壊すと 3 つ全部が止まる。** モデルの増減やゲートウェイの変更は、
この 3 つへの影響を前提に考えること。

## 環境

NVIDIA DGX Spark (128GB 統合メモリ)

## スタック

入口から順に:

| サービス | 役割 |
|---|---|
| **prism-gw** | **唯一の入口 (`:4000`)**。キー 1 本で全モデルをゲートし、上流の応答を組み直さずに中継する。自前実装 (`gateway/app.py`, 322 行) / [`docs/gateway.md`](docs/gateway.md) |
| LiteLLM | 形式変換が要る上流（Anthropic / OpenAI / Gemini / ChatGPT サブスク）と Ollama の列挙を担当。**ホストには公開しない**（prism-gw の後ろだけ） |
| Nucllei | フロントエンド。Open WebUI フォーク（technoplasm）/ `vendor/nucllei` submodule |
| Ollama | ホスト実行。LiteLLM からだけ叩く |
| vLLM (Qwen3.5-122B) | DGX Spark SM121 最適化 / `vendor/qwen35-spark` submodule |
| vLLM (Qwen3.6-35B-A3B) | NVFP4 / 上流公式イメージ。35B MoE の VLM。実測 78.5 t/s で ollama の同モデルより +38%。[`docs/qwen36-vllm.md`](docs/qwen36-vllm.md) |
| llama.cpp (Step-3.7-Flash) | 198B MoE VLM, GGUF IQ4_XS / `llamacpp/Dockerfile`。**vLLM の Qwen3.5 とは排他**（105GB + 100GB で 128GB に収まらない）。切り替えは [`docs/step37-llamacpp.md`](docs/step37-llamacpp.md) |

**GPU を使うサービスは排他。** 起動しているものだけが `/v1/models` に出る。

## エンドポイントとモデル

**エンドポイント:** `http://<HOST>:4000/v1`（Tailscale 経由。prism-gw。LiteLLM は後ろに隠れている）
**APIキー:** `.env` の `PRISM_GW_API_KEY`。**このスタックで唯一クライアントに渡すキー**で、
これ 1 本で全モデルに到達できる。上流のキー（Anthropic / OpenAI / Gemini / 北大）は
prism-gw と LiteLLM が差し替えるのでクライアントには出ない。

### 利用可能なモデル

| model_name | 経路 | バックエンド | 課金 | 備考 |
|---|---|---|---|---|
| `claude-opus-4-6` / `-sonnet-4-6` | LiteLLM | Anthropic | サブスク (OAuth) | `CLAUDE_OAUTH_TOKEN` |
| `gpt-5.6-luna` / `-terra` / `-sol` | LiteLLM | OpenAI 公式 API | **従量** | 下位 / 中位 / フラッグシップ |
| `codex/gpt-5.6-luna` / `-terra` / `-sol` / `codex/gpt-5.5` | LiteLLM | ChatGPT backend | **サブスク枠** | 中身は上と同じモデルだが課金先が違う。**`stream: true` 必須** |
| `gemini-3.8-flash` | LiteLLM | Google AI Studio | 従量 (or $10/月クレジット) | thinking 既定 ON、`reasoning_effort: medium` |
| `ollama/<name>` | LiteLLM | Ollama (ホスト実行) | — | ワイルドカード。`ollama pull` したものが自動で並ぶ |
| `qwen3.6-35b` | **透過** | vLLM (NVFP4) | — | MoE VLM / vision / 78.5 tok/s |
| `qwen3.5-122b-custom` | **透過** | カスタム vLLM (SM121) | — | 122B-A10B。下記の排他枠 |
| `step-3.7-flash` | **透過** | llama.cpp (GGUF IQ4_XS) | — | 198B-A11B。下記の排他枠 |

「透過」= prism-gw が上流へ素通しする経路。`max_model_len` とストリーム中の累積 usage が
生きたまま届く（理由は[下記](#なぜ-prism-gw-があるのか2026-08-14-実測)）。

**ローカルの大物 3 つ（`qwen3.5-122b-custom` / `step-3.7-flash` / `qwen3.6-35b`）は
128GB に同時に載らないので排他運用。** 起動していない上流は prism-gw が自動的に
`/v1/models` から外すので、**一覧に出ているものが今動いているもの**。この README に
「今どれが動いているか」は書かない（すぐ嘘になる）。上の curl で見ること。

**モデルの増減はまずここを疑う前に実物を見ること:**

```bash
# 実際に何が出るかは常にこれが正
curl -s http://<HOST>:4000/v1/models -H "Authorization: Bearer $PRISM_GW_API_KEY" \
  | python3 -c 'import sys,json;[print(m["id"]) for m in json.load(sys.stdin)["data"]]'
```

> **`kimi-k2.6`（北大 llens / H200）は 2026-09-21 に撤去した。** H200 がこのネットワークから
> 到達できない場所へ移設されたため。死んだ上流を残すと `/v1/models` が毎回 connect timeout
> 分だけ待たされる（実測 8.02s → 0.005s）。復活手順は `gateway/config.yaml` のコメント。

### 使い方

```python
from openai import OpenAI

client = OpenAI(base_url="http://<HOST>:4000/v1", api_key="<PRISM_GW_API_KEY>")
response = client.chat.completions.create(
    model="gemini-3.8-flash",
    messages=[{"role": "user", "content": "こんにちは"}],
)
```

```bash
curl http://<HOST>:4000/v1/chat/completions \
  -H "Authorization: Bearer <PRISM_GW_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model": "ollama/gpt-oss:20b", "messages": [{"role": "user", "content": "こんにちは"}]}'
```

### なぜ prism-gw があるのか（2026-08-14 実測）

**LiteLLM はストリームを素通しせず組み直す。** 上流（特に sglang / vLLM）の拡張は落ちるので、
Nucllei のトークン表示に効いてくる。v1.82.3 と 1.96.2 のソースで確認した挙動:

| 何 | 挙動 | 影響 |
|---|---|---|
| チャンクごとの累積 usage | `stream_options` は上流まで素通しするので **上流は返している**が、LiteLLM が受信側で全チャンクから usage を削除し（`streaming_handler.py` の `# remove usage from chunk, only send on final chunk`）最後に自前で 1 個合成する。1.96 でも同じ | **生成中のライブ tok/s が出ない**（確定値の表示は出る） |
| 最終 usage フレームの形 | sglang は `choices: []` の専用フレーム。LiteLLM の合成チャンクは `choices: [{"delta": {}}]` | Nucllei 側で対応済み（technoplasm/nucllei#112） |
| `/v1/models` の context 長 | 1.82 は id/object/created/owned_by の 4 キーのみ。**1.96 から `max_input_tokens` を返す**（`model_info` の設定値が優先） | ゲージの分母。1.96 未満では出ない |
| `usage.reasoning_tokens` | sglang 拡張。OpenAI 形の `completion_tokens_details.reasoning_tokens` に入れ替わる | 表示に使っていないので実害なし |

いずれも config のノブでは変えられない（ハードコード）。**そこで OpenAI 互換の上流は
LiteLLM に載せず、prism-gw が直接・無改変で中継する。** 形式変換が要る Anthropic /
OpenAI / Gemini / ChatGPT と、Ollama のワイルドカード列挙だけを LiteLLM が担当する。

> 以前は LiteLLM の `general_settings.pass_through_endpoints`（`/sglang`）で同じことを
> していたが、prism-gw に一本化して廃止した。Nucllei も `OPENAI_API_BASE_URL` 1 本
> （`http://prism-gw:4000/v1`）だけを見る。

## Nucllei (フロントエンド)

[technoplasm/nucllei](https://github.com/technoplasm/nucllei)（Open WebUI v0.6.5 フォーク）を `:8080` で配信。

**自前ビルドが必須**: 上流 CI が GHCR に出すイメージは **amd64 のみ**で、DGX Spark は aarch64。
そのため GHCR から pull せず `vendor/nucllei` submodule から arm64 をネイティブビルドする。

```bash
git submodule update --init --recursive
docker compose build nucllei          # 初回 ~数分（pnpm build + uv sync）
docker compose up -d --no-deps nucllei

# 最初の管理者はサーバー側で作る（CLI は console script `nucllei`）
docker exec -it nucllei nucllei create-admin --email <you>
```

- 版を上げるとき: submodule を目的の tag へ進めて（`cd vendor/nucllei && git fetch && git checkout <tag>`）再ビルド。
  ピンした commit がこのリポジトリの版の真実源。
- `.dockerignore` が `.git` を除くため UI の版表示は `package.json` フォールバックになる。
  正確に出したければ `docker compose build --build-arg APP_VERSION=<tag> nucllei`。
- 永続状態は volume `nucllei:/app/data`（SQLite `webui.db` / アップロード / secret key）。
  旧 OpenWebUI からの移行時、**旧 volume `open-webui` のデータ（チャット履歴・ユーザー）は引き継がれない**
  （別 volume かつ fork 後に DB スキーマが分岐）。旧 volume はロールバック用に残してある。
- 設定は env が正（`docker-compose.yml`）。env を空にした項目のみ管理画面の保存値が生きる。
- モデル源は prism-gw 一本（`ENABLE_OLLAMA_API=false`）。Ollama にも LiteLLM にも直結しない。

> LiteLLM はワイルドカード定義そのもの (`ollama/*`) も一覧に混ぜてくるが（選んでも動かない）、
> prism-gw が `include.exclude` で落とすので出てこない（`gateway/config.yaml`）。

## Ollama 設定

Ollama はホストで動作する。**叩くのは LiteLLM だけ**（`litellm/config.yaml` の `ollama/*` が
`http://172.28.0.1:11434` を向く）。Nucllei からは直結しない。

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

## Qwen3.5-122B カスタム vLLM

`qwen3.5-122b-custom` は SM121 向けに**自前ビルドした vLLM イメージ**（`ghcr.io/prism-hu/vllm-qwen35-v2`、
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
