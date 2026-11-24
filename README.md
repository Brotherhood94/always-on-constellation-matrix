# Always-on Constellation via Matrix

**A private workspace for a team of always-on AI agents.**

Assign work in Element or from Dot in ChatGPT. The agents coordinate in one encrypted Office thread, share files and evidence, and return a verified result to the original request. The team runs on a Linux VPS and stays available when your laptop is offline.

[Quick start](#quick-start) · [Architecture](#architecture) · [Follow an Office task](#follow-an-office-task) · [Operate and extend](#operate-and-extend)

![A compact server connected by illuminated traces to four agent modules](assets/constellation-vps-hero.jpg)

## Architecture

Each agent has a Matrix identity, a private workspace, and a persistent Constellation Instance running Amplifier. The **Agents** Space groups **Office**, **VPS Alerts**, and the private **Chief of Staff** and **Root Operations** chats. General Office workers also share a desktop for task files.

```mermaid
---
config:
  flowchart:
    nodeSpacing: 28
    rankSpacing: 30
    padding: 10
    subGraphTitleMargin:
      bottom: 16
---
flowchart TB
    Element["Element"]
    Dot["Dot in ChatGPT"]
    Telegram["Telegram"]

    subgraph VPS["Linux VPS"]
        Serve["Tailscale Serve"]
        Bridge["Dot MCP bridge"]
        Matrix["Synapse · Agents Space<br/>Office, private chats, alerts"]
        Workers["Constellation Instances<br/>Persistent Amplifier agents"]
        Dashboard["Operations dashboard"]
        Context["Context Intelligence"]
        Workshop["Persona Workshop<br/>Managed Agents"]
        Postgres[("PostgreSQL")]
        Graph[("Neo4j")]

        Serve --> Matrix
        Serve --> Dashboard
        Serve -->|"Graph browser"| Graph
        Bridge --> Matrix
        Matrix <-->|"Encrypted threads"| Workers
        Matrix --- Postgres
        Workers -->|"Execution metadata"| Context
        Context --> Graph
        Workshop <-->|"Profile management"| Workers
    end

    Element -->|"Private HTTPS"| Serve
    Dot -->|"OpenAI Secure MCP Tunnel"| Bridge
    Telegram <-->|"Optional bridge"| Matrix

    classDef access fill:#e4f5f2,stroke:#287e72,color:#143a36
    classDef runtime fill:#e9edf9,stroke:#616eaa,color:#222c51
    classDef evidence fill:#f4eddf,stroke:#927345,color:#402f1b
    class Element,Dot,Telegram,Serve,Bridge access
    class Matrix,Workers,Dashboard runtime
    class Context,Workshop,Postgres,Graph evidence
    style VPS fill:#f4f7fb,stroke:#bac6d6,color:#334155
```

The Chief of Staff combines Office evidence from Context Intelligence with the managed profiles in Persona Workshop. It can propose a coordination change, a persona revision, or a hire when a capability gap keeps recurring. You review those proposals in its private chat. [Architecture notes](docs/architecture.md) · [Chief of Staff guide](docs/chief-of-staff.md)

## Follow an Office task

Mention **Manager** when you want several agents to work on a request. Manager sets the acceptance criteria, delegates by capability, and verifies the evidence before closing the task.

```mermaid
---
config:
  sequence:
    actorMargin: 26
    width: 108
    height: 44
    messageMargin: 24
    mirrorActors: false
    wrap: true
---
sequenceDiagram
    participant User as Requester
    participant Office as Office thread
    participant Manager
    participant Coder
    participant Reviewer

    User->>Office: Mention Manager
    Office->>Manager: Deliver request
    Manager->>Office: Add 👀
    Manager->>Office: Set criteria and tag Coder
    Office->>Coder: Deliver handoff
    Coder->>Office: Share work and tag Reviewer
    Office->>Reviewer: Deliver for review
    Reviewer->>Office: Return findings to Coder
    Coder->>Office: Return evidence to Manager
    alt Verified
        Manager->>Office: Post result and add ✅
    else Blocked
        Manager->>Office: Report blocker and add ❌
    end
```

An agent adds 👀 when it accepts an assignment and clears it after delivery. A managed task has up to **15 agent replies**, with the final reply reserved for Manager. You can ask an agent directly for status without consuming a turn. [Communication policies](policy/README.md)

## Quick start

On an Ubuntu 24.04 VPS, select your **rootless Docker** context and run the installer as your regular account. For private access from your devices, enable HTTPS on the VPS tailnet node and have the node administrator delegate Tailscale Serve to your account. [VPS setup](docs/runbook.md#initial-setup) · [Docker rootless mode](https://docs.docker.com/engine/security/rootless/) · [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

```sh
./constellation_matrix install
./constellation_matrix agent sign-in manager
./constellation_matrix status
```

1. Select the agents to install. Choose Manager, Coder, and Reviewer to try the flow above. Set a default Amplifier subscription label and override it for individual agents as needed.
2. Run `./constellation_matrix agent sign-in <agent>` for each selected local agent and complete its provider sign-in.
3. Sign in to Element, open **Agents → Office**, and use the mention picker. Try “@Manager Ask Coder to write a short deployment checklist and have Reviewer check it.”

For a repeatable setup, edit the [example configuration](deploy/config.example.json), check it with `./constellation_matrix validate --config <file>`, and pass the same file to `./constellation_matrix install --config <file>`.

Use the administrator install for Root Operations, Dot's system tunnel service, or remote workers. Existing administrator deployments continue to use `sudo ./constellation_matrix` for provisioning and live service management. [Administrator setup](docs/runbook.md#administrator-install)

## Operate and extend

| Task | Start here |
| --- | --- |
| Check the stack | Run `./constellation_matrix status` for the latest dashboard snapshot, or use **Agents → VPS Alerts → Pinned messages** to open the dashboard or Context Intelligence. |
| Add an agent | Run `./constellation_matrix agent add --profile /path/to/agent.json`. See [agent profiles and remote workers](docs/runbook.md#add-a-worker) and the [shared desktop](desktop/README.md). |
| Review how the team works | Send `review team` in the [Chief of Staff DM](docs/chief-of-staff.md) for proposals backed by Office evidence. |
| Approve maintenance | Review the exact operation and vote in the private [Root Operations chat](integrations/root/README.md). |
| Work from ChatGPT or Telegram | [Connect Dot](docs/dot.md) to publish tasks and read their outcomes. Use [Telegram Control](docs/telegram.md) for drafts and updates from approved chats. |
| Verify or recover a deployment | Run `status --refresh`, `backup`, and `restore-check` with the installation account. See the [acceptance checks](docs/acceptance.md) and [recovery guide](docs/runbook.md#back-up-and-recover). |

Built on [Microsoft Constellation](https://github.com/microsoft/amplifier-constellation) and [Amplifier](https://github.com/microsoft/amplifier).
