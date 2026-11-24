FROM constellation-office-connector:0.22.17 AS tools
FROM constellation-amplifier-base:0.22.17
USER root
COPY --from=tools /usr/local/bin/node /usr/local/bin/node
COPY --from=tools /app /opt/workspace-tools
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils unzip && rm -rf /var/lib/apt/lists/*
# The pinned backend reads MCP config from workspace files by default. Use only
# the operator-generated bundle so a shared file cannot register an executable.
RUN python3 -c 'from pathlib import Path; p=Path("/opt/amplifier-acp/.venv/lib/python3.13/site-packages/amplifier_module_tool_mcp/config.py"); s=p.read_text(); a=s.index("        for config in ["); b=s.index("        ]:",a); p.write_text(s[:a]+"        for config in [self._load_from_inline()"+s[b:])'
COPY agents/engine/initialize.py /opt/office/initialize.py
COPY --chmod=0755 agents/engine/start /usr/local/bin/office-start
USER 991:991
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/office-start"]
