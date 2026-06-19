# artifacts/

Large build artifacts. **Everything here except this README is gitignored.**

## docker-images/

Saved Docker images for the `vllm-qwen35` service (the SM121 base + the v2 layer),
so a working build survives even after the PyTorch cu130 nightly wheels expire from
the index. Built with torch 2.13 (avoids the C++20 break that torch 2.14 hits against
vLLM 0.19.0). See [`docs/qwen35-vllm.md`](../docs/qwen35-vllm.md).

Contents do **not** include model weights (mounted at runtime from `~/models`).

| file | images |
|---|---|
| `vllm-sm121-qwen35-v2-20260620.tar.gz` (~8.7 GB) | `vllm-sm121:latest` + `vllm-qwen35-v2:latest` |

Restore:
```bash
gunzip -c artifacts/docker-images/vllm-sm121-qwen35-v2-20260620.tar.gz | docker load
```

Re-save after a rebuild:
```bash
docker save vllm-sm121:latest vllm-qwen35-v2:latest | gzip > artifacts/docker-images/vllm-sm121-qwen35-v2-<date>.tar.gz
```
