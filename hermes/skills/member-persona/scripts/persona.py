#!/usr/bin/env python3
"""member-persona — resolve circle members across LINE/Discord and remember a loose persona.

Subcommands (all print JSON to stdout):
  resolve   --platform <line|discord> --user-id <id> [--display-name N] [--group-id G] [--no-fetch]
  link      --person <pid> --platform <p> --user-id <id> [--display-name N]
  merge     --into <pid> --from <pid>
  get-notes --person <pid>
  add-note  --person <pid> --text "..."
  show      --person <pid>
  list

Identity model:
  people.json = { version, people:{pid:{display,aliases,platforms:{plat:{user_id,display_name,last_seen}},
                  link_confidence,created_at,updated_at}}, index:{"plat:user_id":pid} }
  notes/<pid>.md = free-text loose observations, one bullet per line.

Storage: /opt/data/persona/  (outside skills/memories so the curator never touches it)
LINE display-name resolution uses /v2/bot/profile/{userId} first (works for friends),
then the group-member endpoint as fallback. Never raises on API failure — returns name=None.
Token: this skill's .line_token -> line-group-post/.line_token -> env (sandbox can't read env).
"""
import argparse
import datetime
import difflib
import fcntl
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = "/opt/data/persona"
PEOPLE = os.path.join(BASE, "people.json")
NOTES_DIR = os.path.join(BASE, "notes")
LOCK_FILE = os.path.join(BASE, ".lock")

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOKEN_FILES = [
    os.path.join(_SKILL_DIR, ".line_token"),
    "/opt/data/skills/communication/line-group-post/.line_token",
]

FUZZY_THRESHOLD = 0.6


# ---------- helpers ----------
def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _today():
    return datetime.date.today().isoformat()


def _norm(s):
    return re.sub(r"\s+", "", (s or "").strip().lower())


def _ensure_dirs():
    os.makedirs(NOTES_DIR, exist_ok=True)


def _load():
    _ensure_dirs()
    try:
        with open(PEOPLE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "people": {}, "index": {}}


def _save(data):
    _ensure_dirs()
    tmp = PEOPLE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PEOPLE)


class _Lock:
    def __enter__(self):
        _ensure_dirs()
        self._f = open(LOCK_FILE, "w")
        fcntl.flock(self._f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._f, fcntl.LOCK_UN)
        self._f.close()


def _next_pid(data):
    n = 1
    while f"p_{n:04d}" in data["people"]:
        n += 1
    return f"p_{n:04d}"


def _notes_path(pid):
    return os.path.join(NOTES_DIR, f"{pid}.md")


