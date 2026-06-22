#!/usr/bin/env python3
"""Post a tweet to X for free via twikit (unofficial private-API client).

Run with the persistent venv that has twikit installed:
    /opt/data/x-venv/bin/python post_x.py "text"  [--image PIC.jpg] [--dry-run]

Credentials (chmod 600 files at the skill root, never pasted into chat):
    ../.x_user    X username/handle (no @)
    ../.x_email   account email
    ../.x_pass    account password
    ../.x_totp    OPTIONAL TOTP secret (if 2FA enabled)
Cookie cache ../.x_cookies.json is written on first login and reused, so we
avoid a full re-login on every post (re-login is the main account-flag trigger).
"""
import argparse
import asyncio
import os
import sys

SKILL_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
USER_FILE = os.path.join(SKILL_ROOT, ".x_user")
EMAIL_FILE = os.path.join(SKILL_ROOT, ".x_email")
PASS_FILE = os.path.join(SKILL_ROOT, ".x_pass")
TOTP_FILE = os.path.join(SKILL_ROOT, ".x_totp")
COOKIES_FILE = os.path.join(SKILL_ROOT, ".x_cookies.json")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


async def run(text: str, image: str | None, dry_run: bool) -> None:
    username, email, password = _read(USER_FILE), _read(EMAIL_FILE), _read(PASS_FILE)
    totp = _read(TOTP_FILE) or None
    if not username or not email or not password:
        sys.exit(f"error: missing creds — create {USER_FILE}, {EMAIL_FILE}, {PASS_FILE} (chmod 600)")

    from twikit import Client

    client = Client("en-US")
    # Preferred: cookie-based session (auth_token + ct0). Username/password login
    # is blocked from datacenter IPs (X's login JS-challenge fails server-side), so
    # cookies are the working free path. .x_cookies.json is materialized from
    # X_AUTH_TOKEN / X_CT0 at boot (init/20-social-creds.sh). Falls back to
    # user/pass login only if no cookie file exists.
    if os.path.isfile(COOKIES_FILE):
        try:
            client.load_cookies(COOKIES_FILE)
            await client.user()  # validate the session
        except Exception as e:
            sys.exit(f"cookie session invalid (re-export auth_token/ct0): {e}")
    else:
        try:
            await client.login(
                auth_info_1=username,
                auth_info_2=email,
                password=password,
                totp_secret=totp,
                cookies_file=COOKIES_FILE,
            )
            os.chmod(COOKIES_FILE, 0o600)
        except Exception as e:
            sys.exit(f"login failed (try cookie auth via X_AUTH_TOKEN/X_CT0): {e}")

    if dry_run:
        print(f"DRY-RUN OK: logged in as @{username}; not posting")
        return

    media_ids = None
    if image:
        if not os.path.isfile(image):
            sys.exit(f"error: image not found: {image}")
        try:
            mid = await client.upload_media(image, wait_for_completion=True)
            media_ids = [mid]
        except Exception as e:
            sys.exit(f"media upload failed: {e}")

    try:
        tweet = await client.create_tweet(text=text, media_ids=media_ids)
    except Exception as e:
        sys.exit(f"tweet failed: {e}")
    tid = getattr(tweet, "id", None)
    print(f"OK: posted {tid}")
    if tid:
        print(f"https://x.com/{username}/status/{tid}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Post a tweet to X via twikit (free/unofficial).")
    ap.add_argument("text", nargs="?", help="tweet text (else read stdin)")
    ap.add_argument("--image", help="optional image path (JPEG/PNG)")
    ap.add_argument("--dry-run", action="store_true", help="login only, do NOT post")
    args = ap.parse_args()

    text = (args.text if args.text is not None else sys.stdin.read()).strip()
    if not text and not args.dry_run:
        sys.exit("error: empty tweet text")
    asyncio.run(run(text, args.image, args.dry_run))


if __name__ == "__main__":
    main()
