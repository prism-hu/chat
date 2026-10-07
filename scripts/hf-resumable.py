#!/usr/bin/env python3
"""Resumable HF snapshot download into the standard hub cache layout.

The `hf` CLI and huggingface_hub here restart a failed file under a fresh random
temp name, so on the university link (TLS errors every 1-2 min) 9-50 GiB files never
finish. This uses curl -C - on a stable .part per blob, retrying forever, then
verifies sha256 (LFS) / size and links snapshots/<rev>/<path> -> blobs/<oid>.
usage: hf-resumable.py <repo_id> <revision> [hub_dir]
"""
import hashlib, json, os, subprocess, sys, time, urllib.request
repo, rev = sys.argv[1], sys.argv[2]
hub = sys.argv[3] if len(sys.argv) > 3 else os.path.expanduser("~/.cache/huggingface/hub")
root = os.path.join(hub, "models--" + repo.replace("/", "--"))
blobs, snap = os.path.join(root, "blobs"), os.path.join(root, "snapshots", rev)
os.makedirs(blobs, exist_ok=True); os.makedirs(snap, exist_ok=True)
tok = None
for p in (os.path.expanduser("~/.cache/huggingface/token"),):
    if os.path.exists(p): tok = open(p).read().strip()
hdr = ["-H", f"Authorization: Bearer {tok}"] if tok else []

def api(url):
    for i in range(100):
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"} if tok else {})
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as e:
            print("api retry", e, flush=True); time.sleep(5)
    raise SystemExit("api failed")

tree = [e for e in api(f"https://huggingface.co/api/models/{repo}/tree/{rev}?recursive=1") if e["type"] == "file"]
tree.sort(key=lambda e: e["size"])
for e in tree:
    path, size = e["path"], e["size"]
    lfs = e.get("lfs")
    oid = lfs["oid"] if lfs else e["oid"]
    blob = os.path.join(blobs, oid)
    if not (os.path.exists(blob) and os.path.getsize(blob) == size):
        part = blob + ".part"
        url = f"https://huggingface.co/{repo}/resolve/{rev}/{path}"
        while True:
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have == size: break
            if have > size: os.remove(part); continue
            print(f"{time.strftime('%T')} {path}: {have/2**30:.2f}/{size/2**30:.2f} GiB", flush=True)
            subprocess.run(["curl", "-sS", "-L", "--fail", "-C", "-", "--connect-timeout", "30",
                            "--speed-time", "60", "--speed-limit", "1024", *hdr, "-o", part, url])
            time.sleep(2)
        if lfs:
            h = hashlib.sha256()
            with open(part, "rb") as f:
                for b in iter(lambda: f.read(1 << 24), b""): h.update(b)
            if h.hexdigest() != oid:
                print(f"SHA MISMATCH {path}, redownloading", flush=True); os.remove(part); continue  # noqa
        os.replace(part, blob)
        print(f"{time.strftime('%T')} done {path}", flush=True)
    link = os.path.join(snap, path)
    os.makedirs(os.path.dirname(link), exist_ok=True)
    rel = os.path.relpath(blob, os.path.dirname(link))
    if os.path.islink(link) or os.path.exists(link): os.remove(link)
    os.symlink(rel, link)
os.makedirs(os.path.join(root, "refs"), exist_ok=True)
open(os.path.join(root, "refs", "main"), "w").write(rev)
missing = [e["path"] for e in tree if not os.path.exists(os.path.join(snap, e["path"]))]
print("ALL DONE" if not missing else f"MISSING {missing}", flush=True)
