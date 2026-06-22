---
name: line-group-post
description: "Post a text message to a LINE group (e.g. 北医AI研) via the LINE Messaging API push endpoint. Use when asked to send, forward, announce, or relay something to a LINE group — including cross-platform requests such as a Discord instruction like 'LINEの北医AI研グループに〜と送って'."
version: 1.0.0
author: prism
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [LINE, Messaging, Group, Cross-platform, Push, Communication]
prerequisites:
  env: [LINE_CHANNEL_ACCESS_TOKEN]
---

# LINE group post

Send a text message into a LINE **group** chat using the LINE Messaging API
**push** endpoint (`POST https://api.line.me/v2/bot/message/push`). The bot must
already be a member of the group.

## Why this skill exists

Hermes' built-in `hermes send` can only target LINE chats it has registered in
its directory, and a chat is registered only after it processes an inbound
*message* from it. A group that has only produced `join`/`leave` lifecycle
events is therefore **not** a valid `hermes send` target ("Could not resolve").

The push endpoint has no such restriction: given the `groupId` and the channel
access token, the bot can post to any group it belongs to. This skill wraps that
call so the agent can post to a known group directly — e.g. when instructed from
**Discord** to relay a message to LINE.

## When to use

- "LINEの北医AI研（グループ）に〜と送って / 流して / 共有して"
- A cross-platform relay: a Discord message asking to forward something to the
  LINE group.
- Posting an announcement / cron result into the LINE group.

Do **not** use this for replying to a 1:1 LINE DM — the normal gateway flow
handles those.

## Usage

```bash
# By alias (recommended):
python3 scripts/post_line_group.py --to home "ビルドが終わりました ✅"

# Pipe the body in:
echo "本文" | python3 scripts/post_line_group.py --to 北医AI研

# By raw group id:
python3 scripts/post_line_group.py --to Cb365dbddbe5bd70762ffb51d48ff95cf "本文"

# Preview the request without sending:
python3 scripts/post_line_group.py --to home --dry-run "テスト"
```

Auth comes from `LINE_CHANNEL_ACCESS_TOKEN` (already present in the gateway
environment). On success it prints `OK 200 ...`; on failure it prints the LINE
API error body and exits non-zero.

## Known groups (aliases)

| alias            | groupId                              | name   |
|------------------|--------------------------------------|--------|
| `home` / `北医AI研` | `Cb365dbddbe5bd70762ffb51d48ff95cf` | 北医AI研 |

To add another group later: invite the bot, get its `groupId` from a webhook
`join`/`message` event (or the gateway log), and add a row to `ALIASES` in
`scripts/post_line_group.py`.
