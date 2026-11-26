FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git && rm -rf /var/lib/apt/lists/*
RUN git init /app && git -C /app fetch --depth 1 https://github.com/microsoft/amplifier-context-intelligence.git 1622b48b05fcd93f935e5d55b962081a2c0cb229 && git -C /app checkout --detach FETCH_HEAD
WORKDIR /app
RUN uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python3.13 --no-python-downloads

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY --from=build /app/.venv /app/.venv
RUN groupadd --gid 991 context && useradd --uid 991 --gid 991 --no-create-home context && install -d -m 0700 -o 991 -g 991 /data /data/queues /data/blobs /data/identity
USER 991:991
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/app/.venv/bin/context-intelligence-server"]

COPY --chmod=0644 integrations/context-execution/engine/main.py integrations/context-execution/engine/neo4j_store.py integrations/context-execution/engine/execution_timeline.py integrations/context-execution/engine/office_topology.py /app/.venv/lib/python3.13/site-packages/context_intelligence_server/
COPY --chmod=0644 integrations/context-execution/engine/handlers/data_layer_1/default.py /app/.venv/lib/python3.13/site-packages/context_intelligence_server/handlers/data_layer_1/default.py
