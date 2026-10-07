# Qwen3.8-Flash-Next 2 台構成 (vLLM TP2+EP+MTP3)

レシピは submodule `vendor/qwen38-flash-next-dual`（MiaAI-Lab Qwen3.8-Flash-Next-Dual-DGX-Sparks）。
1 台構成（compose の `vllm-qwen38-fn`、`docs/qwen3.8-flash-next-tp2.md`）とは別物で、
**チェックポイントも違う**（こちらは `nvidia/Qwen3.8-Flash-Next-NVFP4` 123.6 GiB、1 台構成は Mia-AiLab 版）。

- head = enda-spark（rank 0 + API）、worker = enda-gx10（rank 1, `ken@10.0.0.2`）。CX7 port 0 で直結
- API: `http://172.28.0.1:8888/v1`、モデル ID **`qwen3.8-flash-next`**（レシピ既定のまま）
- **1 台構成と同じ ID** なので、prism-gw では既存の `qwen3.8-flash-next` ルートにそのまま出る（別ルートは作っていない）
- コンテナ名は両ノードとも `vllm-fn`（compose の `vllm-qwen38-fn` とは別名）。**ポート 8888 は排他**:
  `vllm-qwen38-fn` / DeepSeek / GLM が動いていたら先に止める（`start.sh` はポートが埋まっていると止まる）

## 起動・停止

```bash
qwen38-flash-next-dual/start.sh            # = レシピの ./start.sh --no-download (bind だけ差し替え)
qwen38-flash-next-dual/start.sh --launch   # 重みの確認・rsync を飛ばして起動だけ
DRY_RUN=1 qwen38-flash-next-dual/start.sh  # レシピの start.sh との差分を表示するだけ
qwen38-flash-next-dual/stop.sh             # docker stop (head → worker) してからレシピの stop.sh

docker logs -f vllm-fn                     # head (API)
ssh ken@10.0.0.2 docker logs -f vllm-fn    # worker
```

起動は 10 分前後。`start.sh` は head の `/health` が 200 になるまで待つ（タイムアウトは無い。
コンテナが落ちれば止まる）。

**bind の差し替え。** レシピは head を `--host 0.0.0.0` 決め打ちで起動し、変える knob が無い
（`EXTRA_VLLM_ARGS` は `--host 0.0.0.0` より前に入るので負ける）。wrapper は `start.sh` を /tmp に写し、
SCRIPT_DIR・`--host`・起動待ちの `/health` の URL の 3 か所だけを `172.28.0.1` に置き換えて実行する。
どれかの形が変わっていれば（レシピ更新時）置き換えずに止まる。submodule には書かない。

## 設定（`vendor/qwen38-flash-next-dual/.env`）

`.env` は submodule の `.gitignore` で管理外（1 台構成の `vendor/qwen38-flash-next/.env` と同じ扱い）。
`.env.sample` をそのまま写し、次だけ変えた。消えたら `cp .env.sample .env` してこの表どおりに直す。

| 変数 | 値（既定） | 理由 |
|---|---|---|
| `WORKER_USER` | `ken`（空） | |
| `IB_GID_INDEX` | `5`（3） | 両ノードとも RoCE v2 IPv4 の GID は 5。`HEAD_IP` / `WORKER_IP` / `IFACE` / `IB_HCA` は既定（10.0.0.1 / 10.0.0.2 / `enp1s0f0np0` / `=rocep1s0f0`）がそのまま合う |
| `GPU_MEMORY_UTILIZATION` | `0.67`（0.80） | CHANGELOG 2026-09-26: 0.835 で配信中の MemAvailable 0.3〜0.9 GiB、0.80 で head 4.5 GiB。0.01 ≈ 1.1 GiB なので 0.67 で head ~19〜20 GiB 残る見込み（vision 用）。KV は 1 ノード ~13 GiB、bf16 で ~85 万トークン（262k の ~3.3 倍） |
| `KV_CACHE_DTYPE` | `auto` = bf16（fp8） | 1 台構成と同じ判断（容量より品質。fp8 は疎な attention の選択を揺らす）。容量が要れば `fp8` に戻すだけ（~1.7 倍） |
| `MTP_DRAFT_VOCAB` | `/home/ken/src/github.com/prism-hu/chat/qwen38-flash-next/draft_vocab_ja_en_code_65k.txt`（en_code_47k） | ここで作った日本語入り語彙。絶対パスはそのまま使われ、head は bind-mount、worker へは scp されて `/etc/vllm-draft-vocab.txt` に入る |
| `REQUIRE_IDLE_GPU` | `false`（true） | technoplasm の `compute.jobs` が spark の GPU に常駐していて、true だと起動前に止まる |
| `EXTRA_VLLM_ARGS` | `--chat-template /root/.cache/vllm/prism-chat-template.jinja --tool-call-parser qwen3_xml` | 1 台構成と同じ froggeric v22.5 テンプレート（既定 `reasoning_effort` が medium。同梱のは xhigh で思考が終わらない）。テンプレートは XML の tool call を出すので parser も qwen3_xml に（後勝ち）。テンプレートの実体は `vendor/qwen38-flash-next/files/chat-template/chat_template.jinja` で、wrapper が起動ごとに両ノードの `~/.cache/vllm/` に置く（レシピが `/root/.cache/vllm` にマウントする唯一のホスト側ディレクトリ） |

