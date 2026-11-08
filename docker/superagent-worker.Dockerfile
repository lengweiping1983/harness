# 显式构建时必须提供经操作者核对的 Debian 系 Python 镜像摘要。
ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/harness
COPY pyproject.toml build_support.py README.md ./
COPY harness ./harness
RUN python -m pip install --no-cache-dir .
# 实际入口、身份、挂载和配额由 Sandbox Runner 固定，镜像不携带任何凭据。
ENTRYPOINT ["python", "-m", "harness.superagent_worker", "--stdio"]
