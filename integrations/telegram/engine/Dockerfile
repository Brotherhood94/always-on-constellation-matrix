FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM node:24.22.3-trixie-slim AS mcp-tools
WORKDIR /opt/telegram-tools
COPY agents/connector/package.json agents/connector/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY integrations/telegram/engine/telegram-mcp.mjs agents/connector/telegram-office.mjs ./
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS amplifier-base
ARG TARGETARCH
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git libpam0g \
    && rm -rf /var/lib/apt/lists/*
RUN case "$TARGETARCH" in \
        amd64) arch=x86_64; checksum=be92610035fc3fdc6e9064f265fc9c753286d5e6c8b3a8e4f23c315bff0c36e4 ;; \
        arm64) arch=aarch64; checksum=748be5b4528c5173e7a0e6936c8f14e01b4ef6783cfd7e4f9941d7ce3c74bc90 ;; \
        *) exit 1 ;; \
    esac \
    && tarball="agent-instance-${arch}-unknown-linux-gnu.tar.gz" \
    && curl -fsSL "https://github.com/microsoft/amplifier-constellation-instance/releases/download/v0.22.17/${tarball}" -o /tmp/instance.tgz \
    && echo "$checksum  /tmp/instance.tgz" | sha256sum -c - \
    && mkdir /tmp/instance \
    && tar -xzf /tmp/instance.tgz -C /tmp/instance \
    && exe=$(find /tmp/instance -type f -name agent-instance) \
    && install -m 0755 "$exe" /usr/local/bin/agent-instance \
    && rm -rf /tmp/instance /tmp/instance.tgz \
    && agent-instance --version
RUN git init /opt/amplifier-acp \
    && git -C /opt/amplifier-acp fetch --depth 1 https://github.com/microsoft/amplifier-app-acp.git 3da5681942d9e9b4c51376e47ec85e69a001623a \
    && git -C /opt/amplifier-acp checkout --detach FETCH_HEAD \
    && uv sync --locked --project /opt/amplifier-acp --python /usr/local/bin/python3.13 --no-python-downloads
RUN uv pip install --no-deps --python /opt/amplifier-acp/.venv/bin/python \
        'amplifier-module-provider-openai-chatgpt @ git+https://github.com/microsoft/amplifier-module-provider-openai-chatgpt.git@cae3eaa32e16b139b8e889b8cc477c3f2d9dee31' \
    && uv pip check --python /opt/amplifier-acp/.venv/bin/python \
    && rm -rf /root/.cache/uv
RUN groupadd -g 991 telegram && useradd -M -u 991 -g 991 -d /instance telegram
COPY --chmod=0755 integrations/telegram/engine/start integrations/telegram/engine/backend /usr/local/bin/
COPY agents/engine/amplifier_module_provider_constellation_hiring /opt/amplifier-acp/.venv/lib/python3.13/site-packages/amplifier_module_provider_constellation_hiring
COPY integrations/telegram/engine/initialize.py integrations/telegram/engine/sign_in.py /opt/telegram/
ENV HOME=/instance AGENT_INSTANCE_CERTIFICATE_NAMES='["telegram-engine"]' \
    TELEGRAM_CODEX_TOKEN_FILE=/instance/backend/codex-oauth.json
USER 991:991
ENTRYPOINT ["/usr/local/bin/start"]

FROM amplifier-base
COPY --from=mcp-tools /usr/local/bin/node /usr/local/bin/node
COPY --from=mcp-tools /opt/telegram-tools /opt/telegram-tools
