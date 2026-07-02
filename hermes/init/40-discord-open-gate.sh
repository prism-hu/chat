#!/bin/sh
# s6 cont-init hook (bind-mounted to /etc/cont-init.d/ via docker-compose).
#
# By default the Discord adapter enforces DISCORD_ALLOWED_USERS globally, so
# only listed users (here: just えんだ) ever get a response — in EVERY channel.
# We want it open to anyone in two cases:
#   - home / free-response channels  → respond to ALL messages from ANYONE
#   - any other channel              → respond when ANYONE REPLIES to the bot
# DMs and non-reply messages in other channels stay restricted to the allowlist.
#
# This patches the user-allowlist gate in on_message() to bypass
# _is_allowed_user for those two cases. The mention/require_mention gates
# already pass for them (home is free-response; a reply pings the bot so it
# lands in message.mentions), so this single gate is the only change needed.
#
# Patches /opt/hermes/... which lives in the IMAGE (not the hermes-data
# volume), so the edit is lost on every container recreate — re-applying it
# here at boot makes it persistent. Never fails container init.
python3 - <<'PY' || true
import sys

PATH = "/opt/hermes/plugins/platforms/discord/adapter.py"
MARKER = "[discord-open-gate]"

OLD = (
    '                    _msg_guild = getattr(message, "guild", None)\n'
    '                    _is_dm = isinstance(message.channel, discord.DMChannel) or _msg_guild is None\n'
    "                    if not self._is_allowed_user(\n"
    "                        str(message.author.id),\n"
    "                        message.author,\n"
    "                        guild=_msg_guild,\n"
    "                        is_dm=_is_dm,\n"
    "                    ):\n"
    "                        return\n"
)

NEW = (
    '                    _msg_guild = getattr(message, "guild", None)\n'
    '                    _is_dm = isinstance(message.channel, discord.DMChannel) or _msg_guild is None\n'
    "                    # [discord-open-gate] bypass the user allowlist for:\n"
    "                    #   - home/free-response channels (anyone, any message)\n"
    "                    #   - replies to THIS bot in any channel (anyone)\n"
    "                    _og_open = False\n"
    "                    try:\n"
    "                        _og_chs = {str(message.channel.id)}\n"
    '                        _og_pid = getattr(message.channel, "parent_id", None)\n'
    "                        if _og_pid:\n"
    "                            _og_chs.add(str(_og_pid))\n"
    "                        _og_free = adapter_self._discord_free_response_channels()\n"
    '                        if ("*" in _og_free) or (_og_chs & _og_free):\n'
    "                            _og_open = True\n"
    '                        elif getattr(message, "type", None) == discord.MessageType.reply and self._client.user is not None:\n'
    '                            _og_ref = getattr(message, "reference", None)\n'
    '                            _og_res = getattr(_og_ref, "resolved", None) if _og_ref else None\n'
    '                            _og_ra = getattr(_og_res, "author", None)\n'
    "                            if (_og_ra is not None and getattr(_og_ra, \"id\", None) == self._client.user.id) or (self._client.user in message.mentions):\n"
    "                                _og_open = True\n"
    "                    except Exception:\n"
    "                        _og_open = False\n"
    "                    if not _og_open and not self._is_allowed_user(\n"
    "                        str(message.author.id),\n"
    "                        message.author,\n"
    "                        guild=_msg_guild,\n"
    "                        is_dm=_is_dm,\n"
    "                    ):\n"
    "                        return\n"
)

try:
    src = open(PATH, encoding="utf-8").read()
except OSError as e:
    print("[discord-open-gate] adapter not found, skipping:", e, flush=True)
    sys.exit(0)

if MARKER in src:
    print("[discord-open-gate] already applied", flush=True)
    sys.exit(0)

if OLD not in src:
    print("[discord-open-gate] anchor not found (image changed?), skipping", flush=True)
    sys.exit(0)

open(PATH, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print("[discord-open-gate] patched", flush=True)
PY
exit 0
