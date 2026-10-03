# prism-gw — 唯一の入口

`:4000` の `/v1` 一本を入口にし、**クライアントのキーを 1 本に束ねる**ための自前
ゲートウェイ。`gateway/app.py`（Python / 約 400 行）と `gateway/config.yaml` だけ。

```
Nucllei / fugu / 外部 ──(PRISM_GW_API_KEY 1本)──> prism-gw :4000 /v1
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

- **認証**: `auth.key`（= `PRISM_GW_API_KEY`）と Bearer を突き合わせる。
  上流のキー（`KIMI_API_KEY` 等）はここで差し替わり、**クライアントには出ない**
- **ルーティング**: リクエストボディの `model` を見て上流を決める。パスは `/v1` のまま
  なので、**Nucllei に登録する OpenAI エンドポイントは 1 個で済む**
- **`/v1/models`**: 全上流を引いて 1 つに統合。どこから来た値でも `max_model_len` に
  正規化して載せる（Nucllei の `get_model_context_size` はこのキーしか見ない）。
  LiteLLM 1.96+ の `max_input_tokens` もここで読み替える
- **ライブ tok/s**: 上流が対応していれば `stream_options.continuous_usage_stats` を
  **こちらで差し込む**。クライアント（Nucllei）が送らなくても効く
- **LiteLLM 経路だけ行単位で補正**（`normalize_stream: true`）: 既に組み直された後で
  守る生バイトが無いので、落ちたキーを写し、取り違えた `finish_reason` を直す。
  やることは下の「LiteLLM 経路の差分」の 2 点のみで、値は作らない。直上流は従来どおり
  `aiter_raw()` の素通し（コード上も別経路）
- 停止中の上流は `/v1/models` から自動で消える（`vllm-qwen35` と `llamacpp-step37` は
  普段停止しているので、一覧に出るのは起動しているものだけ）
- **Ollama が止まっている間は `ollama/...` を一覧に出さない**（`include` の
  `require_alive`）。LiteLLM は Ollama に繋がらないと静的な組み込みリストに
  フォールバックして実在しない `ollama/llama2` を返すので、ゲートウェイ側で
  Ollama の `/api/version` を直接叩いて確かめる。確認は一覧取得と並列・タイムアウト
  1 秒。効くのは一覧だけで、ルーティングは変えない

## 設定

すべて `gateway/config.yaml` に宣言的に書く。`${VAR}` / `${VAR:-既定値}` が使える。

```yaml
auth:
  key: ${PRISM_GW_API_KEY}

upstreams:
  sglang-llens:
    base_url: http://llens.med.hokudai.ac.jp:13300/v1
    api_key: ${KIMI_API_KEY:-}
    force_continuous_usage: true      # ライブ tok/s を上流に要求する
  litellm:
    base_url: http://litellm:4000/v1
    api_key: ${LITELLM_MASTER_KEY}
    normalize_stream: true            # LiteLLM 経路だけ SSE を行単位で補正（下記）

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
    require_alive:                     # url が応答する間だけ glob に合うものを載せる
      - glob: "ollama/*"
        url: http://172.28.0.1:11434/api/version
    skip_declared: true
```

config だけの変更なら再ビルド不要（config はマウントしてある）:

```bash
docker compose restart prism-gw
```

`app.py` はイメージに焼いてある（マウントしていない）ので、こちらを変えたら再ビルド:

```bash
docker compose up -d --build prism-gw
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

## LiteLLM 経路の差分（2026-10-03 実測）

Nucllei は SGLang の生フレーム（`vendor/nucllei/_memos/model-frames.md`）を正とする。
`codex/gpt-5.6-luna`（LiteLLM `chatgpt/` プロバイダ、OAuth、Responses API 経由）で
観察された 6 点を、**LiteLLM 直**（`172.28.0.2:4000`）・**ChatGPT backend 直**
（`chatgpt.com/backend-api/codex/responses`、litellm コンテナ内から同一リクエストを再生）・
**gw 経由**の 3 段で SSE 行ごとに到着時刻付きで採取し、`claude-sonnet-4-6` を
LiteLLM 経路の対照にして切り分けた。

