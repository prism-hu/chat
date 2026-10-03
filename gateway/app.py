"""prism-gw — OpenAI 互換の透過ゲートウェイ.

設計方針はひとつ: **応答を組み直さない**。

LiteLLM も Bifrost も、上流の応答を自前のスキーマへ正規化してから再構築する。
その過程で OpenAI 仕様に無いキーが落ちる。実測 (docs/gateway.md):

  - sglang が返す `max_model_len`      → 消える (Nucllei のゲージが点かない)
  - ストリーム中の累積 usage           → 43 チャンク中 1 個に削られる (ライブ tok/s が死ぬ)

ここでは中継しかしない。ルーティングとヘッダの差し替えだけを行い、本文は
`aiter_raw()` でバイトのまま流す。

唯一の例外が `normalize_stream` を付けた上流 (= LiteLLM)。そこは**既に組み直された
後**なので守るべき生バイトが無く、LiteLLM が落とした/取り違えたキーを行単位で
補う (`_normalize_sse`)。直上流 (sglang / vLLM / llama.cpp) には一切触れない。
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger("prism-gw")

CONFIG_PATH = os.environ.get("GATEWAY_CONFIG", "/app/config.yaml")
# ストリームは分単位で続きうるので read タイムアウトは張らない。接続だけ縛る。
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)
# 中継してはいけないヘッダ。長さと転送方式は httpx が張り直す。
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
    "content-encoding", "host",
}

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(node: Any) -> Any:
    """${VAR} / ${VAR:-default} を環境変数に展開する。"""
    if isinstance(node, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), node)
    if isinstance(node, dict):
        return {k: _expand(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand(v) for v in node]
    return node


class Config:
    def __init__(self, raw: dict):
        self.key: str = (raw.get("auth") or {}).get("key") or ""
        self.upstreams: dict[str, dict] = raw.get("upstreams") or {}
        self.includes: list[dict] = raw.get("include") or []
        # クライアントから見える名前 -> ルート
        self.routes: dict[str, dict] = {}
        for m in raw.get("models") or []:
            name = m["name"]
            self.routes[name] = {
                "upstream": m["upstream"],
                "upstream_model": m.get("upstream_model", name),
                "context": m.get("context"),
            }

    def upstream(self, name: str) -> dict:
        try:
            return self.upstreams[name]
        except KeyError:
            raise RuntimeError(f"unknown upstream: {name}") from None


def load_config() -> Config:
    with open(CONFIG_PATH) as f:
        raw = _expand(yaml.safe_load(f))
    cfg = Config(raw)
    if not cfg.key:
        raise RuntimeError("auth.key が空。PRISM_GW_API_KEY を設定すること。")
    log.info(
        "loaded %d upstream(s), %d declared model(s), %d include(s)",
        len(cfg.upstreams), len(cfg.routes), len(cfg.includes),
    )
    return cfg


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or ""
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def _authorized(request: Request) -> bool:
    return _bearer(request) == request.app.state.cfg.key


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        {"error": {"message": "Invalid API key", "type": "invalid_request_error", "code": 401}},
        status_code=401,
    )


def _upstream_headers(request: Request, up: dict) -> dict[str, str]:
    """クライアントのヘッダを引き継ぎつつ、認証だけ上流のものに差し替える。"""
    out = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    api_key = up.get("api_key")
    if api_key:
        out["authorization"] = f"Bearer {api_key}"
    else:
        # 認証不要な上流 (ローカル vLLM 等) にこちらのキーを渡さない
        out.pop("authorization", None)
        out.pop("Authorization", None)
    return out


def _response_headers(resp: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP}


# 一覧取得のタイムアウト。**connect だけ短くする**のが肝。
# ホストごと落ちた上流は SYN に応答が無く、connect が満了するまで丸々待たされる。
# read を短くすると生きている上流が重いときに取りこぼすので、そちらは据え置く。
_MODELS_TIMEOUT = httpx.Timeout(8.0, connect=2.0, pool=2.0)


async def _fetch_models(client: httpx.AsyncClient, up: dict) -> list[dict]:
    """上流の /v1/models を引く。落ちている上流は空扱い (一覧から消えるだけ)。"""
    url = up["base_url"].rstrip("/") + "/models"
    headers = {}
    if up.get("api_key"):
        headers["authorization"] = f"Bearer {up['api_key']}"
    started = time.monotonic()
    try:
        r = await client.get(url, headers=headers, timeout=_MODELS_TIMEOUT)
        r.raise_for_status()
        return (r.json() or {}).get("data") or []
    except Exception as e:  # noqa: BLE001 — 上流の不調で一覧全体を落とさない
        # 所要時間まで出す: 「落ちている上流はどれで、何秒待たされたか」が
        # 一覧が遅いときの切り分けにそのまま要る。
        log.warning("upstream %s not listable after %.2fs (%s)",
                    url, time.monotonic() - started, type(e).__name__)
        return []


# 生存確認のタイムアウト。一覧取得と並列に走るので、落ちていても一覧が待たされるのは
# 最大でこの秒数。止まっているだけなら connection refused で即座に返る。
_PROBE_TIMEOUT = httpx.Timeout(1.0)


async def _alive(client: httpx.AsyncClient, url: str) -> bool:
    """url が 2xx を返せば生きている扱い。繋がらない・遅い・エラーは全部「落ちている」。"""
    try:
        r = await client.get(url, timeout=_PROBE_TIMEOUT)
        return r.is_success
    except Exception:  # noqa: BLE001 — 落ちているのが平常でありうるので騒がない
        return False


def _pick_context(entry: dict, keys: list[str]) -> int | None:
    for k in keys:
        v = entry.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return None


async def list_models(request: Request) -> Response:
    """全上流を 1 つの一覧に統合する。

    Nucllei のコンテキストゲージ (utils/middleware.py の get_model_context_size) は
    `max_model_len` しか見ないので、どこから来た値でもこのキーに正規化して載せる。
    """
    if not _authorized(request):
        return _unauthorized()
    cfg: Config = request.app.state.cfg
    client: httpx.AsyncClient = request.app.state.client

    # 上流ごとに 1 回だけ引く
    needed = {r["upstream"] for r in cfg.routes.values()} | {
        inc["upstream"] for inc in cfg.includes
    }
    # **並列で引く。** 直列だと所要時間が全上流の合計になり、落ちた上流 1 つで
    # 一覧全体がその分だけ遅れる。gather なら「最も遅い 1 上流」で済む。
    names = sorted(needed)
    # require_alive の生存確認も同じ gather に載せる (URL ごとに 1 回)。
    probes = sorted({
        g["url"] for inc in cfg.includes for g in inc.get("require_alive") or []
    })
    results = await asyncio.gather(
        *(_fetch_models(client, cfg.upstream(n)) for n in names),
        *(_alive(client, u) for u in probes))
    listings = dict(zip(names, results[:len(names)]))
    alive = dict(zip(probes, results[len(names):]))

    out: list[dict] = []
    declared: set[str] = set()

    for name, route in cfg.routes.items():
        by_id = {e.get("id"): e for e in listings.get(route["upstream"], [])}
        entry = by_id.get(route["upstream_model"])
        if entry is None:
            continue  # 上流が停止中 → 一覧に出さない
        declared.add(name)
        ctx = route["context"] or _pick_context(entry, ["max_model_len", "max_input_tokens"])
        item = {**entry, "id": name, "owned_by": route["upstream"]}
        if ctx:
            item["max_model_len"] = ctx
        out.append(item)

    for inc in cfg.includes:
        keys = inc.get("context_from") or ["max_model_len", "max_input_tokens"]
        # exclude は **完全一致**。`ollama/*` のようなワイルドカード定義そのものを
        # 落とすのが用途なので、グロブ解釈すると配下のモデルまで巻き込む。
        # パターンで消したいときは exclude_glob を使う。
        excl = set(inc.get("exclude") or [])
        excl_glob = inc.get("exclude_glob") or []
        # require_alive: url が応答しない間だけ、glob に合うものを一覧から外す。
        # LiteLLM は Ollama が落ちていると静的な組み込みリスト (`ollama/llama2`) に
        # フォールバックするので、上流の一覧だけでは「実在しない」と見分けられない。
        # **一覧だけの話**で、ルーティング (proxy) は変えない。
        excl_glob = excl_glob + [
            g["glob"] for g in inc.get("require_alive") or [] if not alive[g["url"]]
        ]
        for entry in listings.get(inc["upstream"], []):
            mid = entry.get("id")
            if not mid:
                continue
            if mid in excl or any(fnmatch.fnmatch(mid, p) for p in excl_glob):
                continue
            if inc.get("skip_declared") and mid in declared:
                continue
            item = dict(entry)
            ctx = _pick_context(entry, keys)
            if ctx:
                item["max_model_len"] = ctx
            out.append(item)

    return JSONResponse({"object": "list", "data": out})


def _normalize_chunk(chunk: dict, state: dict) -> bool:
    """LiteLLM が組み直したチャンク 1 個を SGLang の形に寄せる。変えたら True。

    Nucllei は SGLang の生フレームを正とする (vendor/nucllei/_memos/model-frames.md)。
    LiteLLM 経由で欠ける/ずれるのは実測 (docs/gateway.md「LiteLLM 経路の差分」) で 2 点:

      1. usage.reasoning_tokens が無く completion_tokens_details の下にだけある
         → 上にも**写す** (元は残す。値を作らない、写すだけ)
      2. tool_calls を流したのに finish_reason が "stop"
         → ChatGPT backend の response.completed が output: [] を返し、LiteLLM の
           responses→chat 変換がそれだけを見て "stop" に倒すため (claude 経由は正しく
           "tool_calls")。ストリーム中に delta.tool_calls を見ていれば "tool_calls" に
           直す。OpenAI 仕様上この組み合わせは "tool_calls" 以外にならないので安全。
    """
    changed = False
    usage = chunk.get("usage")
    if isinstance(usage, dict) and "reasoning_tokens" not in usage:
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
            usage["reasoning_tokens"] = details["reasoning_tokens"]
            changed = True
    for choice in chunk.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict) and delta.get("tool_calls"):
            state["tool_calls"] = True
        if state.get("tool_calls") and choice.get("finish_reason") == "stop":
            choice["finish_reason"] = "tool_calls"
            changed = True
    return changed


async def _normalize_sse(resp: httpx.Response):
    """SSE を**行単位**で中継し、`data: {...}` だけ `_normalize_chunk` を通す。

    バッファは行の途中までに限る (改行が来たら即座に流す) ので、上流が送った
    タイミングはそのまま保たれる。変えた行だけ再シリアライズし (LiteLLM と同じ
    compact / 非 ASCII 生出力)、それ以外の行と `[DONE]`・コメント行はバイトのまま。
    JSON に見えない行は触らない。
    """
    state: dict = {}
    buf = b""
    try:
        async for raw in resp.aiter_raw():
            buf += raw
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line, buf = buf[:nl + 1], buf[nl + 1:]
                yield _normalize_line(line, state)
        if buf:
            yield _normalize_line(buf, state)
    finally:
        await resp.aclose()


def _normalize_line(line: bytes, state: dict) -> bytes:
    body = line.strip()
    if not body.startswith(b"data:"):
        return line
    payload = body[5:].strip()
    if not payload.startswith(b"{"):
        return line
    try:
        chunk = json.loads(payload)
    except ValueError:
        return line
    if not isinstance(chunk, dict) or not _normalize_chunk(chunk, state):
        return line
    out = json.dumps(chunk, ensure_ascii=False, separators=(",", ":")).encode()
    return b"data: " + out + line[len(line.rstrip(b"\r\n")):]


async def proxy(request: Request) -> Response:
    """ボディの `model` で上流を決め、あとは素通しする。"""
    if not _authorized(request):
        return _unauthorized()
    cfg: Config = request.app.state.cfg
    client: httpx.AsyncClient = request.app.state.client

    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {}

    model = payload.get("model")
    route = cfg.routes.get(model)
    if route:
        up = cfg.upstream(route["upstream"])
        if route["upstream_model"] != model:
            payload["model"] = route["upstream_model"]
    elif cfg.includes:
        # 明示ルートに無いものは最初の include 先 (= LiteLLM) に投げる
        up = cfg.upstream(cfg.includes[0]["upstream"])
    else:
        return JSONResponse(
            {"error": {"message": f"model '{model}' not found", "type": "invalid_request_error"}},
            status_code=404,
        )

    streaming = bool(payload.get("stream"))
    # ライブ tok/s の材料。クライアントが要求しなくてもこちらで足す。
    # 応答は無改変なので、対応していない上流でも無視されるだけ。
    if streaming and up.get("force_continuous_usage"):
        so = dict(payload.get("stream_options") or {})
        so.setdefault("include_usage", True)
        so.setdefault("continuous_usage_stats", True)
        payload["stream_options"] = so

    if payload:
        body = json.dumps(payload).encode()

    url = up["base_url"].rstrip("/") + "/" + request.path_params["rest"]
    req = client.build_request(
        request.method, url,
        headers=_upstream_headers(request, up),
        content=body,
        params=request.query_params,
    )

    if not streaming:
        resp = await client.send(req)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=_response_headers(resp),
        )

    resp = await client.send(req, stream=True)

    # LiteLLM 経路だけ行単位で補正する。直上流はこの下の relay (バイト素通し)。
    if up.get("normalize_stream") and resp.is_success:
        return StreamingResponse(
            _normalize_sse(resp),
            status_code=resp.status_code,
            headers=_response_headers(resp),
            media_type=resp.headers.get("content-type"),
        )

    async def relay():
        try:
            # aiter_raw = デコードも再構築もしない。上流のバイトがそのまま出る。
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()

    return StreamingResponse(
        relay(),
        status_code=resp.status_code,
        headers=_response_headers(resp),
        media_type=resp.headers.get("content-type"),
    )


async def health(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


@asynccontextmanager
async def lifespan(app: Starlette):
    app.state.cfg = load_config()
    app.state.client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
    try:
        yield
    finally:
        await app.state.client.aclose()


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/v1/{rest:path}", proxy, methods=["POST", "GET", "PUT", "DELETE", "PATCH"]),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "4000")))
