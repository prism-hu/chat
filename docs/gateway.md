# prism-gw — 唯一の入口

`:4000` の `/v1` 一本を入口にし、**クライアントのキーを 1 本に束ねる**ための自前
ゲートウェイ。`gateway/app.py`（Python / 約 250 行）と `gateway/config.yaml` だけ。

```
Nucllei / fugu / 外部 ──(GATEWAY_KEY 1本)──> prism-gw :4000 /v1
                                               ├─ kimi-k2.6       → sglang (llens)   透過
                                               ├─ qwen3.6-35b     → vLLM             透過
                                               ├─ qwen3.5-122b-custom → vLLM         透過
                                               ├─ step-3.7-flash  → llama.cpp        透過
                                               └─ その他           → LiteLLM          (Claude / OpenAI / Ollama)
```

## なぜ自前なのか

要件は 3 つ:

1. クライアントのキーは 1 本
2. コンテキスト長が見える
3. 生成中のライブ tok/s が見える

**既製の LLM ゲートウェイは 2 と 3 を構造的に満たせない。** 彼らの仕事は各社バラバラの
API を OpenAI 形式へ正規化することなので、正規化の過程で仕様外のキーが落ちる。
同一条件で実測した結果 (2026-08-15):

| 経路 | ストリームのチャンク数 | 累積 usage 付き |
|---|---|---|
| **sglang 直**（`continuous_usage_stats: true`） | 41 | **39** |
| LiteLLM 1.96.2 経由 | 43 | 1 |
| Bifrost (maximhq) 経由 | 44 | 1 |
| **prism-gw 経由** | 47 | **45** |

`/v1/models` も同じで、sglang / vLLM が返す `max_model_len` は LiteLLM でも Bifrost でも
消える（Bifrost はローカルモデルの `context_length` が `None` になる）。

LiteLLM には認証付きの生 pass-through 機能があるが **Enterprise 限定**で、無料版では
`auth: true` を書いた時点で proxy 全体が起動不能になる（`docs/` 履歴 / PR #1・#3）。

そこで「**組み直さないこと**」だけを仕事にする中継を置いた。ルーティングとヘッダの
差し替えしかせず、本文は `aiter_raw()` でバイトのまま流す。

## 仕組み

- **認証**: `auth.key`（既定で `LITELLM_MASTER_KEY` を流用）と Bearer を突き合わせる。
  上流のキー（`KIMI_API_KEY` 等）はここで差し替わり、**クライアントには出ない**
- **ルーティング**: リクエストボディの `model` を見て上流を決める。パスは `/v1` のまま
  なので、**Nucllei に登録する OpenAI エンドポイントは 1 個で済む**
- **`/v1/models`**: 全上流を引いて 1 つに統合。どこから来た値でも `max_model_len` に
  正規化して載せる（Nucllei の `get_model_context_size` はこのキーしか見ない）。
  LiteLLM 1.96+ の `max_input_tokens` もここで読み替える
- **ライブ tok/s**: 上流が対応していれば `stream_options.continuous_usage_stats` を
  **こちらで差し込む**。クライアント（Nucllei）が送らなくても効く
- 停止中の上流は `/v1/models` から自動で消える（`vllm-qwen35` と `llamacpp-step37` は
  普段停止しているので、一覧に出るのは起動しているものだけ）

## 設定

すべて `gateway/config.yaml` に宣言的に書く。`${VAR}` / `${VAR:-既定値}` が使える。

```yaml
auth:
  key: ${GATEWAY_KEY}

upstreams:
  sglang-llens:
    base_url: http://llens.med.hokudai.ac.jp:13300/v1
    api_key: ${KIMI_API_KEY:-}
    force_continuous_usage: true      # ライブ tok/s を上流に要求する

models:                                # 明示ルート
  - name: kimi-k2.6                    # クライアントから見える名前
    upstream: sglang-llens
  - name: qwen3.6-35b
    upstream: vllm-qwen36
    upstream_model: qwen36             # 上流での実名
    # context: 262144                  # 上流が max_model_len を返さない場合だけ書く

include:                               # 上流の一覧をそのまま取り込む
  - upstream: litellm
    context_from: [max_model_len, max_input_tokens]
    exclude: ["ollama/*"]              # **完全一致**。配下の実モデルは残る
    skip_declared: true
```

反映は再ビルド不要（config はマウントしてある）:

```bash
docker compose restart prism-gw
```

## LiteLLM の役割

**形式変換が要るものだけ**に縮小した:

- `claude-*` — Anthropic API ⇄ OpenAI 形式の変換
- `gpt-5.6-*` — OpenAI 公式（`drop_params` で推論モデルの非対応パラメータを落とす）
- `ollama/*` — ワイルドカード展開（`/api/tags` を引いて実在モデルを列挙）

OpenAI 互換な上流（vLLM / sglang / llama.cpp）は **LiteLLM に載せない**。載せると
`max_model_len` と累積 usage が消える。ホストにも公開していない（入口は prism-gw 一本）。

**版は `v1.96.2` に固定してある。** `main-stable` / `main-latest` は 1.82 系で止まっており
（2026-08 時点で実測）、1.82 の `/v1/models` は 4 キーしか返さないので `model_info` を
書いてもコンテキスト長が出ない。

## 動作確認

```bash
K=$(grep '^LITELLM_MASTER_KEY=' .env | cut -d= -f2-)

# 統合された一覧（全モデルに max_model_len が付く）
curl -s localhost:4000/v1/models -H "Authorization: Bearer $K" \
  | python3 -c 'import sys,json;[print(f"{m[\"id\"]:26}{m.get(\"max_model_len\")}") for m in json.load(sys.stdin)["data"]]'

# 認証（401 になること）
curl -s -o /dev/null -w "%{http_code}\n" localhost:4000/v1/models

# ライブ tok/s（usage 付きチャンクが多数あること）
curl -s -N localhost:4000/v1/chat/completions -H "Authorization: Bearer $K" \
  -H 'Content-Type: application/json' \
  -d '{"model":"kimi-k2.6","max_tokens":80,"stream":true,
       "messages":[{"role":"user","content":"1から30まで数えて"}]}' \
  | grep -c '"completion_tokens"'
```

## 制約

- **Claude / OpenAI / Ollama はライブ tok/s が出ない。** LiteLLM を通る経路なので
  組み直しの影響を受ける。ただしクラウド API はそもそも `continuous_usage_stats` に
  対応しておらず、失っているものは実質ない
- **Ollama のモデルは `max_model_len` が付かない。** LiteLLM のワイルドカード経路が
  返さないため。必要なら `include` に `context_overrides` を足す余地はある
- キー 1 本で全モデルに到達できる = **そのキーが漏れれば全部使われる**。これは
  LiteLLM master key 時代と同じ性質で、悪化はしていない。モデル別に絞りたくなったら
  `auth` を複数キー + 許可モデル一覧に拡張する（数十行で足りる）
