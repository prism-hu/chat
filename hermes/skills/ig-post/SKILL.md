---
name: ig-post
description: "Post a photo (with caption) to Instagram via instagrapi (unofficial private-API client). Use when asked to post / publish / upload to Instagram, including cross-platform requests (e.g. a Discord instruction to post an image to IG). Requires an image — IG feed posts cannot be text-only."
version: 1.0.0
author: prism
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Instagram, Social-media, Post, Cross-platform, instagrapi]
prerequisites:
  env: []
---

# Instagram post (instagrapi)

Publish a photo with a caption to an Instagram account, using
[`instagrapi`](https://github.com/subzeroid/instagrapi) (an **unofficial**
client that talks to Instagram's private mobile API).

## ⚠️ Account-safety warning (read first)

This uses Instagram's private API, **not** the official Graph API. Instagram may
flag automated logins — especially the **first login from a datacenter/server
IP** — and challenge or **ban** the account. Mitigations this skill applies:

- **Session reuse**: the login session is cached to `.ig_session.json` so we do
  NOT re-login on every post (re-login is the main ban trigger).
- Keep posting **low-frequency** and human-like.
- **Do not use a precious account.** Prefer a secondary / disposable account.
  For a business-critical account, switch to the official Meta Graph API instead.

## Credentials (you add these manually, like the LINE token)

Two files at the skill root (chmod 600), never pasted into chat:

```
<skill>/.ig_user      # one line: the IG username
<skill>/.ig_pass      # one line: the IG password
```

The session cache `<skill>/.ig_session.json` is created on first successful
login and reused afterward.

## Usage

```bash
PY=/opt/data/ig-venv/bin/python   # persistent venv with instagrapi installed

# Post a local image with a caption:
$PY scripts/post_instagram.py --image /opt/data/image_cache/foo.jpg --caption "今日のひとこと #北医AI研"

# Caption from stdin:
echo "キャプション本文" | $PY scripts/post_instagram.py --image /path/to/pic.jpg

# Validate setup without posting (checks creds + image + venv, logs in, no upload):
$PY scripts/post_instagram.py --image /path/to/pic.jpg --caption "test" --dry-run
```

On success prints `OK: posted <media_pk>`. On failure prints the error and exits
non-zero.

## Notes

- **Image required.** A feed post needs a photo (JPEG/PNG). Text-only IG posts do
  not exist. For Stories/Reels/video, extend the script (`photo_upload` →
  `clip_upload` / `photo_upload_to_story`).
- The instagrapi venv lives at `/opt/data/ig-venv` (persistent across container
  recreates); the skill + creds live under `/opt/data/skills/...` (also
  persistent).
- Upgrade path: replace this skill with an official Meta Graph API flow if you
  later move to a business/creator account.
