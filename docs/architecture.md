# Architecture

Synapse owns room membership and Matrix delivery. PostgreSQL and Neo4j run on internal networks. Tailscale Serve provides private HTTPS to the Matrix client API, dashboard, and graph browser. A separate outbound OpenAI tunnel connects the Dot MCP bridge.

Each Office agent has a persistent Matrix device and a dedicated Constellation Instance that runs Amplifier. Its connector opens a scoped TLS ACP channel, maps each thread to a session, and tracks accepted assignments and delivery in SQLite. Credentials and the agent’s private workspace are deployment data. General Office workers also share the desktop. The Architect prepares maintenance plans. A broker in the separate Docker control runtime verifies the Matrix approval and executes the matching scoped action. The Architect’s own containers have no Docker socket.

The operator-managed registry drives provisioning and role capabilities. The dashboard’s read-only catalog is derived from registry entries and observed service state. Task Manager owns coordinated tasks. Hiring Manager, Coach, and Scouter read Office evidence under distinct contributor identities. Each has a private operator chat. The host broker checks the actor and exact operation before changing the registry or Persona Workshop. The dashboard publishes a profile for every registered agent. [Starter team](team.md)

When Telegram is enabled, its dedicated agent joins Office and operates the separate Telegram Space. The operator may mention it directly or ask Task Manager to hand off through the scoped Telegram connector. Telegram Agent replies in the original Office thread. A request addressed privately in Telegram Bridge receives its result there. The dashboard displays the specialist and its integration-managed route using the Telegram service snapshot. [Telegram integration](telegram.md#use-telegram-from-office)

Agents submit scoped execution metadata to one Context Intelligence service. Neo4j links each Office assignment to its handoffs, recipient acceptance, returns, and authoritative outcome. Each contributor writes under its own identity. The Architect writes sanitized outcome references in the Office graph and keeps the operation audit under a separate Control Room grant. Telegram has its own grant.

The three management agents can read bounded Office reports for handoffs, outcomes and target checks. Their contributor credentials are scoped to the Office workspace. Neo4j Browser remains the operator interface for exploring the wider graph.

See [communication policies](../policy/README.md), [installation and recovery](runbook.md), and [acceptance checks](acceptance.md).

## Logo sources

The diagram uses official service marks to identify the components of this independent project. The Element marks belong to Element Creations Ltd. The Matrix logo is a trademark of The Matrix.org Foundation. The PostgreSQL elephant is a trademark of the PostgreSQL Community Association. [Element](https://element.io/logo) · [Matrix.org](https://matrix.org/branding/) · [PostgreSQL](https://www.postgresql.org/about/press/presskit18/base/) · [Telegram](https://telegram.org/tour/screenshots) · [Tailscale](https://tailscale.com/press) · [Amplifier](https://github.com/microsoft/amplifier/tree/main/assets/branding)
