#!/usr/bin/env python3
"""Post a text message to a LINE group (or any LINE chat id) via the Messaging
API push endpoint.

Auth: LINE_CHANNEL_ACCESS_TOKEN (already in the Hermes gateway environment).
The bot must be a member of the target group.

Examples:
    post_line_group.py --to home "deploy finished"
    echo "本文" | post_line_group.py --to 北医AI研
    post_line_group.py --to Cb3...95cf --dry-run "preview only"
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# alias -> LINE groupId. Keep lowercase keys; lookups are case-insensitive.
ALIASES = {
    "home": "Cb365dbddbe5bd70762ffb51d48ff95cf",
    "北医ai研": "Cb365dbddbe5bd70762ffb51d48ff95cf",
}

PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_TEXT_LIMIT = 5000

# The gateway keeps secrets OUT of the agent's code-execution sandbox, so the
# LINE_CHANNEL_ACCESS_TOKEN env var is usually absent when the agent runs this.
# Fallback: a chmod-600 token file dropped at the skill root (one dir up).
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, ".line_token")


def get_token() -> str:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def resolve(target: str) -> str:
    return ALIASES.get(target.lower(), target)


def main() -> None:
    ap = argparse.ArgumentParser(description="Push a text message to a LINE group.")
    ap.add_argument("--to", required=True, help="group alias or raw LINE chat id")
    ap.add_argument("message", nargs="?", help="message text (else read stdin)")
    ap.add_argument("--dry-run", action="store_true", help="print payload, do not send")
    args = ap.parse_args()

    token = get_token()
    if not token and not args.dry_run:
        sys.exit(
            "error: no LINE token (env LINE_CHANNEL_ACCESS_TOKEN unset and "
            f"{TOKEN_FILE} missing/empty)"
        )

    text = (args.message if args.message is not None else sys.stdin.read()).strip()
    if not text:
        sys.exit("error: empty message")
    if len(text) > LINE_TEXT_LIMIT:
        text = text[: LINE_TEXT_LIMIT - 1] + "…"

    to = resolve(args.to)
    payload = {"to": to, "messages": [{"type": "text", "text": text}]}

    if args.dry_run:
        print("DRY-RUN: would POST to", PUSH_URL)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    req = urllib.request.Request(
        PUSH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        print(f"OK {resp.status}: sent to {to}")
    except urllib.error.HTTPError as e:
        sys.exit(f"LINE API error {e.code}: {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"network error: {e.reason}")


if __name__ == "__main__":
    main()
