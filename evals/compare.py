#!/usr/bin/env python3
"""Side-by-side comparison of eval runs.

Usage: python3 evals/compare.py evals/results/a.jsonl evals/results/b.jsonl [...]
Writes evals/results/compare-<YYYYmmdd-HHMM>.md
"""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402
from run import fmt, quote, short  # noqa: E402


def load(path):
    recs = [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]
    model = (recs[0].get("label") or recs[0]["model"]) if recs else Path(path).stem
    return model, Path(path).name, {r["id"]: r for r in recs}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    runs = [load(p) for p in sys.argv[1:]]
    labels = []
    for model, fname, _ in runs:  # disambiguate same model run twice
        labels.append(model if [m for m, _, _ in runs].count(model) == 1 else f"{model} ({fname})")

    ids = []
    for _, _, recs in runs:
        ids += [i for i in recs if i not in ids]

    md = ["# Eval comparison", "", "runs: " + ", ".join(f"`{fn}`" for _, fn, _ in runs), ""]

    md += ["## Per-model summary (medians over questions)", "",
           "| model | n | TTFT s | TTFA s | think s | decode tok/s | reas tok/s* | ans tok/s* | prefill tok/s | "
           "completion tok | reas tok* | ans tok* | accept | len | wall s | wall total s | errors | length cut |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for label, (_, _, recs) in zip(labels, runs):
        a = run.aggregate(list(recs.values()))
        md.append(f"| {label} | {a['n']} | {fmt(a['ttft_median'], 2)} | {fmt(a['ttfa_median'], 2)} | "
                  f"{fmt(a['think_s_median'])} | {fmt(a['decode_tps_median'])} | {fmt(a['reasoning_tps_median'])} | "
                  f"{fmt(a['answer_tps_median'])} | {fmt(a['prefill_tps_median'], 0)} | "
                  f"{fmt(a['completion_tokens_median'], 0)} | {fmt(a['reasoning_tokens_median'], 0)} | "
                  f"{fmt(a['answer_tokens_median'], 0)} | {run.fmt_pct(a['accept_rate_median'])} | "
                  f"{fmt(a['mean_accept_len_median'], 2)} | {fmt(a['wall_median'])} | {a['wall_total']} | "
                  f"{', '.join(a['errors']) or '-'} | {', '.join(a['length_cut']) or '-'} |")
    md += ["", "*reasoning/answer token split is estimated from per-chunk usage unless the server reports "
           "`completion_tokens_details.reasoning_tokens`.*", "", run.SPEC_NOTE, ""]
    md += ["## Spec-decode acceptance by category (pooled; ja vs others)", "",
           "| group | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    cats = sorted({r["category"] for _, _, recs in runs for r in recs.values()} - {"japanese"})
    groups = [("japanese", lambda r: r["category"] == "japanese"),
              ("non-japanese", lambda r: r["category"] != "japanese")] + \
             [(c, (lambda c: lambda r: r["category"] == c)(c)) for c in cats]
    for gname, pred in groups:
        cells = []
        for _, _, recs in runs:
            sp = run.pool_spec([r for r in recs.values() if pred(r)])
            a_, l_ = run.spec_short(sp)
            cells.append(f"{a_} / len {l_}")
        md.append(f"| {gname} | " + " | ".join(cells) + " |")
    md.append("")

    md += ["## Per-question metrics", "", run.SUMMARY_HEADER[0].replace("| id | category |", "| id | model |"),
           run.SUMMARY_HEADER[1]]
    for qid in ids:
        for label, (_, _, recs) in zip(labels, runs):
            r = recs.get(qid)
            if r:
                row = run.summary_row(dict(r, category=label), label=f"[{qid}](#{qid})")
                md.append(row)
            else:
                md.append(f"| {qid} | {label} | " + " | ".join(["-"] * 14) + " |")
    md.append("")

    for qid in ids:
        first = next(recs[qid] for _, _, recs in runs if qid in recs)
        md += [f"## {qid}", "", f"**category:** {first['category']}" +
               (f" · **image:** `{first['image']}`" if first.get("image") else ""), "",
               "**Prompt:**", "", quote(short(first["prompt"], 400)), "", "**Check:** " + first["check"], ""]
        for label, (_, _, recs) in zip(labels, runs):
            r = recs.get(qid)
            md += [f"### {label}", ""]
            if not r:
                md += ["_(not run)_", ""]
                continue
            trace = run.tool_trace_md(r)
            if trace:
                md += ["**Tool trace:**", "", trace, ""]
            if r.get("calendar_events"):
                md += ["**Calendar events:** `" + json.dumps(r["calendar_events"], ensure_ascii=False) + "`", ""]
            md += [r["answer"] or "_(empty)_", "", "*" + run.stats_line(r) + "*", ""]
            if r.get("spec"):
                md += [f"*spec per-position: {run.per_pos_str(r['spec'])}*", ""]
            if r["reasoning"]:
                md += [f"<details><summary>reasoning ({r['reasoning_chars']} 字)</summary>", "", "```text",
                       r["reasoning"].replace("```", "ˋˋˋ"), "```", "", "</details>", ""]
        md += ["---", ""]

    out = run.RESULTS_DIR / f"compare-{dt.datetime.now():%Y%m%d-%H%M}.md"
    out.write_text("\n".join(md))
    print(out)


if __name__ == "__main__":
    main()
