# Private Matrix and Constellation VPS template

Recreate a private Matrix service for Element and always-on agents. A Linux VPS hosts Synapse, PostgreSQL, the resource dashboard, and four agents. You can add a worker on a second host and connect Dot to assign work from ChatGPT.

| Guide | What it covers |
| --- | --- |
| [Architecture](docs/architecture.md) | Network diagram, agent roles, shared policies, and a managed task from assignment to closure |
| [Runbook](docs/runbook.md) | Source requirements, install order, Element onboarding, Dot, monitoring, and recovery |
| [Acceptance](docs/acceptance.md) | Observed results and checks to repeat on a new deployment |

Start with the [source and deployment prerequisites](docs/runbook.md#prerequisites). This repository supplies the guides. Obtain the matching installer sources and any optional deployment bundles through your authorized source distribution.

## Planned work

- Give each agent a role-specific persona. Define its working style, decision criteria, and tone alongside the shared Matrix policies.
- Design a Chief of Staff that reviews authorized threads and proposes improvements in a separate room.
- Automate new-agent provisioning so registration, default policies, dashboard inventory, and backups are configured together.
