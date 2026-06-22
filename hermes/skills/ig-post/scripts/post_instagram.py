#!/usr/bin/env python3
"""Post a photo + caption to Instagram via instagrapi.

Run with the persistent venv that has instagrapi installed:
    /opt/data/ig-venv/bin/python post_instagram.py --image PIC.jpg --caption "..."

Credentials (chmod 600 files at the skill root, never pasted into chat):
    ../.ig_user   one line: IG username
    ../.ig_pass   one line: IG password
Session cache ../.ig_session.json is written on first login and reused, so we
avoid re-login on every post (re-login is the main account-flag trigger).
"""
import argparse
import os
import sys

SKILL_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
USER_FILE = os.path.join(SKILL_ROOT, ".ig_user")
PASS_FILE = os.path.join(SKILL_ROOT, ".ig_pass")
SESSION_FILE = os.path.join(SKILL_ROOT, ".ig_session.json")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def main() -> None:
    ap = argparse.ArgumentParser(description="Post a photo+caption to Instagram.")
    ap.add_argument("--image", required=True, help="path to the image (JPEG/PNG)")
    ap.add_argument("--caption", help="caption text (else read stdin; may be empty)")
    ap.add_argument("--dry-run", action="store_true", help="login + validate, do NOT upload")
    args = ap.parse_args()

    username, password = _read(USER_FILE), _read(PASS_FILE)
    if not username or not password:
        sys.exit(f"error: missing creds — create {USER_FILE} and {PASS_FILE} (chmod 600)")
    if not os.path.isfile(args.image):
        sys.exit(f"error: image not found: {args.image}")

    caption = args.caption if args.caption is not None else sys.stdin.read()
    caption = caption.strip()

    from instagrapi import Client
    from instagrapi.exceptions import ClientError

    cl = Client()
    # Reuse a cached session when present; only do a full login if needed.
    if os.path.isfile(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
        except Exception:
            pass
    try:
        cl.login(username, password)  # no-op-ish if settings already authenticate
    except ClientError as e:
        sys.exit(f"login failed: {e}")
    try:
        cl.dump_settings(SESSION_FILE)
        os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass

    if args.dry_run:
        print(f"DRY-RUN OK: logged in as {username}; image+caption validated; not posting")
        return

    try:
        media = cl.photo_upload(args.image, caption)
    except Exception as e:  # instagrapi raises a wide range; surface it
        sys.exit(f"upload failed: {e}")
    print(f"OK: posted {getattr(media, 'pk', media)}")


if __name__ == "__main__":
    main()
