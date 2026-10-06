#!/usr/bin/env python3
"""Drop the page cache of model files (no sudo): posix_fadvise(DONTNEED) on every
file under the given roots (default: ~/.cache/huggingface/hub and ~/models).

Why: on GB10 the DeepSeek-V4-Flash worker (enda-gx10) wedged at 78,100 MiB during
weight load whenever tens of GiB of other models' files sat in the page cache
(2026-10-06/07, 3 hangs at util 0.80 and 0.82). After dropping that cache the same
launch came up every time. See README.md.
"""
import os, sys
roots = sys.argv[1:] or ["~/.cache/huggingface/hub", "~/models"]
n = 0
for root in roots:
    for dp, _, fn in os.walk(os.path.expanduser(root)):
        for f in fn:
            p = os.path.join(dp, f)
            if os.path.islink(p):
                continue
            try:
                fd = os.open(p, os.O_RDONLY)
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                os.close(fd)
                n += 1
            except OSError:
                pass
print(f"drop-model-cache: {n} files")
