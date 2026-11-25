# Architecture

Synapse owns room membership and Matrix delivery. PostgreSQL and Neo4j run on internal networks. Tailscale Serve provides private HTTPS to the Matrix client API, dashboard, and graph browser. A separate outbound OpenAI tunnel connects the Dot MCP bridge.

Each Office agent has a persistent Matrix device and a dedicated Constellation Instance that runs Amplifier. Its connector opens a scoped TLS ACP channel, maps each thread to a session, and tracks accepted assignments and delivery in SQLite. Credentials and the agent’s private workspace are deployment data. General Office workers also share the desktop. VPS Manager prepares maintenance plans. A separate systemd broker executes the exact approved action with host privileges after the operator votes in Root Operations.

The operator-managed registry drives provisioning and role capabilities. The dashboard’s read-only catalog is derived from registry entries and observed service state. Manager owns managed task completion. Chief of Staff observes Office and publishes findings to its operator DM.

Agents submit scoped execution metadata to one Context Intelligence service. Neo4j links each Office assignment to its handoffs, recipient acceptance, returns, and authoritative outcome. Each contributor writes under its own identity. VPS Manager writes sanitized outcome references in the Office graph and keeps the operation audit under a separate Root Operations grant. Telegram has its own grant.

See [communication policies](../policy/README.md), [installation and recovery](runbook.md), and [acceptance checks](acceptance.md).

## Logo sources

The diagram uses official service marks to identify the components of this independent project. The Element marks belong to Element Creations Ltd. The Matrix logo is a trademark of The Matrix.org Foundation. The PostgreSQL elephant is a trademark of the PostgreSQL Community Association. [Element](https://element.io/logo) · [Matrix.org](https://matrix.org/branding/) · [PostgreSQL](https://www.postgresql.org/about/press/presskit18/base/) · [Telegram](https://telegram.org/tour/screenshots) · [Tailscale](https://tailscale.com/press) · [Amplifier](https://github.com/microsoft/amplifier/tree/main/assets/branding)