def _line_token():
    for p in _TOKEN_FILES:
        try:
            with open(p, encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    return t
        except OSError:
            pass
    return os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()


def _line_get(url, tok):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"}, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        return None


def fetch_line_name(user_id, group_id=None):
    """Resolve a LINE userId to a displayName. Returns name or None (never raises)."""
    tok = _line_token()
    if not tok:
        return None
    # plain profile first (works when the user added the bot as a friend)
    data = _line_get(f"https://api.line.me/v2/bot/profile/{user_id}", tok)
    if data and data.get("displayName"):
        return data["displayName"]
    # group-member fallback
    if group_id:
        data = _line_get(
            f"https://api.line.me/v2/bot/group/{group_id}/member/{user_id}/profile", tok
        )
        if data and data.get("displayName"):
            return data["displayName"]
    return None


def _add_alias(person, name):
    if not name:
        return
    if name != person.get("display") and name not in person.setdefault("aliases", []):
        person["aliases"].append(name)


def _best_match(data, name):
    """Return (pid, score) of the closest existing person by display/alias, or (None, 0)."""
    target = _norm(name)
    if not target:
        return None, 0.0
    best_pid, best = None, 0.0
    for pid, p in data["people"].items():
        candidates = [p.get("display", "")] + p.get("aliases", [])
        for c in candidates:
            score = difflib.SequenceMatcher(None, target, _norm(c)).ratio()
            if score > best:
                best_pid, best = pid, score
    return best_pid, round(best, 3)


# ---------- subcommands ----------
def resolve(platform, user_id, display_name=None, group_id=None, no_fetch=False):
    """Map (platform, user_id) -> person, creating/refreshing as needed.

    Library entry point — used by both the CLI and the persona-inject hook.
    Returns a dict: {person_id, display, name_to_use, is_new, suggestion}.
    """
    platform = platform.strip().lower()
    key = f"{platform}:{user_id}"
    with _Lock():
        data = _load()
        # known identity -> return + refresh
        if key in data["index"]:
            pid = data["index"][key]
            person = data["people"][pid]
            plat = person.setdefault("platforms", {}).setdefault(platform, {})
            plat["user_id"] = user_id
            plat["last_seen"] = _now()
            name = display_name
            if not name and platform == "line" and not no_fetch and not plat.get("display_name"):
                name = fetch_line_name(user_id, group_id)
            if name:
                plat["display_name"] = name
                _add_alias(person, name)
            person["updated_at"] = _now()
            _save(data)
            return {"person_id": pid, "display": person.get("display"),
                    "name_to_use": plat.get("display_name") or person.get("display"),
                    "is_new": False, "suggestion": None}

        # unknown identity -> resolve name, fuzzy-check, create
        name = display_name
        if not name and platform == "line" and not no_fetch:
            name = fetch_line_name(user_id, group_id)

        suggestion = None
        if name:
            mpid, score = _best_match(data, name)
            if mpid and score >= FUZZY_THRESHOLD:
                suggestion = {"person_id": mpid, "display": data["people"][mpid].get("display"),
                              "score": score,
                              "hint": "same person? confirm with: persona.py link "
                                      f"--person {mpid} --platform {platform} --user-id {user_id}"}

        pid = _next_pid(data)
        display = name or user_id
        data["people"][pid] = {
            "display": display,
            "aliases": [],
            "platforms": {platform: {"user_id": user_id,
                                     "display_name": name or None,
                                     "last_seen": _now()}},
            "link_confidence": "confirmed",
            "created_at": _now(),
            "updated_at": _now(),
        }
        data["index"][key] = pid
        _save(data)
        return {"person_id": pid, "display": display,
                "name_to_use": display, "is_new": True, "suggestion": suggestion}


def get_notes_text(pid):
    """Return the raw notes markdown for a person (empty string if none)."""
    try:
        with open(_notes_path(pid), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def cmd_resolve(args):
    return resolve(args.platform, args.user_id, args.display_name, args.group_id, args.no_fetch)


def cmd_link(args):
    platform = args.platform.strip().lower()
    key = f"{platform}:{args.user_id}"
    with _Lock():
        data = _load()
        if args.person not in data["people"]:
            return {"error": f"unknown person {args.person}"}
        person = data["people"][args.person]
        person.setdefault("platforms", {})[platform] = {
            "user_id": args.user_id,
            "display_name": args.display_name or None,
            "last_seen": _now(),
        }
        _add_alias(person, args.display_name)
        person["link_confidence"] = "confirmed"
        person["updated_at"] = _now()
        # repoint index; drop any other person that owned this key
        for pid, p in list(data["people"].items()):
            if pid != args.person:
                pl = p.get("platforms", {}).get(platform)
                if pl and pl.get("user_id") == args.user_id:
                    p["platforms"].pop(platform, None)
        data["index"][key] = args.person
        _save(data)
        return {"person_id": args.person, "linked": key, "display": person.get("display")}


def cmd_merge(args):
    with _Lock():
        data = _load()
        if args.into not in data["people"] or getattr(args, "from") not in data["people"]:
            return {"error": "unknown person id(s)"}
        src_id = getattr(args, "from")
        dst = data["people"][args.into]
        src = data["people"][src_id]
        for plat, info in src.get("platforms", {}).items():
            dst.setdefault("platforms", {})[plat] = info
            uid = info.get("user_id")
            if uid:
                data["index"][f"{plat}:{uid}"] = args.into
            _add_alias(dst, info.get("display_name"))
        _add_alias(dst, src.get("display"))
        for a in src.get("aliases", []):
            _add_alias(dst, a)
        dst["updated_at"] = _now()
        # merge notes
        src_notes = _notes_path(src_id)
        if os.path.exists(src_notes):
            with open(src_notes, encoding="utf-8") as f:
                extra = f.read()
            with open(_notes_path(args.into), "a", encoding="utf-8") as f:
                f.write(f"\n<!-- merged from {src_id} -->\n{extra}")
            os.remove(src_notes)
        data["people"].pop(src_id, None)
        _save(data)
        return {"merged_into": args.into, "removed": src_id, "display": dst.get("display")}


def cmd_get_notes(args):
    return {"person_id": args.person, "notes": get_notes_text(args.person)}


def cmd_add_note(args):
    with _Lock():
        data = _load()
        if args.person not in data["people"]:
            return {"error": f"unknown person {args.person}"}
        _ensure_dirs()
        with open(_notes_path(args.person), "a", encoding="utf-8") as f:
            f.write(f"- {_today()} {args.text.strip()}\n")
    return {"person_id": args.person, "added": args.text.strip()}


def cmd_show(args):
    data = _load()
    if args.person not in data["people"]:
        return {"error": f"unknown person {args.person}"}
    notes = cmd_get_notes(args)["notes"]
    return {"person_id": args.person, "person": data["people"][args.person], "notes": notes}


def cmd_list(args):
    data = _load()
    out = []
    for pid, p in data["people"].items():
        out.append({"person_id": pid, "display": p.get("display"),
                    "platforms": list(p.get("platforms", {}).keys()),
                    "aliases": p.get("aliases", [])})
    return {"count": len(out), "people": out}


def main():
    ap = argparse.ArgumentParser(description="member-persona registry + notes")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve")
    r.add_argument("--platform", required=True)
    r.add_argument("--user-id", required=True)
    r.add_argument("--display-name", default=None)
    r.add_argument("--group-id", default=None)
    r.add_argument("--no-fetch", action="store_true")
    r.set_defaults(func=cmd_resolve)

    l = sub.add_parser("link")
    l.add_argument("--person", required=True)
    l.add_argument("--platform", required=True)
    l.add_argument("--user-id", required=True)
    l.add_argument("--display-name", default=None)
    l.set_defaults(func=cmd_link)

    m = sub.add_parser("merge")
    m.add_argument("--into", required=True)
    m.add_argument("--from", required=True)
    m.set_defaults(func=cmd_merge)

    g = sub.add_parser("get-notes")
    g.add_argument("--person", required=True)
    g.set_defaults(func=cmd_get_notes)

    a = sub.add_parser("add-note")
    a.add_argument("--person", required=True)
    a.add_argument("--text", required=True)
    a.set_defaults(func=cmd_add_note)

    s = sub.add_parser("show")
    s.add_argument("--person", required=True)
    s.set_defaults(func=cmd_show)

    ls = sub.add_parser("list")
    ls.set_defaults(func=cmd_list)

    args = ap.parse_args()
    try:
        result = args.func(args)
    except Exception as e:  # never crash the caller; report as JSON
        result = {"error": f"{type(e).__name__}: {e}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
