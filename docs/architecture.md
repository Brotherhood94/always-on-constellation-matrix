# Architecture

Synapse owns room membership and Matrix delivery. PostgreSQL and Neo4j run on internal networks. Tailscale Serve provides private HTTPS to the Matrix client API, dashboard, and graph browser. A separate outbound OpenAI tunnel connects the Dot MCP bridge.

Each Office agent has a persistent Matrix device and a dedicated Constellation Instance that runs Amplifier. Its connector opens a scoped TLS ACP channel, maps each thread to a session, and tracks accepted assignments and delivery in SQLite. Credentials and the agent’s private workspace are deployment data. General Office workers also share the desktop. The Root execution broker is a separate host process gated by an operator vote on the exact proposal.

The operator-managed registry drives provisioning and role capabilities. The dashboard’s read-only catalog is derived from registry entries and observed service state. Manager owns managed task completion. Chief of Staff observes Office and publishes findings to its operator DM.

Agents submit scoped execution metadata to one Context Intelligence service. Context Intelligence attributes each write to the authenticated contributor. Neo4j stores the timeline relationships. Telegram and Root use separate workspace grants within the same service.

See [communication policies](../policy/README.md), [installation and recovery](runbook.md), and [acceptance checks](acceptance.md).
