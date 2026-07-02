#!/bin/sh
# s6 cont-init hook (bind-mounted to /etc/cont-init.d/ via docker-compose).
#
# In non-free channels the Discord adapter requires an @mention (require_mention)
# to respond. A REPLY to the bot should count as addressing it too — otherwise
# "reply to ふぐ" in a normal channel is silently dropped. The default mention
# check only looks at message.mentions + mention_prefix, which misses replies
# whose ping is off (mentions[] empty). This patch makes a reply whose
# referenced message was authored by the bot satisfy the mention requirement.
#
# Pairs with 40-discord-open-gate.sh (which opens the user-allowlist for the same
# reply case). Patches in-image code, re-applied at boot. Never fails init.
python3 - <<'PY' || true
import sys

PATH = "/opt/hermes/plugins/platforms/discord/adapter.py"
MARKER = "[discord-reply-mention]"

OLD = (
    "            if require_mention and not is_free_channel and not in_bot_thread:\n"
    "                if self._client.user not in message.mentions and not mention_prefix:\n"
    "                    return\n"
)

NEW = (
    "            if require_mention and not is_free_channel and not in_bot_thread:\n"
    "                # [discord-reply-mention] a reply to the bot counts as addressing\n"
    "                # it, even when the reply ping is off (mentions[] then empty).\n"
    "                _rm_reply_to_bot = False\n"
    "                try:\n"
    "                    _rm_ref = getattr(message, \"reference\", None)\n"
    "                    if _rm_ref is not None and self._client.user is not None:\n"
    "                        _rm_res = getattr(_rm_ref, \"resolved\", None)\n"
    "                        _rm_ra = getattr(_rm_res, \"author\", None)\n"
    "                        if _rm_ra is not None and getattr(_rm_ra, \"id\", None) == self._client.user.id:\n"
    "                            _rm_reply_to_bot = True\n"
    "                        elif self._client.user in message.mentions:\n"
    "                            _rm_reply_to_bot = True\n"
    "                except Exception:\n"
    "                    _rm_reply_to_bot = False\n"
    "                if self._client.user not in message.mentions and not mention_prefix and not _rm_reply_to_bot:\n"
    "                    return\n"
)

try:
    src = open(PATH, encoding="utf-8").read()
except OSError as e:
    print("[discord-reply-mention] adapter not found, skipping:", e, flush=True)
    sys.exit(0)

if MARKER in src:
    print("[discord-reply-mention] already applied", flush=True)
    sys.exit(0)

if OLD not in src:
    print("[discord-reply-mention] anchor not found (image changed?), skipping", flush=True)
    sys.exit(0)

open(PATH, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print("[discord-reply-mention] patched", flush=True)
PY
exit 0