そのまま使っている主な既定: `MODEL_ID=nvidia/Qwen3.8-Flash-Next-NVFP4`、`SERVED_MODEL_NAME=qwen3.8-flash-next`、
`MAX_MODEL_LEN=262144`、`MAX_NUM_SEQS=8`、`MTP_NUM_SPECULATIVE_TOKENS=3`、`NFS_SHARE=false`（worker は自前のコピー）、
`EVICT_PAGE_CACHE=true`、`PORT=8888`。

**リビジョンのピン。** レシピには revision の設定が無い（`download.sh` は常に main を取る）。vLLM は
`MODEL_ID` を `HF_HUB_OFFLINE=1` で `refs/main` から解決するので、wrapper は
(1) `download.sh` を常に飛ばし（`--no-download`）、(2) 両ノードで `snapshots/fc694b54fb0174e0913e6adf86691ef85a4ead47`
があり `refs/main` がそれを指すことを確かめる（`refs/main` が無ければ書く。別の sha なら止まる）。

## 重みとイメージ

2026-10-07 に両ノードへ配置済み（124 GiB、sha256 照合済み）。入れ直すときは、大学の回線だと `hf download` は
大きいファイルを取り切れない（失敗のたびにやり直す）ので、再開できる `scripts/hf-resumable.py` を使う:

```bash
python3 scripts/hf-resumable.py nvidia/Qwen3.8-Flash-Next-NVFP4 fc694b54fb0174e0913e6adf86691ef85a4ead47
```

gx10 に要るもの: `~/.cache/huggingface/hub/models--nvidia--Qwen3.8-Flash-Next-NVFP4/`
（`blobs/`、`snapshots/fc694b54…/`、`refs/main` = そのピン）とイメージ `vllm/vllm-openai:qwen38-flash-next`。
レシピは worker の snapshot を `files/resolve_snapshot.py`（index が名指す shard が全部あるか）で見て、
揃っていれば rsync を飛ばす。揃っていなければ head の repo ディレクトリを丸ごと `rsync -av` する。
イメージは `.Id` を比べ、違えば worker で `docker pull` する。

```bash
H=$HOME/.cache/huggingface/hub
W=ken@10.0.0.2
SSH='ssh -c aes128-gcm@openssh.com -o Compression=no'
d=models--nvidia--Qwen3.8-Flash-Next-NVFP4; r=fc694b54fb0174e0913e6adf86691ef85a4ead47
ssh $W "mkdir -p .cache/huggingface/hub/$d/blobs .cache/huggingface/hub/$d/snapshots .cache/huggingface/hub/$d/refs"
(cd $H/$d && find snapshots/$r -type l -printf '%l\n' | sed 's#^\(\.\./\)*##' | sort -u) |
  xargs -P8 -I{} rsync -a -L --partial -e "$SSH" $H/$d/{} $W:.cache/huggingface/hub/$d/{}
rsync -a -e "$SSH" $H/$d/snapshots/$r $W:.cache/huggingface/hub/$d/snapshots/
ssh $W "printf %s $r > .cache/huggingface/hub/$d/refs/main"
ssh $W python3 - .cache/huggingface/hub/$d < vendor/qwen38-flash-next-dual/files/resolve_snapshot.py  # sha が出て exit 0 なら OK

docker save vllm/vllm-openai:qwen38-flash-next | ssh ken@10.0.0.2 docker load
```

（最後の `resolve_snapshot.py` はリポジトリのルートから実行する。）

## 注意

- 2 ノードの既知の失敗モード（ホストロック、長い prefill での decode 停止など）は
  `docs/qwen3.8-flash-next-tp2.md` を参照。メンテナンス枠で試すこと。
- `.env` は「環境変数より .env が勝つ」（ABLIT / HF_TOKEN 以外）。一時的に値を変えるなら `.env` を直す。
