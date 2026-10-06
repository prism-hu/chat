#!/usr/bin/env python3
"""Small comparison eval for local LLMs behind the prism gateway (stdlib only).

Usage:
  python3 evals/run.py --model deepseek-v4-flash [--only id1,id2]
  python3 evals/run.py --model deepseek-v4-flash --only id1 --merge-into evals/results/<file>.jsonl
  python3 evals/run.py --report evals/results/<file>.jsonl      # regenerate .md only
"""
import argparse
import ast
import base64
import datetime as dt
import hashlib
import json
import mimetypes
import operator
import os
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_DIR = EVAL_DIR.parent
RESULTS_DIR = EVAL_DIR / "results"
DEFAULT_BASE = "http://localhost:4000/v1"
MAX_TOKENS = 8000
TIMEOUT_S = 600
MAX_TURNS = 10


# --------------------------------------------------------------------------
# Mock tools (deterministic)
# --------------------------------------------------------------------------

DOCS = [
    {
        "id": "DOC-101",
        "title": "ヨード造影剤使用に関する院内基準（2026年4月改訂）",
        "date": "2026-04-01",
        "body": (
            "造影CT等でヨード造影剤を使用する際は、検査前3か月以内の腎機能（eGFR）で判断する。"
            "eGFR 45以上: 通常どおり実施可。"
            "eGFR 30以上45未満: 実施可。ただし検査前後各6時間、生理食塩液 1 mL/kg/h の補液を推奨。"
            "eGFR 30未満: 原則として造影検査を避ける。やむを得ず実施する場合は放射線科医と主治医が協議し、"
            "必要性とリスク説明の内容を診療録に記載したうえで補液を行う。"
            "メトホルミン内服中の患者は、検査当日は休薬し、検査後48時間は再開しない（腎機能を再評価してから再開）。"
        ),
    },
    {
        "id": "DOC-102",
        "title": "【全職員必須】2026年度 第2回 院内感染対策研修会のお知らせ",
        "date": "2026-09-25",
        "body": (
            "日時: 2026年10月20日（火）17:30〜19:00。場所: 臨床講義棟 大講堂。"
            "対象: 全職員（受講必須）。内容: 薬剤耐性菌の動向と手指衛生の再確認。"
            "当日参加できない方は、11月6日までに e-learning で受講してください。"
        ),
    },
    {
        "id": "DOC-087",
        "title": "2025年度 第2回 院内感染対策研修会のお知らせ",
        "date": "2025-09-26",
        "body": (
            "日時: 2025年10月21日（火）17:30〜19:00。場所: 臨床研究棟 1階 会議室。"
            "対象: 全職員（受講必須）。内容: 標準予防策と感染経路別予防策。"
        ),
    },
    {
        "id": "DOC-103",
        "title": "電子カルテシステム 定期メンテナンスによる停止のお知らせ",
        "date": "2026-09-30",
        "body": (
            "2026年10月11日（日）0:00〜6:00 の間、電子カルテシステムを停止します。"
            "停止中は紙運用（ダウンタイム帳票）に切り替えてください。"
        ),
    },
    {
        "id": "DOC-104",
        "title": "職員インフルエンザワクチン接種日程",
        "date": "2026-09-28",
        "body": (
            "接種日: 2026年10月14日（水）〜16日（金）13:00〜16:00。場所: 職員健康管理室。"
            "職員証を持参してください。"
        ),
    },
    {
        "id": "DOC-105",
        "title": "MRI検査室への金属類持ち込み禁止の徹底について",
        "date": "2026-08-20",
        "body": (
            "MRI検査室には酸素ボンベ、点滴スタンド、ストレッチャー等の磁性体を持ち込まないこと。"
            "MRI対応の機器のみ使用可。"
        ),
    },
]

WEATHER_FIXED = {
    ("札幌", "2026-10-07"): {"weather": "曇のち雨", "high_c": 14, "low_c": 7, "precip_prob_pct": 70},
    ("東京", "2026-10-07"): {"weather": "晴れ", "high_c": 24, "low_c": 17, "precip_prob_pct": 10},
    ("札幌", "2026-10-20"): {"weather": "晴れ時々曇", "high_c": 13, "low_c": 4, "precip_prob_pct": 20},
}
CITY_ALIASES = {
    "札幌": ["札幌", "札幌市", "sapporo"],
    "東京": ["東京", "東京都", "tokyo"],
    "大阪": ["大阪", "大阪市", "osaka"],
    "旭川": ["旭川", "旭川市", "asahikawa"],
    "函館": ["函館", "函館市", "hakodate"],
}