| # | 現象 | 発生源 | 根拠 | gw の対応 |
|---|---|---|---|---|
| 1 | `usage.reasoning_tokens` が無い | **LiteLLM**（一般） | claude / codex とも `completion_tokens_details.reasoning_tokens` にだけ載る。SGLang 拡張キーを LiteLLM は知らない | **写す**。usage を持つチャンクで `completion_tokens_details.reasoning_tokens` を `usage.reasoning_tokens` にもコピー（元は残す） |
| 2 | usage が最後の 1 回だけ | **backend**（両方） | Responses API は usage を `response.completed` でしか返さない（backend 直で確認）。Anthropic も `message_delta` 末尾のみ。`stream_options.continuous_usage_stats` を LiteLLM に送っても無視される。**途中値が存在しない**ので作りようがない | なし（数値を捏造しない）。クラウド経路はライブ tok/s 非対応のまま |
| 3 | reasoning が終わるまで何も流れない | **backend + LiteLLM** | backend は reasoning 中、`output_item.added`（暗号化 reasoning）以外のイベントを出さない。LiteLLM proxy は中身のあるチャンクが出るまで **HTTP ヘッダすら返さない**（実測: ヘッダ到着 = 最初の content デルタ = 4.5 s）。その後の content / tool 引数は 5〜30 ms 間隔で**ちゃんと逐次流れる**（49.5 s → 1.4 s は「無音 48 s + 本文 1.4 s」であって一括到着ではない）。gw 旧版（素通し）でも新版でも行到着時刻は LiteLLM 直と一致 | なし。gw は行単位でバッファせず即時に流す |
| 4 | reasoning が暗号化 `reasoning_items` | **backend**（+ LiteLLM の既定） | backend は `encrypted_content` のみで `summary: []`。LiteLLM は `reasoning.summary` を要求しない。**クライアントが `reasoning_effort: {"effort":"medium","summary":"auto"}` と dict で送れば** LiteLLM はそのまま通し、backend の要約が `delta.reasoning_content`（平文）で返る（実測 1 デルタ、ただし reasoning 完了後に届くので #3 の無音は解消しない）。生 CoT は出ない | なし。effort の意味が変わるので gw では差し込まない。欲しければ Nucllei 側で `codex/*` に dict を送る |
| 5 | tool_calls を流したのに `finish_reason: "stop"` | **backend → LiteLLM** | backend の `response.completed` は `output: []`（公式 OpenAI と違い output を再掲しない）。LiteLLM の responses→chat 変換はその `output` に `function_call` があるかだけで finish_reason を決める（`completion_extras/litellm_responses_transformation/transformation.py`）ので "stop" に倒れる。claude 経由は "tool_calls" で正しい | **直す**。ストリーム中に `delta.tool_calls` を見ていて finish が "stop" なら "tool_calls" に書き換え。OpenAI 仕様上この組み合わせは他にならない |
| 6 | 15:45 の 500 | **backend + LiteLLM（非ストリーム）** | litellm ログ 15:45:50: `ValueError: Unknown items in responses API response: []` in `transform_response`（**非ストリーム経路**）、LiteLLM が 2 回リトライして 500。`litellm/config.yaml` の注意書きどおり `codex/*` は `stream: false` だと落ちる。直前のチャット成功から 0.5 s 後の短い呼び出しなので、Nucllei のタイトル生成等の**非ストリーム補助呼び出し**が原因と見られる（1 回で backend 3 発消費） | なし。`codex/*` への補助呼び出しも `stream: true` にするのは Nucllei 側 |

付随して観察した形の差（直さない）: LiteLLM の usage フレームは `choices: []` ではなく
`choices: [{"index":0,"delta":{}}]`。role フレームは無く最初の content デルタに
`role` が同居する。`matched_stop` は無い。

補正は `gateway/app.py` の `_normalize_sse`（行単位、`data: {` の行だけ JSON を見る、
変えた行だけ再シリアライズ、それ以外はバイトのまま）。直上流はこの経路を通らない。

## 動作確認

```bash
K=$(grep '^PRISM_GW_API_KEY=' .env | cut -d= -f2-)

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
  対応しておらず、失っているものは実質ない（上の「LiteLLM 経路の差分」#2）
- **`codex/*` は reasoning 中、無音。** backend が暗号化 reasoning しか出さず、LiteLLM は
  最初の中身が出るまでヘッダも返さない（同 #3 / #4）。`stream: false` は 500（同 #6）
- **Ollama のモデルは `max_model_len` が付かない。** LiteLLM のワイルドカード経路が
  返さないため。必要なら `include` に `context_overrides` を足す余地はある
- キー 1 本で全モデルに到達できる = **そのキーが漏れれば全部使われる**。これは
  LiteLLM master key 時代と同じ性質で（値も同一のまま移行した）、悪化はしていない。モデル別に絞りたくなったら
  `auth` を複数キー + 許可モデル一覧に拡張する（数十行で足りる）
