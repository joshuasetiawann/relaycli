# RelayCLI — batteries-included agent image.
# git + ripgrep power the native tools; node/npx powers the MCP presets.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Non-root: the agent edits files and runs commands — keep it unprivileged.
RUN useradd --create-home --shell /bin/bash relay

WORKDIR /opt/relaycli
COPY pyproject.toml README.md ./
COPY relaycli ./relaycli
RUN pip install --no-cache-dir .

# Pre-create the mount points owned by the agent user — VOLUME/WORKDIR would
# otherwise create them as root and the agent could not write config/memory.
RUN mkdir -p /home/relay/.relaycli /workspace \
    && chown relay:relay /home/relay/.relaycli /workspace

USER relay
# The project you want the agent to work on mounts here.
WORKDIR /workspace
# Config, memory, and history persist in a volume at ~/.relaycli.
VOLUME ["/home/relay/.relaycli"]

ENTRYPOINT ["relaycli"]
