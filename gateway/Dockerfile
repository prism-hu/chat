# prism-gw. 依存は httpx / starlette / uvicorn / pyyaml だけなので薄い。
# 依存解決は uv (lock があればそれを使い、無ければその場で解決する)。
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

# 依存だけ先に入れてレイヤを効かせる (app.py の変更で再解決しない)
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY app.py ./
# config.yaml は compose でマウントする (編集して再起動するだけで反映される)。
# イメージにも既定値として焼いておくので、単体 docker run でも動く。
COPY config.yaml ./

EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:4000/health').read()" || exit 1

CMD ["python", "app.py"]