SQL_SCHEMA = """
CREATE TABLE patients (patient_id TEXT PRIMARY KEY, name TEXT, birth_date TEXT, sex TEXT, medications TEXT);
CREATE TABLE appointments (appt_id INTEGER PRIMARY KEY, patient_id TEXT, patient_name TEXT,
  date TEXT, time TEXT, department TEXT, exam TEXT);
CREATE TABLE labs (lab_id INTEGER PRIMARY KEY, patient_id TEXT, date TEXT, test TEXT, value REAL, unit TEXT);
"""
SQL_ROWS = {
    "patients": [
        ("P-1024", "佐藤花子", "1951-03-14", "F", "メトホルミン 500mg 1日2回; アムロジピン 5mg 1日1回"),
        ("P-4096", "佐藤健", "1968-11-02", "M", "なし"),
        ("P-2048", "田中一郎", "1949-07-21", "M", "エナラプリル 5mg 1日1回; スピロノラクトン 25mg 1日1回"),
        ("P-3072", "田中美咲", "1985-01-30", "F", "なし"),
        ("P-2049", "田中一", "1972-05-05", "M", "ロスバスタチン 2.5mg 1日1回"),
        ("P-5120", "鈴木一郎", "1960-12-12", "M", "なし"),
    ],
    "appointments": [
        (1, "P-1024", "佐藤花子", "2026-10-08", "10:30", "放射線科", "造影CT（腹部）"),
        (2, "P-4096", "佐藤健", "2026-10-08", "11:00", "放射線科", "単純CT（胸部）"),
        (3, "P-2048", "田中一郎", "2026-10-07", "09:00", "循環器内科", "再診"),
        (4, "P-3072", "田中美咲", "2026-10-09", "14:00", "腎臓内科", "再診"),
        (5, "P-2049", "田中一", "2026-10-21", "10:00", "内分泌内科", "再診"),
        (6, "P-5120", "鈴木一郎", "2026-10-06", "15:00", "消化器内科", "上部消化管内視鏡"),
    ],
    "labs": [
        (1, "P-1024", "2026-09-10", "Cr", 1.25, "mg/dL"),
        (2, "P-1024", "2026-09-10", "eGFR", 33, "mL/min/1.73m2"),
        (3, "P-1024", "2026-09-10", "K", 4.4, "mmol/L"),
        (4, "P-1024", "2026-10-01", "Cr", 1.62, "mg/dL"),
        (5, "P-1024", "2026-10-01", "eGFR", 24, "mL/min/1.73m2"),
        (6, "P-1024", "2026-10-01", "K", 4.6, "mmol/L"),
        (7, "P-2048", "2026-09-15", "K", 5.2, "mmol/L"),
        (8, "P-2048", "2026-10-02", "K", 5.9, "mmol/L"),
        (9, "P-2048", "2026-10-02", "Cr", 1.10, "mg/dL"),
        (10, "P-2048", "2026-10-02", "eGFR", 52, "mL/min/1.73m2"),
        (11, "P-3072", "2026-10-03", "K", 4.1, "mmol/L"),
        (12, "P-3072", "2026-10-03", "Cr", 0.70, "mg/dL"),
        (13, "P-3072", "2026-10-03", "eGFR", 78, "mL/min/1.73m2"),
        (14, "P-2049", "2026-08-30", "K", 4.3, "mmol/L"),
        (15, "P-4096", "2026-09-20", "eGFR", 85, "mL/min/1.73m2"),
    ],
}


def new_db():
    db = sqlite3.connect(":memory:")
    db.executescript(SQL_SCHEMA)
    for table, rows in SQL_ROWS.items():
        ph = ",".join("?" * len(rows[0]))
        db.executemany(f"INSERT INTO {table} VALUES ({ph})", rows)
    db.commit()
    return db


