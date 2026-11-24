FROM constellation-office-connector:0.22.17 AS tools
FROM constellation-amplifier-base:0.22.17
USER root
COPY --from=tools /usr/local/bin/node /usr/local/bin/node
COPY --from=tools /app /opt/workspace-tools
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils unzip && rm -rf /var/lib/apt/lists/*
COPY agents/engine/provider-requirements.txt /opt/office/provider-requirements.txt
RUN git init /opt/amplifier-cli \
    && git -C /opt/amplifier-cli fetch --depth 1 https://github.com/microsoft/amplifier-app-cli.git 77f303ee0458408b172ae4a9d326b2a21e2c420a \
    && git -C /opt/amplifier-cli checkout --detach FETCH_HEAD \
    && uv sync --locked --no-dev --project /opt/amplifier-cli --python /usr/local/bin/python3.13 --no-python-downloads \
    && for app in amplifier-cli amplifier-acp; do \
        uv pip install --no-deps --python "/opt/$app/.venv/bin/python" -r /opt/office/provider-requirements.txt \
        && uv pip check --python "/opt/$app/.venv/bin/python" || exit 1; \
    done \
    && rm -rf /root/.cache/uv
COPY agents/engine/initialize.py agents/engine/provider_setup.py agents/engine/prepare_runtime.py /opt/office/
RUN /opt/amplifier-acp/.venv/bin/python /opt/office/prepare_runtime.py
COPY --chmod=0755 agents/engine/start /usr/local/bin/office-start
USER 991:991
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/office-start"]
