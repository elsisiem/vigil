# Vigil: Python 3.12 for the swarm, Node 22 for the One CLI.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 NODE_VERSION=22.23.4

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates xz-utils git \
    && rm -rf /var/lib/apt/lists/*

# Node 22 (the One CLI refuses older runtimes), then the One CLI itself.
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 && rm /tmp/node.tar.xz \
    && npm install -g @withone/cli@2.10.0 && node --version && one --version

WORKDIR /app
COPY pyproject.toml README.md ./
COPY vigil ./vigil
COPY fixtures ./fixtures
COPY recordings ./recordings
RUN pip install -e . && mkdir -p cache

ENV VIGIL_HOST=0.0.0.0 VIGIL_PORT=8080 VIGIL_DRY_RUN=1
EXPOSE 8080

# Authorise the One CLI from the secret, then serve.
CMD ["sh", "-c", "if [ -n \"$ONE_SECRET\" ]; then one init --auth manual --api-key \"$ONE_SECRET\" >/dev/null 2>&1 || true; fi; exec python -m vigil serve --host 0.0.0.0 --port 8080"]
