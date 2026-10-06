# DeepSeek-V4-Flash Vision-Exp（2 台構成）

レシピは `vendor/deepseek-v4-flash-dspark`（MiaAI-Lab、submodule・無改変）。
設定は submodule 内の `.env.dspark`（レシピの `.gitignore` で管理外）。

```bash
deepseek-v4-flash/start.sh   # 両ノードのモデルのページキャッシュを捨ててから、レシピの start を呼ぶ
deepseek-v4-flash/stop.sh    # 両ノード停止
```

- head = enda-spark（10.0.0.1）、worker = enda-gx10（10.0.0.2）。CX7 は両ノード port 0
  （`enp1s0f0np0` / `rocep1s0f0`）。
- API は `172.28.0.1:8888`（prism-gw からだけ届く。vLLM に API キーは無い）。
  prism-gw のモデル名は `deepseek-v4-flash`。
- **172.28.0.1:8888 を使う他のモデル（Flash-Next / GLM / 27B TP=2）とは排他。**

## `.env.dspark` の主な値（2026-10-07）

| 値 | 理由 |
|---|---|
| `GPU_MEMORY_UTILIZATION_TEXT=0.82` | 重みは 1 ランク 80.0 GiB。KV は小さい方のノード（gx10、総 119.6 GiB）で決まる。0.74 では gx10 の KV が 3.96 GiB で 512k にも足りなかった。0.82 で KV 約 1.8M トークン、spark の空きは約 10〜20 GiB |
| `MAX_MODEL_LEN=1048576` | 上の KV で 1M が 1.7 本入る |
| `DEFAULT_THINKING=high` | DeepSeek V4 は low / high / max の 3 段で medium は無い |
| `VLLM_HOST=172.28.0.1` | レシピ既定の 0.0.0.0 だと認証なしの API が LAN / tailscale に出る |

## 起動が固まるとき

worker（gx10）が重みのロード中に GPU 78,100 MiB で止まり、ログが進まなくなることがあった
（2026-10-06〜07 に 3 回。util 0.80 でも 0.82 でも起きた）。どの回も、gx10 のページキャッシュに
他のモデルのファイルが数十 GiB 載っていた。`drop-model-cache.py`（`posix_fadvise` で sudo 不要）で
キャッシュを捨ててから起動すると、毎回通った。`start.sh` はこれを両ノードで先に実行する。

- 止める → すぐ起動する、でも固まったことがある。`stop.sh` のあと両ノードの `MemAvailable` が
  戻ってから起動する。
- 固まったら `stop.sh` で止めて `start.sh` からやり直す（コンテナは `unless-stopped` で
  再起動を繰り返すので、放置しない）。
