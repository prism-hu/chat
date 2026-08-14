# syntax=docker/dockerfile:1.7
#
# llama.cpp (CUDA / GB10 SM121) — Step-3.7-Flash 用のサーバイメージ。
#
# ベースは https://github.com/stevibe/step37-flash-dgx-spark (MIT) の Dockerfile。
# 取り込みにあたって変えたのは以下:
#   - モデルの自動ダウンロード用 entrypoint を落とした
#     (このリポジトリはモデルを host の ${MODELS_DIR} に置く方式。vllm-qwen35 と同じ)
#   - 起動フラグは entrypoint に埋めず docker-compose.yml の command に出した
#   - よって huggingface_hub / jq などランタイム用の追加パッケージも不要
#
# なぜ llama.cpp なのか: Step-3.7-Flash は 198B MoE で、vLLM 向けの最小量子化
# (NVFP4) でも weights 124GB。128GB 統合メモリに載らない。GGUF IQ4_XS (105GB)
# が唯一の道。詳細は docs/step37-llamacpp.md。

FROM nvidia/cuda:13.1.1-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH=/usr/local/cuda/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ cmake ninja-build git \
    ca-certificates libgomp1 libssl-dev curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

# 既定は上流 ggml-org/master。Step3.7 対応は PR #23845 (2026-06-02) でマージ済みで、
# 本体・vision・tool call とも問題なく動く。StepFun の fork (step3.7 ブランチ) でも
# 本体は動くが、MTP のドラフト GGUF が読めない (docs/step37-llamacpp.md)。
# fork に戻すときは:
#   docker compose build --build-arg LLAMACPP_REPO=https://github.com/stepfun-ai/llama.cpp.git \
#                        --build-arg LLAMACPP_REF=step3.7 llamacpp-step37
ARG LLAMACPP_REPO=https://github.com/ggml-org/llama.cpp.git
ARG LLAMACPP_REF=master
RUN git clone --depth 1 --branch "${LLAMACPP_REF}" "${LLAMACPP_REPO}" llama.cpp \
 || (git clone "${LLAMACPP_REPO}" llama.cpp && cd llama.cpp && git checkout "${LLAMACPP_REF}")

WORKDIR /opt/llama.cpp

# 121 = GB10 (DGX Spark)。他アーキ向けにビルドしないので nvcc は 1 パスで済む。
ARG CMAKE_CUDA_ARCHITECTURES=121
# nvcc は 1 ジョブあたり数 GB 食う。vllm-qwen35 を上げたままビルドするとホストの
# RAM が足りない (122B が ~100GB 保持している)。増やすなら先に qwen を止めること。
ARG BUILD_JOBS=2

# ビルド時には driver (libcuda.so) が無いので stub とリンクする。実行時は
# NVIDIA Container Toolkit が本物を注入する。
RUN test -f /usr/local/cuda/lib64/stubs/libcuda.so

RUN cmake -S . -B build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=${CMAKE_CUDA_ARCHITECTURES} \
      -DCMAKE_C_COMPILER=gcc \
      -DCMAKE_CXX_COMPILER=g++ \
      -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc \
      -DLLAMA_CURL=OFF \
      -DLLAMA_BUILD_SERVER=ON \
      -DLLAMA_BUILD_TOOLS=ON \
      -DCMAKE_EXE_LINKER_FLAGS="-L/usr/local/cuda/lib64/stubs -lcuda -Wl,-rpath-link,/usr/local/cuda/lib64/stubs -Wl,--allow-shlib-undefined" \
      -DCMAKE_SHARED_LINKER_FLAGS="-L/usr/local/cuda/lib64/stubs -lcuda -Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
 && cmake --build build --target llama-server llama-cli -j"${BUILD_JOBS}"

# devel イメージのまま (multi-stage にしていない) ので ~10GB ある。ビルド成果物と
# CUDA ランタイムを別ステージに移せば縮むが、まずは動かすことを優先。
ENV PATH=/opt/llama.cpp/build/bin:${PATH}

EXPOSE 8000
ENTRYPOINT ["/opt/llama.cpp/build/bin/llama-server"]
