---
name: x-post
description: "Post a tweet to X (Twitter) for FREE via twikit (unofficial, username/password — no paid X API). Optional image. Use when asked to post / tweet / publish to X, including cross-platform requests (e.g. a Discord instruction to tweet something). This is the no-cost alternative to the official xurl skill, which requires paid X API credits."
version: 1.0.0
author: prism
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [X, Twitter, Social-media, Post, Cross-platform, twikit, free]
prerequisites:
  env: []
---

# X (Twitter) post — free, via twikit

Post a tweet using [`twikit`](https://github.com/d60/twikit), an **unofficial**
client that talks to X's internal web API with a normal account login. **No paid
X API / no credits required** — this is the $0 alternative to the official
`xurl` skill (which now needs paid credits to post in 2026).

## ⚠️ Account-safety warning (read first)

Unofficial = against X's ToS. Risks: account flag/ban, and **breakage whenever X
changes its web API** (twikit works intermittently). Mitigations here:

- **Cookie/session reuse**: login cookies are cached to `.x_cookies.json` so we
  do NOT do a full login on every post (re-login is the main flag trigger).
- Keep posting **low-frequency** and human-like.
- Prefer a **non-precious account**. If X posting is business-critical, pay for
  the official API and use the `xurl` skill instead.

## Credentials (you add these manually, like the LINE/IG creds — never in chat)

Files at the skill root (chmod 600):

```
<skill>/.x_user    # one line: X username/handle (without @)
<skill>/.x_email   # one line: account email
<skill>/.x_pass    # one line: account password
<skill>/.x_totp    # OPTIONAL one line: TOTP secret, if 2FA is enabled
```

The cookie cache `<skill>/.x_cookies.json` is created on first login and reused.

## Usage

```bash
PY=/opt/data/x-venv/bin/python   # persistent venv with twikit installed

# Text tweet:
$PY scripts/post_x.py "Hermesからのテスト投稿です 🐡"

# From stdin:
echo "本文" | $PY scripts/post_x.py

# With an image:
$PY scripts/post_x.py --image /opt/data/image_cache/foo.jpg "画像つき"

# Validate login without posting:
$PY scripts/post_x.py --dry-run "test"
```

On success prints `OK: posted <tweet_id>` and the tweet URL. On failure prints
the error and exits non-zero.

## Notes

- twikit venv: `/opt/data/x-venv` (persistent across recreates). Skill + creds:
  `/opt/data/skills/.../x-post` (persistent).
- If posts start failing after an X update, `uv pip install --python
  /opt/data/x-venv/bin/python -U twikit` to pull a fixed version.
- The official, ToS-safe path is the `xurl` skill (needs paid X API credits).
