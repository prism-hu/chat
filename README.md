# chat.prism-hu.org

Visit https://chat.prism-hu.org

## 環境

NVIDIA DGX Spark (128GB 統合メモリ)

## スタック

- OpenWebUI
- Ollama (ホスト実行)
- LiteLLM
- vLLM (Qwen3.5-122B, DGX Spark SM121 最適化 / `vendor/qwen35-spark` submodule)
- Hermes Agent (Nous Research / バックエンドは qwen3.5-122b)

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

`qwen3.5-122b-custom` は SM121 向けに**自前ビルドした vLLM イメージ**（`ghcr.io/prism-hu/vllm-qwen35-v2`、
GHCR から pull）で配信している。albond のフォーク（`vendor/qwen35-spark` submodule）をベースに
INT4+FP8 hybrid / MTP-2 / FlashInfer で最適化（~52 tok/s）。

**セットアップ・動作チェック・GHCR 配布・バージョン経緯・トラブルシュートは
[docs/qwen35-vllm.md](docs/qwen35-vllm.md) に集約。**

submodule 取得: `git submodule update --init --recursive`

## Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent)（Nous Research）を **docker-compose
サービス**として同梱。永続メモリ・学習スキル・ツール呼び出しを持つセルフホスト型エージェント。
**ホストには一切インストールしない**（compose で完結）。

- バックエンドは**ローカルの Qwen3.5-122B vLLM に直結**（`vllm-qwen35:8000`、API キー不要）。
  検証済み: `Model: qwen` / `Provider: Custom endpoint`。
- メモリ／スキル／セッションは named volume `hermes-data` に永続化。`hermes/config.yaml` は
  `/opt/data/config.yaml` に **rw** マウント（Hermes が自己管理。repo 側は seed/example 扱い）。
- モデル切替や LiteLLM 経由（`qwen3.5-122b-custom`、要 `LITELLM_MASTER_KEY`）にする場合は
  `hermes/config.yaml` を編集（LiteLLM 経由の例も同ファイルにコメントで記載）。

### Web UI（dashboard）

コンテナ常駐プロセスは **dashboard**（`:9119`）。config / セッション / 埋め込みチャットを持つ Web UI。
**HTTP Basic 認証（フォームログイン）で保護**しており、未認証は `/login` へリダイレクト・API は 401。

- 認証プラグイン `basic` を `hermes/config.yaml` の `plugins.enabled` で有効化（dashboard は
  非ループバック bind 時に auth provider 未登録だと**起動を拒否**する fail-closed 仕様）。
- 資格情報は `.env` 由来（`HERMES_DASHBOARD_BASIC_AUTH_USERNAME/PASSWORD/SECRET`）。
  compose では `${VAR:?…}` の必須参照にしてあるので、未設定だと `up` がエラーで止まる。
  → **`.env` のプレースホルダを実値に必ず差し替えること**（`.env.example` 参照）。

```bash
docker compose up -d hermes        # 起動（初回 pull）
# ブラウザで http://<HOST>:9119 → ログイン（.env の USERNAME / PASSWORD）
docker compose logs -f hermes
```

> ⚠️ `--insecure` は付けないこと（非ループバック bind を許可すると同時に**認証ゲートを無効化**し、
> API キー管理画面を無認証で晒す）。

### CLI で使う

```bash
docker exec -it hermes hermes chat                            # 対話
docker exec hermes hermes -z "日本語で自己紹介して" --yolo --cli  # 一発実行（qwen）
docker exec hermes hermes status                              # 状態 / モデル確認
```

設定変更後は `docker compose restart hermes` で反映。

> メッセージング（Telegram/Slack/...）から使いたい場合は別途 `gateway` を有効化し、資格情報を
> `hermes-data` volume 内の `/opt/data/.env` に置く（repo には入れない）。

### メッセージング & 投稿スキル

プラットフォーム資格情報は `.env` → compose の `environment:` で hermes に注入する
（`hermes-data` volume 内の `/opt/data/.env` ではなく repo の `.env` を正とする運用）。

**稼働中:**

- **Discord** — `DISCORD_BOT_TOKEN` ほか。`DISCORD_ALLOWED_USERS` が空なら全員許可。
  home チャンネル（`DISCORD_HOME_CHANNEL`）は free-response（メンション不要）、他チャンネルは
  メンション or bot へのリプライで反応。
- **LINE** — `LINE_*`。webhook は `:8646/line/webhook`（cloudflared 経由で
  `https://hermes-gateway.prism-hu.org` に公開）。グループの発言は **「ふぐ」を含むテキストのみ**
  反応（下記 fugu-gate）。DM は常時反応。

**スキル**（`hermes/skills/` に reference 実装。`hermes-data` volume の
`/opt/data/skills/...` に配置して `hermes skills list` で有効化）:

| skill | 状態 | 内容 |
|---|---|---|
| `line-group-post` | ✅ 稼働 | LINE push API で指定グループへ投稿（`hermes send` はメッセージ受信済みチャットしか宛先解決できないため、push で直接送る） |
| `xurl`（builtin） | ⏸ 課金待ち | X 公式 CLI。OAuth1 認証済み（`/opt/data/home/.xurl`）だが、**X は 2026 従量課金で投稿にクレジット必須**（`POST /2/tweets` → `CreditsDepleted`）。課金すれば即投稿可 |
| `x-post`（reference） | ❌ 不可 | twikit（非公式・無料）。twikit 同梱の `x-client-transaction-id` 生成が X 現行 JS に追従できず失敗（"KEY_BYTE indices"）。サーバーからは塞がれている |
| `ig-post`（reference） | ❌ 不可 | instagrapi（非公式・無料）。サーバー IP からの初回ログインで Instagram の challenge に阻まれる |

> `x-post` / `ig-post` は**動かないが参考実装として repo に残置**（`hermes/skills/{x-post,ig-post}/`）。
> live システム（volume のスキル・venv・秘密）からは撤去済み。経緯・教訓は auto-memory
> `social-posting-status` / `unofficial-social-api-server-wall` 参照。再挑戦するなら residential
> proxy か、X は公式課金（xurl）が現実解。

**cont-init フック**（`hermes/init/`、compose で `/etc/cont-init.d/` に `:ro` bind。
イメージ内を起動毎に冪等パッチ＝recreate で消えないようにする。`#!/command/with-contenv sh`
かつホスト側に実行ビット必須）:

- `10-line-fugu-gate.sh` — ✅ LINE adapter にグループ用「ふぐ」メンションゲートを当てる。
- `20-social-creds.sh` — reference。`.env` の secret を skill 用ファイルへ展開するブリッジ
  （agent のサンドボックスは env を読めないため）。X/IG 撤去に伴い**未マウント**。パターンは
  auto-memory `hermes-skill-secret-bridge` 参照。

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
