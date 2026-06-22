#!/bin/sh
# s6 cont-init hook (bind-mounted to /etc/cont-init.d/ via docker-compose).
#
# LINE's text bubble has zero Markdown support. The in-image stripper
# strip_markdown_preserving_urls() already removes bold/italic/code/headings/
# bullets, but it leaves Markdown TABLES untouched, so a table renders on LINE
# as raw "| a | b |" pipes. This idempotently patches that function to flatten
# tables: separator rows (|---|:--:|) are dropped and each data row becomes
# "cell / cell / cell".
#
# Patches /opt/hermes/... which lives in the IMAGE (not the hermes-data volume),
# so the edit is lost on every container recreate — re-applying it here at boot
# makes it persistent. Never fails container init (always exits 0; no-ops if
# already applied or if the anchor/file is missing after an image update).
python3 - <<'PY' || true
import sys

PATH = "/opt/hermes/plugins/platforms/line/adapter.py"
MARKER = "[line-table-strip]"
ANCHOR = '    text = _MD_BULLET_RE.sub("• ", text)\n'
PATCH = (
    "\n"
    "    # [line-table-strip] flatten Markdown tables — LINE shows them as raw pipes.\n"
    "    _tbl_lines = []\n"
    '    for _ln in text.split("\\n"):\n'
    "        _s = _ln.strip()\n"
    '        if ("|" in _s) and (_s.startswith("|") or _s.count("|") >= 2):\n'
    '            if set(_s) <= set("|-: "):\n'
    "                continue  # separator row like |---|:--:|\n"
    '            _cells = [c.strip() for c in _s.strip("|").split("|")]\n'
    '            _tbl_lines.append(" / ".join(c for c in _cells if c))\n'
    "        else:\n"
    "            _tbl_lines.append(_ln)\n"
    '    text = "\\n".join(_tbl_lines)\n'
)

try:
    src = open(PATH, encoding="utf-8").read()
except OSError as e:
    print("[line-table-strip] adapter not found, skipping:", e, flush=True)
    sys.exit(0)

if MARKER in src:
    print("[line-table-strip] already applied", flush=True)
    sys.exit(0)

i = src.find(ANCHOR)
if i == -1:
    print("[line-table-strip] anchor not found (image changed?), skipping", flush=True)
    sys.exit(0)

at = i + len(ANCHOR)
open(PATH, "w", encoding="utf-8").write(src[:at] + PATCH + src[at:])
print("[line-table-strip] patched", flush=True)
PY
exit 0
