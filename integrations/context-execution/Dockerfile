FROM matrix-context-intelligence:6.13.0-1622b48
COPY --chmod=0644 engine/main.py engine/neo4j_store.py engine/execution_timeline.py /app/.venv/lib/python3.13/site-packages/context_intelligence_server/
COPY --chmod=0644 engine/handlers/data_layer_1/default.py /app/.venv/lib/python3.13/site-packages/context_intelligence_server/handlers/data_layer_1/default.py