def _bigrams(s):
    s = re.sub(r"\s+", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


_CALC_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_CALC_FUNCS = {"abs": abs, "round": round, "min": min, "max": max,
               "sqrt": lambda x: x ** 0.5}


def _calc(node):
    if isinstance(node, ast.Expression):
        return _calc(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _CALC_OPS:
        left, right = _calc(node.left), _calc(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _CALC_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _CALC_OPS:
        return _CALC_OPS[type(node.op)](_calc(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _CALC_FUNCS:
        return _CALC_FUNCS[node.func.id](*[_calc(a) for a in node.args])
    raise ValueError(f"unsupported expression: {ast.dump(node)[:80]}")


class ToolBox:
    """Per-question tool state (fresh sqlite + calendar)."""

    def __init__(self):
        self.db = new_db()
        self.events = []

    def search_docs(self, query, top_k=3):
        q = _bigrams(query)
        scored = []
        for d in DOCS:
            text = _bigrams(d["title"] + d["body"])
            score = len(q & text) / max(1, len(q))
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        hits = [dict(d, score=round(s, 2)) for s, d in scored[: int(top_k or 3)]]
        return {"query": query, "results": hits}

    def get_patient_labs(self, patient_id, tests=None):
        cur = self.db.execute("SELECT patient_id, name, birth_date, sex, medications FROM patients WHERE patient_id=?",
                              (patient_id,))
        p = cur.fetchone()
        if not p:
            return {"error": f"patient_id {patient_id!r} not found"}
        rows = self.db.execute("SELECT date, test, value, unit FROM labs WHERE patient_id=? ORDER BY date DESC, test",
                               (patient_id,)).fetchall()
        if tests:
            want = {t.lower() for t in (tests if isinstance(tests, list) else [tests])}
            rows = [r for r in rows if r[1].lower() in want]
        return {
            "patient": dict(zip(["patient_id", "name", "birth_date", "sex", "medications"], p)),
            "labs": [dict(zip(["date", "test", "value", "unit"], r)) for r in rows],
        }

    def calculator(self, expression):
        try:
            return {"expression": expression, "result": _calc(ast.parse(str(expression), mode="eval"))}
        except Exception as e:  # noqa: BLE001
            return {"expression": expression, "error": str(e)}

    def get_weather(self, city, date):
        key = None
        c = str(city).strip().lower()
        for canon, aliases in CITY_ALIASES.items():
            if c in [a.lower() for a in aliases]:
                key = canon
        if key is None:
            return {"error": f"unknown city: {city}"}
        try:
            d = dt.date.fromisoformat(str(date))
        except ValueError:
            return {"error": "date must be YYYY-MM-DD"}
        if not (dt.date(2026, 10, 6) <= d <= dt.date(2026, 10, 25)):
            return {"error": "forecast available only for 2026-10-06..2026-10-25"}
        if (key, d.isoformat()) in WEATHER_FIXED:
            w = WEATHER_FIXED[(key, d.isoformat())]
        else:
            h = int(hashlib.md5(f"{key}{d}".encode()).hexdigest(), 16)
            base = {"札幌": 13, "旭川": 11, "函館": 14, "東京": 22, "大阪": 23}[key]
            hi = base + h % 5 - 2
            w = {"weather": ["晴れ", "曇り", "雨", "晴れ時々曇"][h % 4], "high_c": hi,
                 "low_c": hi - 7 - (h >> 4) % 3, "precip_prob_pct": [10, 30, 70, 20][h % 4]}
        return {"city": key, "date": d.isoformat(), **w}

    def calendar_create(self, title, start, end, location=None):
        try:
            s, e = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)
        except (TypeError, ValueError):
            return {"error": "start/end must be ISO 8601 (e.g. 2026-10-20T17:30:00)"}
        if e <= s:
            return {"error": "end must be after start"}
        ev = {"event_id": f"EVT-{len(self.events) + 1:04d}", "title": title, "start": start, "end": end,
              "location": location, "status": "created"}
        self.events.append(ev)
        return ev

    def run_sql(self, query):
        try:
            cur = self.db.execute(query)
            if cur.description:
                cols = [c[0] for c in cur.description]
                rows = cur.fetchmany(50)
                return {"columns": cols, "rows": rows, "row_count": len(rows)}
            self.db.commit()
            return {"status": "ok", "rows_affected": cur.rowcount}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def call(self, name, args):
        fn = getattr(self, name, None)
        if name not in TOOL_SCHEMAS or fn is None:
            return {"error": f"unknown tool: {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


TOOL_SCHEMAS = {
    "search_docs": _fn("search_docs", "院内のお知らせ・規程文書を全文検索し、関連度の高い文書を返す。",
                       {"query": {"type": "string", "description": "検索語（日本語可）"},
                        "top_k": {"type": "integer", "description": "返す件数（既定3）"}}, ["query"]),
    "get_patient_labs": _fn("get_patient_labs", "患者IDを指定して、患者基本情報（内服薬含む）と検査値の履歴（新しい順）を取得する。",
                            {"patient_id": {"type": "string", "description": "患者ID（例: P-0001）"},
                             "tests": {"type": "array", "items": {"type": "string"},
                                       "description": "絞り込む検査名（例: [\"K\",\"eGFR\"]）。省略時は全項目"}},
                            ["patient_id"]),
    "calculator": _fn("calculator", "四則演算・べき乗などの数式を計算する。",
                      {"expression": {"type": "string", "description": "数式（例: (24-14)*2）"}}, ["expression"]),
    "get_weather": _fn("get_weather", "都市と日付を指定して天気予報（天気・最高/最低気温・降水確率）を取得する。",
                       {"city": {"type": "string", "description": "都市名（例: 札幌）"},
                        "date": {"type": "string", "description": "YYYY-MM-DD"}}, ["city", "date"]),
    "calendar_create": _fn("calendar_create", "ユーザーのカレンダーに予定を作成する。",
                           {"title": {"type": "string"},
                            "start": {"type": "string", "description": "開始日時 ISO 8601（JST, 例 2026-10-20T17:30:00）"},
                            "end": {"type": "string", "description": "終了日時 ISO 8601（JST）"},
                            "location": {"type": "string"}}, ["title", "start", "end"]),
    "run_sql": _fn("run_sql", "院内データベース（SQLite）に SQL を実行する。テーブル: "
                   "patients(patient_id, name, birth_date, sex, medications), "
                   "appointments(appt_id, patient_id, patient_name, date, time, department, exam), "
                   "labs(lab_id, patient_id, date, test, value, unit)。日付は 'YYYY-MM-DD' 文字列。",
                   {"query": {"type": "string", "description": "SQL 文"}}, ["query"]),
}


# --------------------------------------------------------------------------
# HTTP / streaming
# --------------------------------------------------------------------------

def load_api_key():
    key = os.environ.get("PRISM_GW_API_KEY")
    if key:
        return key
    env = REPO_DIR / ".env"
    for line in env.read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?PRISM_GW_API_KEY\s*=\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip("'\"")
    sys.exit("PRISM_GW_API_KEY not found in env or .env")


def stream_chat(base, key, payload):
    """POST a streaming chat completion. Returns dict with content/reasoning/tool_calls/usage/timings."""
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
    )
    t0 = time.monotonic()
    t_first = t_first_r = t_last_r = t_first_a = None
    content, reasoning = [], []
    tool_calls = {}
    finish_reason = None
    usage = None
    cum_ctoks = None            # cumulative completion_tokens seen in per-chunk usage (vLLM sends it)
    r_tok_at_last_r = None      # cumulative completion tokens at the last reasoning chunk
    n_r_chunks = n_a_chunks = 0
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        for raw in resp:
            now = time.monotonic()
            if now - t0 > TIMEOUT_S:
                raise TimeoutError(f"request exceeded {TIMEOUT_S}s")
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise RuntimeError(f"stream error: {chunk['error']}")
            if chunk.get("usage"):
                usage = chunk["usage"]
                cum_ctoks = usage.get("completion_tokens")
            for ch in chunk.get("choices") or []:
                delta = ch.get("delta") or {}
                r = delta.get("reasoning") or delta.get("reasoning_content")
                if r:
                    reasoning.append(r)
                    n_r_chunks += 1
                    t_first_r = t_first_r or now
                    t_last_r = now
                    r_tok_at_last_r = cum_ctoks
                got_a = False
                if delta.get("content"):
                    content.append(delta["content"])
                    got_a = True
                for tc in delta.get("tool_calls") or []:
                    got_a = True
                    slot = tool_calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if got_a:
                    n_a_chunks += 1
                    t_first_a = t_first_a or now
                if (r or got_a) and t_first is None:
                    t_first = now
                if ch.get("finish_reason"):
                    finish_reason = ch["finish_reason"]
    t_end = time.monotonic()
    usage = usage or {}
    ctoks = usage.get("completion_tokens") or 0
    ptoks = usage.get("prompt_tokens")
    # reasoning/answer token split: exact if usage has reasoning_tokens; otherwise estimated from the
    # cumulative per-chunk usage at the last reasoning chunk, or from chunk counts.
    rt_exact = ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
    if rt_exact is not None:
        r_toks, split_src = rt_exact, "usage"
    elif not reasoning:
        r_toks, split_src = 0, "usage"
    elif r_tok_at_last_r is not None:
        r_toks, split_src = min(r_tok_at_last_r, ctoks), "est_chunk_usage"
    else:
        tot = n_r_chunks + n_a_chunks
        r_toks, split_src = (round(ctoks * n_r_chunks / tot) if tot else 0), "est_chunk_count"
    a_toks = max(0, ctoks - r_toks)

    def rate(n, s):
        return round(n / s, 2) if n and s and s > 0 else None

    rel = lambda t: round(t - t0, 3) if t else None  # noqa: E731
    think_s = (t_last_r - t_first_r) if t_first_r else None
    decode_s = (t_end - t_first) if t_first else None
    answer_s = (t_end - t_first_a) if t_first_a else None
    return {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "tool_calls": [tool_calls[i] for i in sorted(tool_calls)],
        "finish_reason": finish_reason,
        "prompt_tokens": ptoks,
        "completion_tokens": ctoks,
        "reasoning_tokens": r_toks,
        "answer_tokens": a_toks,
        "token_split_source": split_src,
        "ttft": rel(t_first),
        "ttfa": rel(t_first_a),
        "think_s": round(think_s, 3) if think_s is not None else None,
        "decode_s": round(decode_s, 3) if decode_s else None,
        "decode_tps": rate(ctoks, decode_s),
        "reasoning_tps": rate(r_toks, think_s),
        "answer_tps": rate(a_toks, answer_s),
        "prefill_tps": rate(ptoks, (t_first - t0) if t_first else None),
        "wall": round(t_end - t0, 3),
    }


# --------------------------------------------------------------------------
# Speculative-decoding metrics (vLLM /metrics, Prometheus text). Best effort: never fails a question.
# --------------------------------------------------------------------------

SPEC_METRICS = {
    "vllm:spec_decode_num_drafts_total": "drafts",
    "vllm:spec_decode_num_draft_tokens_total": "draft_tokens",
    "vllm:spec_decode_num_accepted_tokens_total": "accepted",
}
SPEC_POS_METRIC = "vllm:spec_decode_num_accepted_tokens_per_pos_total"
# TensorFold (GLM) fallback: no vllm:spec_decode_*; it exposes MTP drafted/accepted counters and a
# decode-round counter. rounds_total is used as the number of verify steps ("drafts"), so
# mean accepted length = 1 + accepted / rounds. Checked on a fresh server: 2 requests, 70 completion
# tokens = 21 rounds + 47 accepted + 2 (first token of each request comes from prefill). Per-position: n/a.
TF_METRICS = {
    "tensorfold:mtp_drafted_total": "draft_tokens",
    "tensorfold:mtp_accepted_total": "accepted",
    "tensorfold_health:rounds_total": "drafts",
}
# SGLang fallback (e.g. qwen3.8-27b with DFLASH): counters summed over label sets
#   gen = sglang:generation_tokens_total, verify = sglang:spec_verify_calls_total,
#   req = sglang:generation_tokens_histogram_count (finished requests).
# The first token of each request comes from prefill, so tokens per verify step
#   len = (Δgen − Δreq) / Δverify   (stored as drafts=Δverify, accepted=Δgen−Δreq−Δverify, so len = 1 + accepted/drafts)
# Acceptance rate is left null: the per-verify drafted-token count (8 vs 7 incl. root) is not exposed, so
# (len−1)/8 is only an approximation. The gauge sglang:spec_accept_length is a moving window and is not used.
SG_METRICS = {
    "sglang:generation_tokens_total": "gen",
    "sglang:spec_verify_calls_total": "verify",
    "sglang:generation_tokens_histogram_count": "req",
}
METRICS_URL = None


def scrape_spec():
    """Return {"drafts", "draft_tokens", "accepted", "pos": {N: v}} summed over label sets, or None."""
    if not METRICS_URL:
        return None
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=5) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    out = {"drafts": 0.0, "draft_tokens": 0.0, "accepted": 0.0, "pos": {}, "source": "vllm"}
    tf = {"drafts": 0.0, "draft_tokens": 0.0, "accepted": 0.0, "pos": {}, "source": "tensorfold"}
    sg = {"gen": 0.0, "verify": 0.0, "req": 0.0}
    seen = tf_seen = sg_seen = False
    for line in text.splitlines():
        if line.startswith("sglang:"):
            m = re.match(r"^([^{\s]+)(\{[^}]*\})?\s+(\S+)", line)
            if m and m.group(1) in SG_METRICS:
                try:
                    sg[SG_METRICS[m.group(1)]] += float(m.group(3))
                    sg_seen = sg_seen or m.group(1) == "sglang:spec_verify_calls_total"
                except ValueError:
                    pass
            continue
        if line.startswith("tensorfold"):
            m = re.match(r"^([^{\s]+)(\{[^}]*\})?\s+(\S+)", line)
            if m and m.group(1) in TF_METRICS:
                try:
                    tf[TF_METRICS[m.group(1)]] += float(m.group(3))
                    tf_seen = tf_seen or m.group(1) != "tensorfold_health:rounds_total"
                except ValueError:
                    pass
            continue
        if not line.startswith("vllm:spec_decode"):
            continue
        m = re.match(r"^([^{\s]+)(\{[^}]*\})?\s+(\S+)", line)
        if not m:
            continue
        name, labels, val = m.group(1), m.group(2) or "", m.group(3)
        try:
            v = float(val)
        except ValueError:
            continue
        if name in SPEC_METRICS:
            out[SPEC_METRICS[name]] += v
            seen = True
        elif name == SPEC_POS_METRIC:
            pm = re.search(r'position="(\d+)"', labels)
            if pm:
                k = int(pm.group(1))
                out["pos"][k] = out["pos"].get(k, 0.0) + v
    if seen:
        return out
    if tf_seen:
        return tf
    if sg_seen:
        # cumulative pseudo-counters; deltas give drafts=Δverify, accepted=Δgen−Δreq−Δverify
        return {"drafts": sg["verify"], "draft_tokens": 0.0, "accepted": sg["gen"] - sg["req"] - sg["verify"],
                "pos": {}, "source": "sglang"}
    return None


def spec_delta(before, after):
    if not before or not after:
        return None
    d = {k: after[k] - before[k] for k in ("drafts", "draft_tokens", "accepted")}
    d["pos"] = {k: after["pos"].get(k, 0.0) - before["pos"].get(k, 0.0) for k in sorted(after["pos"])}
    d["source"] = after.get("source", "vllm")
    return d


def spec_sum(deltas):
    deltas = [d for d in deltas if d]
    if not deltas:
        return None
    tot = {"drafts": 0.0, "draft_tokens": 0.0, "accepted": 0.0, "pos": {}, "source": deltas[0].get("source", "vllm")}
    for d in deltas:
        for k in ("drafts", "draft_tokens", "accepted"):
            tot[k] += d[k]
        for k, v in d["pos"].items():
            tot["pos"][k] = tot["pos"].get(k, 0.0) + v
    return tot


def spec_stats(d):
    """Derived: acceptance rate, mean accepted length per draft (incl. bonus token), per-position rate."""
    if not d or not (d["drafts"] or d["draft_tokens"]):
        return None
    return {
        "source": d.get("source", "vllm"),
        "drafts": int(d["drafts"]), "draft_tokens": int(d["draft_tokens"]), "accepted": int(d["accepted"]),
        "accept_rate": round(d["accepted"] / d["draft_tokens"], 4) if d["draft_tokens"] else None,
        "mean_accept_len": round(1 + d["accepted"] / d["drafts"], 3) if d["drafts"] else None,
        "per_pos": {str(k): round(v / d["drafts"], 4) for k, v in sorted(d["pos"].items(), key=lambda x: int(x[0]))}
        if d["drafts"] else {},
    }


def build_user_message(q):
    if not q.get("image"):
        return {"role": "user", "content": q["prompt"]}
    path = REPO_DIR / q["image"]
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return {"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        {"type": "text", "text": q["prompt"]},
    ]}


def run_question(base, key, model, q, extra_body=None, tag=None):
    messages = []
    if q.get("system"):
        messages.append({"role": "system", "content": q["system"]})
    messages.append(build_user_message(q))
    tools = [TOOL_SCHEMAS[t] for t in q.get("tools", [])]
    box = ToolBox()
    rec = {"id": q["id"], "category": q["category"], "model": model,
           "label": f"{model}[{tag}]" if tag else model, "tag": tag, "extra_body": extra_body or None,
           "prompt": q["prompt"], "system": q.get("system"), "image": q.get("image"),
           "check": q.get("check", ""), "turns": [], "error": None}
    t0 = time.monotonic()
    for turn in range(1, MAX_TURNS + 1):
        payload = {"model": model, "messages": messages, "stream": True,
                   "stream_options": {"include_usage": True}, "max_tokens": MAX_TOKENS}
        if tools:
            payload["tools"] = tools
        if extra_body:
            payload.update(extra_body)
        m_before = scrape_spec()
        try:
            r = stream_chat(base, key, payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:1000]
            rec["error"] = f"HTTP {e.code}: {body}"
            break
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {e}"
            break
        delta = spec_delta(m_before, scrape_spec())
        t = {"turn": turn, **{k: v for k, v in r.items() if k != "tool_calls"}, "tool_calls": [],
             "spec_raw": delta, "spec": spec_stats(delta)}
        rec["turns"].append(t)
        if not r["tool_calls"]:
            break
        asst = {"role": "assistant", "content": r["content"] or None, "tool_calls": []}
        if r["reasoning"]:
            asst["reasoning_content"] = r["reasoning"]
        results = []
        for i, tc in enumerate(r["tool_calls"]):
            tc_id = tc["id"] or f"call_{turn}_{i}"
            try:
                args = json.loads(tc["arguments"] or "{}")
                if not isinstance(args, dict):
                    raise ValueError("arguments is not an object")
                result = box.call(tc["name"], args)
            except ValueError as e:
                args = None
                result = {"error": f"invalid JSON arguments: {e}"}
            t["tool_calls"].append({"name": tc["name"], "arguments": tc["arguments"], "args": args,
                                    "result": result})
            asst["tool_calls"].append({"id": tc_id, "type": "function",
                                       "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"}})
            results.append({"role": "tool", "tool_call_id": tc_id,
                            "content": json.dumps(result, ensure_ascii=False)})
        messages.append(asst)
        messages.extend(results)
    else:
        rec["error"] = (rec["error"] or "") + f"max_turns ({MAX_TURNS}) reached"
    rec["wall"] = round(time.monotonic() - t0, 2)
    finalize(rec)
    rec["calendar_events"] = box.events
    return rec


def finalize(rec):
    turns = rec["turns"]
    last = turns[-1] if turns else {}
    rec["answer"] = last.get("content", "")
    rec["reasoning"] = "\n\n".join(
        (f"[turn {t['turn']}]\n" if len(turns) > 1 else "") + t["reasoning"] for t in turns if t["reasoning"])
    rec["reasoning_chars"] = sum(len(t["reasoning"]) for t in turns)
    rec["finish_reason"] = last.get("finish_reason")
    rec["hit_length"] = any(t["finish_reason"] == "length" for t in turns)
    rec["n_turns"] = len(turns)
    rec["n_tool_calls"] = sum(len(t["tool_calls"]) for t in turns)
    rec["prompt_tokens"] = sum(t["prompt_tokens"] or 0 for t in turns)
    rec["completion_tokens"] = sum(t["completion_tokens"] or 0 for t in turns)
    rec["reasoning_tokens"] = sum(t.get("reasoning_tokens") or 0 for t in turns)
    rec["answer_tokens"] = sum(t.get("answer_tokens") or 0 for t in turns)
    srcs = {t.get("token_split_source") for t in turns if t.get("reasoning")}
    rec["token_split_source"] = "usage" if srcs <= {"usage"} else "estimate"
    rec["ttft"] = turns[0]["ttft"] if turns else None
    rec["ttfa"] = turns[0].get("ttfa") if turns else None            # first turn: first content/tool-call token
    rec["final_ttfa"] = last.get("ttfa")                             # final turn, relative to its request start
    rec["think_s"] = round(sum(t.get("think_s") or 0 for t in turns), 2)
    rec["turns_s"] = round(sum(t["wall"] or 0 for t in turns), 2)    # time spent inside model requests
    rec["outside_s"] = round(rec["wall"] - rec["turns_s"], 2) if rec.get("wall") is not None else None

    def rate(n, s):
        return round(n / s, 2) if n and s else None

    rec["decode_tps"] = rate(rec["completion_tokens"], sum(t["decode_s"] or 0 for t in turns))
    rec["reasoning_tps"] = rate(rec["reasoning_tokens"], rec["think_s"])
    ans_s = sum((t["wall"] - t["ttfa"]) for t in turns if t.get("ttfa") is not None)
    rec["answer_tps"] = rate(rec["answer_tokens"], ans_s)
    sraw = spec_sum([t.get("spec_raw") for t in turns])
    rec["spec"] = spec_stats(sraw)
    rec["prefill_tps"] = rate(rec["prompt_tokens"], sum(t["ttft"] or 0 for t in turns))


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def short(s, n=300):
    s = s or ""
    return s if len(s) <= n else s[:n] + f"…（全{len(s)}字）"


def fmt(v, nd=1):
    if v is None:
        return "-"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def fmt_pct(v):
    return "-" if v is None else f"{v * 100:.1f}%"


def spec_short(sp):
    return (fmt_pct(sp.get("accept_rate")), fmt(sp.get("mean_accept_len"), 2)) if sp else ("-", "-")


def per_pos_str(sp):
    if not sp or not sp.get("per_pos"):
        return "-"
    return " ".join(f"p{k}={v * 100:.0f}%" for k, v in sp["per_pos"].items())


def pool_spec(recs):
    """Pool raw counts over records (token-weighted), return spec_stats-like dict or None."""
    tot = {"drafts": 0, "draft_tokens": 0, "accepted": 0, "pos": {}, "source": "vllm"}
    n = 0
    for r in recs:
        sp = r.get("spec")
        if not sp:
            continue
        n += 1
        tot["source"] = sp.get("source", "vllm")
        for k in ("drafts", "draft_tokens", "accepted"):
            tot[k] += sp[k]
        for k, v in sp["per_pos"].items():
            tot["pos"][k] = tot["pos"].get(k, 0) + v * sp["drafts"]
    return spec_stats(tot) if n else None


SPEC_NOTE = ("*Spec-decode acceptance comes from deltas of the server's global vLLM /metrics counters around each "
             "request; numbers are polluted if anyone else uses the model concurrently. accept = accepted / draft "
             "tokens; len = 1 + accepted / drafts (mean tokens emitted per verify step); p<N> = accepted at draft "
             "position N / drafts. For TensorFold (GLM) servers: accept = tensorfold:mtp_accepted_total / "
             "tensorfold:mtp_drafted_total, len = 1 + accepted / tensorfold_health:rounds_total (decode rounds as verify "
             "steps), per-position n/a. For SGLang servers: len = (Δgeneration_tokens − Δfinished_requests) / "
             "Δspec_verify_calls (first token of each request comes from prefill); accept rate n/a (drafted tokens per "
             "verify not exposed; ≈ (len−1)/8 for 8 draft tokens).*")


def quote(s):
    return "\n".join("> " + line for line in (s or "").splitlines()) or "> (empty)"


def tool_trace_md(rec):
    lines = []
    for t in rec["turns"]:
        for tc in t["tool_calls"]:
            res = json.dumps(tc["result"], ensure_ascii=False)
            lines.append(f"- T{t['turn']} `{tc['name']}({short(tc['arguments'], 200)})` → {short(res, 400)}")
    return "\n".join(lines)


def stats_line(rec):
    est = " (est.)" if rec.get("token_split_source") == "estimate" else ""
    return (f"TTFT {fmt(rec['ttft'], 2)}s · TTFA {fmt(rec.get('ttfa'), 2)}s · think {fmt(rec.get('think_s'))}s · "
            f"decode {fmt(rec['decode_tps'])} tok/s (reasoning {fmt(rec.get('reasoning_tps'))} / answer "
            f"{fmt(rec.get('answer_tps'))}{est}) · prefill {fmt(rec.get('prefill_tps'), 0)} tok/s · "
            f"completion {rec['completion_tokens']} tok (reasoning {rec.get('reasoning_tokens')} / answer "
            f"{rec.get('answer_tokens')}{est}) · prompt {rec['prompt_tokens']} tok · wall {fmt(rec['wall'])}s "
            f"(in model {fmt(rec.get('turns_s'))}s) · turns {rec['n_turns']} · tool calls {rec['n_tool_calls']} · "
            f"reasoning {rec['reasoning_chars']}字 · spec accept {spec_short(rec.get('spec'))[0]}, "
            f"len {spec_short(rec.get('spec'))[1]} · finish `{rec['finish_reason']}`"
            + (" · **LENGTH CUT**" if rec["hit_length"] else "")
            + (f" · **ERROR** {short(rec['error'], 200)}" if rec.get("error") else ""))


def turn_lines(rec):
    if rec["n_turns"] <= 1:
        return ""
    out = ["| turn | TTFT s | TTFA s | think s | decode tok/s | prefill tok/s | prompt | completion (r/a) | accept | len | wall s | finish | calls |",
           "|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|---:|"]
    for t in rec["turns"]:
        out.append(f"| {t['turn']} | {fmt(t['ttft'], 2)} | {fmt(t.get('ttfa'), 2)} | {fmt(t.get('think_s'))} | "
                   f"{fmt(t['decode_tps'])} | {fmt(t.get('prefill_tps'), 0)} | {t['prompt_tokens']} | "
                   f"{t['completion_tokens']} ({t.get('reasoning_tokens')}/{t.get('answer_tokens')}) | "
                   f"{spec_short(t.get('spec'))[0]} | {spec_short(t.get('spec'))[1]} | {fmt(t['wall'])} | {t['finish_reason']} | {len(t['tool_calls'])} |")
    return "\n".join(out)


SUMMARY_HEADER = ("| id | category | TTFT s | TTFA s | think s | decode tok/s | reas tok/s* | ans tok/s* | prefill tok/s | "
                  "completion (reas/ans*) | accept | len | wall s | finish | tool calls | turns |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|---:|---:|")


def summary_row(r, label=None):
    fin = r["finish_reason"] or "-"
    if r["hit_length"]:
        fin = "**length**"
    if r.get("error"):
        fin += " **ERR**"
    return (f"| {label or r['id']} | {r['category']} | {fmt(r['ttft'], 2)} | {fmt(r.get('ttfa'), 2)} | "
            f"{fmt(r.get('think_s'))} | {fmt(r['decode_tps'])} | {fmt(r.get('reasoning_tps'))} | "
            f"{fmt(r.get('answer_tps'))} | {fmt(r.get('prefill_tps'), 0)} | {r['completion_tokens']} "
            f"({r.get('reasoning_tokens')}/{r.get('answer_tokens')}) | {spec_short(r.get('spec'))[0]} | "
            f"{spec_short(r.get('spec'))[1]} | {fmt(r['wall'])} | {fin} | "
            f"{r['n_tool_calls']} | {r['n_turns']} |")


def summary_table(recs):
    return "\n".join(list(SUMMARY_HEADER) + [summary_row(r) for r in recs]) + (
        "\n\n*TTFT = first token of any kind (reasoning included); TTFA = first answer token (content or tool call) "
        "of the first turn; think = first→last reasoning token. reas/ans tokens and their tok/s are estimates from "
        "per-chunk cumulative usage unless the server reports completion_tokens_details.reasoning_tokens.*\n\n" + SPEC_NOTE)


def _med(recs, k, nd=2):
    v = [r.get(k) for r in recs if r.get(k) is not None]
    return round(statistics.median(v), nd) if v else None


AGG_KEYS = ["ttft", "ttfa", "think_s", "decode_tps", "reasoning_tps", "answer_tps", "prefill_tps",
            "completion_tokens", "reasoning_tokens", "answer_tokens", "wall"]


def aggregate(recs):
    tps = [r["decode_tps"] for r in recs if r["decode_tps"]]
    a = {"n": len(recs), **{k + "_median": _med(recs, k) for k in AGG_KEYS},
         "tps_min": round(min(tps), 1) if tps else None, "tps_max": round(max(tps), 1) if tps else None,
         "completion_total": sum(r["completion_tokens"] for r in recs),
         "wall_total": round(sum(r["wall"] for r in recs), 1),
         "accept_rate_median": _med([r.get("spec") or {} for r in recs], "accept_rate", 4),
         "mean_accept_len_median": _med([r.get("spec") or {} for r in recs], "mean_accept_len", 3),
         "spec_pooled": pool_spec(recs),
         "errors": [r["id"] for r in recs if r.get("error")],
         "length_cut": [r["id"] for r in recs if r["hit_length"]]}
    return a


def agg_line(a):
    return (f"{a['n']} questions · median: TTFT {fmt(a['ttft_median'], 2)}s, TTFA {fmt(a['ttfa_median'], 2)}s, "
            f"think {fmt(a['think_s_median'])}s, decode {fmt(a['decode_tps_median'])} tok/s "
            f"(min {fmt(a['tps_min'])}, max {fmt(a['tps_max'])}; reasoning {fmt(a['reasoning_tps_median'])} / "
            f"answer {fmt(a['answer_tps_median'])}), prefill {fmt(a['prefill_tps_median'], 0)} tok/s, "
            f"completion {fmt(a['completion_tokens_median'], 0)} tok, wall {fmt(a['wall_median'])}s, "
            f"spec accept {fmt_pct(a['accept_rate_median'])} / len {fmt(a['mean_accept_len_median'], 2)} · "
            f"completion total {a['completion_total']} · wall total {a['wall_total']}s · "
            f"errors: {', '.join(a['errors']) or 'none'} · length cut: {', '.join(a['length_cut']) or 'none'}")


def category_spec_table(recs):
    groups = [("japanese", [r for r in recs if r["category"] == "japanese"]),
              ("non-japanese", [r for r in recs if r["category"] != "japanese"])]
    for c in sorted({r["category"] for r in recs} - {"japanese"}):
        groups.append((c, [r for r in recs if r["category"] == c]))
    out = ["**Spec-decode acceptance by category** (pooled counts; median over questions in parentheses)", "",
           "| group | n | accept | len | per-position |", "|---|---:|---:|---:|---|"]
    for name, rs in groups:
        sp = pool_spec(rs)
        med_a = _med([r.get("spec") or {} for r in rs], "accept_rate", 4)
        med_l = _med([r.get("spec") or {} for r in rs], "mean_accept_len", 3)
        out.append(f"| {name} | {len(rs)} | {spec_short(sp)[0]} ({fmt_pct(med_a)}) | {spec_short(sp)[1]} "
                   f"({fmt(med_l, 2)}) | {per_pos_str(sp)} |")
    return "\n".join(out)


def write_report(jsonl_path):
    jsonl_path = Path(jsonl_path)
    recs = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
    model = (recs[0].get("label") or recs[0]["model"]) if recs else "?"
    eb = recs[0].get("extra_body") if recs else None
    md = [f"# Eval: `{model}`", "", f"source: `{jsonl_path.name}`" +
          (f" · extra body: `{json.dumps(eb, ensure_ascii=False)}`" if eb else ""), "", agg_line(aggregate(recs)), "",
          summary_table(recs), "", category_spec_table(recs), ""]
    for r in recs:
        md += [f"## {r['id']}", "", f"**category:** {r['category']}" +
               (f" · **image:** `{r['image']}`" if r.get("image") else "") +
               (f" · **tools:** {', '.join(sorted({tc['name'] for t in r['turns'] for tc in t['tool_calls']})) or '(none called)'}"
                if r['category'] == 'agentic' else ""), ""]
        md += ["**Prompt:**", "", quote(short(r["prompt"], 400)), ""]
        md += ["**Check:** " + r["check"], ""]
        trace = tool_trace_md(r)
        if trace:
            md += ["**Tool trace:**", "", trace, ""]
        if r.get("calendar_events"):
            md += ["**Calendar events created:** `" + json.dumps(r["calendar_events"], ensure_ascii=False) + "`", ""]
        md += ["**Answer:**", "", r["answer"] or "_(empty)_", ""]
        md += ["*" + stats_line(r) + "*", ""]
        if r.get("spec"):
            md += [f"*spec decode ({r['spec'].get('source', 'vllm')}): drafts/rounds {r['spec']['drafts']}, draft tokens {r['spec']['draft_tokens']}, accepted "
                   f"{r['spec']['accepted']} · per-position {per_pos_str(r['spec'])}*", ""]
        tl = turn_lines(r)
        if tl:
            md += [tl, ""]
        if r["reasoning"]:
            md += ["<details><summary>reasoning ({} 字)</summary>".format(r["reasoning_chars"]), "",
                   "```text", r["reasoning"].replace("```", "ˋˋˋ"), "```", "", "</details>", ""]
        md += ["---", ""]
    out = jsonl_path.with_suffix(".md")
    out.write_text("\n".join(md))
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--base-url", default=os.environ.get("PRISM_GW_BASE", DEFAULT_BASE))
    ap.add_argument("--questions", default=str(EVAL_DIR / "questions.json"))
    ap.add_argument("--merge-into", help="existing results .jsonl; replace records with the same id")
    ap.add_argument("--report", help="only regenerate the .md for this .jsonl")
    ap.add_argument("--extra-body", help="JSON object merged into every request body, e.g. '{\"reasoning_effort\":\"high\"}'")
    ap.add_argument("--tag", help="label suffix: results file <model>-<ts>-<tag>.jsonl, shown as model[tag]")
    ap.add_argument("--metrics-url", default=os.environ.get("PRISM_METRICS_URL", "http://172.28.0.1:8888/metrics"),
                    help="vLLM Prometheus endpoint for spec-decode acceptance ('' to disable)")
    a = ap.parse_args()

    if a.report:
        print(write_report(a.report))
        return
    if not a.model:
        ap.error("--model is required")

    extra_body = None
    if a.extra_body:
        extra_body = json.loads(a.extra_body)
        if not isinstance(extra_body, dict):
            ap.error("--extra-body must be a JSON object")
    qs = json.loads(Path(a.questions).read_text())
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
        unknown = set(want) - {q["id"] for q in qs}
        if unknown:
            sys.exit(f"unknown ids: {sorted(unknown)}")
        qs = [q for q in qs if q["id"] in want]

    global METRICS_URL
    METRICS_URL = a.metrics_url or None
    if METRICS_URL and scrape_spec() is None:
        print(f"note: no spec-decode metrics at {METRICS_URL}; acceptance will be null", flush=True)
    key = load_api_key()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", a.model)
    if a.merge_into:
        final_path = Path(a.merge_into)
        out_path = final_path.with_suffix(".partial.jsonl")
    else:
        suffix = "-" + re.sub(r"[^A-Za-z0-9._-]+", "_", a.tag) if a.tag else ""
        final_path = out_path = RESULTS_DIR / f"{safe_model}-{dt.datetime.now():%Y%m%d-%H%M}{suffix}.jsonl"
    out_path.write_text("")

    for i, q in enumerate(qs, 1):
        print(f"[{i}/{len(qs)}] {q['id']} ...", end=" ", flush=True)
        rec = run_question(a.base_url, key, a.model, q, extra_body, a.tag)
        with out_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{rec['finish_reason']} {rec['completion_tokens']}tok {fmt(rec['decode_tps'])}tok/s "
              f"{rec['wall']}s tools={rec['n_tool_calls']} "
              f"acc={fmt_pct((rec.get('spec') or {}).get('accept_rate'))} "
              f"len={fmt((rec.get('spec') or {}).get('mean_accept_len'), 2)}" + (f" ERROR {short(rec['error'], 150)}" if rec["error"] else ""),
              flush=True)

    if a.merge_into:
        new = {json.loads(l)["id"]: l for l in out_path.read_text().splitlines() if l.strip()}
        old = [l for l in final_path.read_text().splitlines() if l.strip()]
        merged, seen = [], set()
        for l in old:
            rid = json.loads(l)["id"]
            merged.append(new.get(rid, l))
            seen.add(rid)
        merged += [l for rid, l in new.items() if rid not in seen]
        final_path.write_text("\n".join(merged) + "\n")
        out_path.unlink()

    print("jsonl:", final_path)
    print("md:   ", write_report(final_path))


if __name__ == "__main__":
    main()
