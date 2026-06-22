#!/bin/sh
# s6 cont-init hook (bind-mounted to /etc/cont-init.d/ via docker-compose).
#
# The LINE adapter has no mention-gating, so by default the bot would respond to
# EVERY message in a group. This idempotently patches the in-image adapter so
# that in groups/rooms the bot only responds when the text contains "@ふぐ"
# (or "@fugu"). DMs are unaffected.
#
# It patches /opt/hermes/... which lives in the IMAGE (not the hermes-data
# volume), so the edit is lost on every container recreate — re-applying it here
# at boot makes it persistent. Written to NEVER fail container init (always
# exits 0; no-ops if already applied or if the anchor/file is missing after an
# image update).
python3 - <<'PY' || true
import sys

PATH = "/opt/hermes/plugins/platforms/line/adapter.py"
MARKER = "[fugu-gate]"
ANCHOR = "    async def _handle_message_event(self, event: Dict[str, Any]) -> None:\n"
GATE = (
    '        # [fugu-gate] groups/rooms: only respond to TEXT containing "ふぐ";\n'
    '        # ignore everything else (other text, stickers, media). DMs unaffected.\n'
    '        _g_src = event.get("source") or {}\n'
    '        _g_msg = event.get("message") or {}\n'
    '        if _g_src.get("type") in ("group", "room"):\n'
    '            _g_t = (_g_msg.get("text") or "") if _g_msg.get("type") == "text" else ""\n'
    '            if "ふぐ" not in _g_t:\n'
    '                return\n'
)

try:
    src = open(PATH, encoding="utf-8").read()
except OSError as e:
    print("[fugu-gate] adapter not found, skipping:", e, flush=True)
    sys.exit(0)

if MARKER in src:
    print("[fugu-gate] already applied", flush=True)
    sys.exit(0)

i = src.find(ANCHOR)
if i == -1:
    print("[fugu-gate] anchor not found (image changed?), skipping", flush=True)
    sys.exit(0)

at = i + len(ANCHOR)
open(PATH, "w", encoding="utf-8").write(src[:at] + GATE + src[at:])
print("[fugu-gate] patched", flush=True)
PY
exit 0
