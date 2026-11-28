# Always-on Constellation via Matrix

**A private, always-on AI office you can shape around your work.**

Give Task Manager a request in Element or from Dot in ChatGPT. It can assign parts of the work to other agents, keep the handoffs in one encrypted Office thread, and return the result with evidence. The team runs on a Linux VPS and stays available when your laptop is offline.

Start with six roles and add specialists as your work grows. Use the record of real assignments, handoffs, and outcomes to decide when to improve how agents work together, equip an existing agent, or add someone to the team.

[Quick start](#quick-start) · [Starter team](#meet-the-starter-team) · [Office workflow](#follow-an-office-task) · [Architecture](#architecture) · [Deployment scenarios](docs/acceptance.md#post-deployment-scenarios)

![A compact server connected by illuminated traces to four agent modules](assets/constellation-vps-hero.jpg)

## Meet the starter team

| Agent | What it owns | Where to start |
| --- | --- | --- |
| **Task Manager** | Coordinates work, delegates by capability, and verifies the result in the original thread. | **Office** |
| **Hiring Manager** | Groups recurring capability gaps across differently worded assignments and proposes new personas or retirements. | **Hiring Manager** private chat |
| **Coach** | Reviews handoffs in Context Intelligence and revises working styles and permitted contacts through Persona Workshop. | **Coach** private chat |
| **Scouter** | Researches skills, MCP servers, and tools for existing duties and has the target agent verify approved access. | **Scouter** private chat |
| **The Architect** | Prepares host operations and tested workspace patches for an operator vote. Recovers explicitly opted-in connectors when they fail. | **Office** for requests, **Agents → Rooms → Control Room** for approval |
| **Helper** | Answers setup and troubleshooting questions using the deployed guides and dashboard health. | **Agents → People → Helper** |

Open the dashboard from **Agents → Control Room → Pinned messages → Workspace shortcuts**. Select **Persona and duties** on any roster entry to see what it owns, how it works, whom it can contact, and which tools it has verified. **Team connections** shows the permitted directions. Follow an arrow to review that route’s changes and recent Office handoffs, or select **View connection changes** for the workspace log. Coach can propose a direct Dot route after three bridge-confirmed Office deliveries. Review the proposal in Coach’s private chat. [Team connections and history](docs/team.md#team-connections) · [Starting contact permissions and role examples](docs/team.md#first-interactions)

![Constellation dashboard with CPU, memory and disk charts, Task Manager's contact routes, and the five-agent roster](assets/screenshots/dashboard.webp)

*Demo dashboard with sample VPS metrics and Task Manager’s incoming and outgoing routes highlighted.*

## Quick start

On a Linux VPS with Docker Engine and Compose, run setup as your regular user after `docker info` succeeds. Use an enrolled Tailscale node or an existing private HTTPS proxy for Element, the dashboard, and Context Intelligence. [VPS prerequisites](docs/runbook.md#initial-setup)

```sh
./constellation_matrix doctor
./constellation_matrix install
```

1. Choose your network, operator, passwords, integrations, and starter team.
2. Connect one provider and pick a model and reasoning effort. Reuse that connection for the team or choose exceptions. The installer checks the selected agents before showing the Element page.
3. Sign in to Element with the displayed homeserver. Open **Agents → Rooms → Control Room** for the dashboard, Context Intelligence, and enabled integration guides. If you enabled Telegram, finish its QR sign-in in the VPS terminal.

Resume an interrupted setup with `./constellation_matrix setup-guide`. [Guided setup](docs/setup-guide.md)


### Hire a specialist from Element

Ask **@Task Manager** in Office to hire a specialist or send `hire release-editor Prepare release notes from verified changes` in **Agents → People → Hiring Manager**. Reply to the setup prompts to choose **ChatGPT Codex** or **ChatGPT Plan**, the connected account, the model, and reasoning effort. Open the encrypted proposal attachment and vote once. Hiring Manager starts the approved Docker services and reports when the new agent is ready for an Office assignment. [Hiring walkthrough](docs/team.md#hire-from-office)

### Finish Telegram setup

Enabling **Telegram** installs **Telegram Agent** automatically. Telegram Agent uses the shared provider during agent setup. After Element sign-in, the same installer guides you through importing your Telegram API credentials and linking your account with a terminal QR code and a hidden two-step verification prompt. If interrupted, resume setup from the same terminal.

```sh
./constellation_matrix telegram setup
```

[Telegram credentials and QR sign-in](docs/telegram.md#install-the-bridge)

### Finish in Element

- **Agents → Control Room → Pinned messages.** Open **Workspace shortcuts** for the dashboard and Context Intelligence. If you enabled Dot, open **Connect Dot** to configure the private Constellation Office plugin.
- **Agents → Control Room → Pinned messages.** Open **Set up Telegram**. Telegram Agent manages the Telegram Space and joins Office as its Telegram specialist. Mention **@Telegram Agent** or **@Task Manager** in Office. Find contacts and groups under **Telegram → People**. Telegram Agent verifies the destination and reports the result in the originating thread. Use a draft request to preview a message. In Control Room, mention Telegram Agent to check the bridge or request an approved administration operation. [Telegram setup and examples](docs/telegram.md)

### Send your first task

Task Manager is the entry point for coordinated Office tasks. Open **Agents → Office** in Element, select `@Task Manager` from the mention picker, and send “@Task Manager, coordinate a three-step checklist for getting started in Office and post the result in this thread.”

If you enabled Dot, open a ChatGPT conversation with the **Constellation Office** plugin and ask “Dot, send this task to Office starting with Task Manager: coordinate a three-step checklist for getting started and post the result in the Office thread.” [Dot setup](docs/dot.md)

With Dot and Telegram configured, try “Dot, ask Task Manager in Office to send a private Telegram message to Sam Example saying «Hello».” Task Manager hands the request to Telegram Agent in Office. Dot reports the verified result from the same thread. [Telegram from Office](docs/telegram.md#use-telegram-from-office)

If you skipped Task Manager, use the Office agent named in your welcome. If you skipped the Office roster, add a worker first. [Add a worker](docs/runbook.md#add-a-worker)

## Follow an Office task

Mention **Task Manager** when you want several agents to work on a request. Task Manager sets the acceptance criteria, delegates by capability, and verifies the evidence before closing the task.

![English Office thread in Element showing Alex Morgan’s request, Task Manager’s handoff to Scouter, a GitHub Docs link, and a verified result](assets/screenshots/element-office.webp)

*In this scripted demo, Scouter checks GitHub’s release-notes workflow and Task Manager recommends editing the generated notes before publication.*

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
    participant Manager as Task Manager
    participant Scout as Scouter
    User->>Office: Mention Task Manager with a tool research request
    Office->>Manager: Deliver request
    Manager->>Office: Add 👀 and give Scouter a research question
    Office->>Scout: Deliver scoped handoff
    Scout->>Office: Return publisher evidence to Task Manager
    alt Verified
        Manager->>Office: Post result and add ✅
    else Blocked
        Manager->>Office: Report blocker and add ❌
    end
```

An agent adds 👀 when it accepts an assignment and clears it after delivery. A managed task has up to **15 agent replies**, with the final reply reserved for Task Manager. You can ask an agent directly for status without consuming a turn. [Communication policies](policy/README.md)

### Improve the team from its work

Context Intelligence records who handed off an assignment, whether the recipient accepted it, and whether the original task finished or was blocked. During a stalled release task, Scouter can propose narrowly scoped GitHub access for a Coder who already owns repository work. Coach can revise the working agreement if the handoff omitted acceptance criteria. Hiring Manager can propose a release editor if the team repeatedly has nobody responsible for release notes. [Execution timelines](integrations/context-execution/README.md)

![Context Intelligence execution graph in Neo4j Browser showing the release-notes task, Scouter’s handoff, and the verified outcome](assets/screenshots/context-intelligence.webp)

*The same demo task in Neo4j Browser. The graph links Task Manager’s dispatch to Scouter’s acceptance and return, and records the verified outcome.*

These management workflows become available as the relevant agents activate. The operator approves hires, retirements, tool grants, and changes to contacts involving The Architect in private chats. Coach can apply and undo ordinary working-style and contact changes. [Team workflows](docs/team.md#first-interactions)

## Architecture

Each agent has a Matrix identity, a private workspace, and a persistent Constellation Instance running Amplifier. The **Agents** Space groups **Office**, **Control Room**, and the operator’s private chats with **Hiring Manager**, **Coach**, **Scouter**, and **Helper**. The encrypted approvals room **Control Room** appears under **Agents → Rooms**. General Office workers also share a desktop for task files.

Docker restart policies bring the workspace services back when the engine starts. The Docker control runtime runs scoped operations, monitors the deployment, and schedules checkpoints. Tailscale Serve provides private dashboard access. [Architecture notes](docs/architecture.md) · [Boot and service placement](docs/runbook.md#boot-and-service-placement)

![Architecture overview showing Element, Dot and Telegram connecting to Matrix and always-on Amplifier agents on a private VPS](assets/architecture.svg)

<details>
<summary>Service connections in Mermaid</summary>

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

</details>

## Operate and extend

For unattended provisioning, edit the example configuration, check it with `./constellation_matrix validate --config <file>`, and run `./constellation_matrix install --config <file>`. Run `setup-guide` from an interactive terminal afterwards. [Example configuration](deploy/config.example.json)

| Task | Start here |
| --- | --- |
| Check the stack | Run `./constellation_matrix status` for the latest dashboard snapshot, or use **Agents → Control Room → Pinned messages** to open the dashboard or Context Intelligence. |
| Update an existing workspace | Run `./constellation_matrix update` from the checkout containing the revision you want to deploy. [Update a workspace](docs/runbook.md#update-an-existing-workspace) |
| Add a local Office worker | Run `./constellation_matrix agent add --profile /path/to/agent.json --configure`. This opens native Amplifier sign-in after provisioning. See [agent profiles](docs/runbook.md#add-a-worker) and the [shared desktop](desktop/README.md). |
| Retire a local Office worker | Run `./constellation_matrix agent remove <id>` to finish accepted work, revoke access, and archive the workspace. [Retirement](docs/runbook.md#retire-a-worker) |
| Manage staffing | Ask **Hiring Manager** to hire or retire a teammate and review its exact proposal in the private chat. [Staffing](docs/team.md#first-interactions) |
| Improve handoffs or tools | Ask **Coach** about an Office handoff, or **Scouter** about a missing tool. [Contacts and tool grants](docs/team.md) |
| Upgrade an existing team | Preview with `./constellation_matrix agent migrate-team`. [Migration and recovery](docs/team.md#upgrade-an-existing-team) |
| Ask for VPS maintenance | Mention **The Architect** in Office or ask Task Manager to delegate. Review the action in the private [Control Room chat](integrations/root/README.md). |
| Release a workspace patch or recover a connector | Enable [The Architect maintenance](integrations/root/README.md#repository-maintenance-and-automatic-recovery) on the VPS. Vote on tested patches in Control Room and choose which connectors can restart automatically. |
| Trace an Office task | Open [Context Intelligence](integrations/context-execution/README.md) to see who handed off the work, whether the recipient accepted it, and how the original assignee closed the task. |
| Get help with setup or a service | Open **Agents → People → Helper**. On an existing deployment, run `./constellation_matrix agent add --starter helper --configure`. [Helper guide](docs/team.md#ask-helper) |
| Repair sidebar placement | Run `./constellation_matrix layout` on the VPS and sign in as the Matrix operator. [Layout migration](docs/telegram.md#repair-an-existing-workspace) |
| Work from ChatGPT or Telegram | [Connect Dot](docs/dot.md) to publish tasks and read their outcomes. Use [Telegram Agent](docs/telegram.md) to open chats, manage groups, send requested messages and summarize registered conversations. |
| Test a deployed team | Run the [deployment scenarios](docs/acceptance.md#post-deployment-scenarios), from a single Office task through handoffs, approvals, and recovery. |
| Verify or recover a deployment | Run `status --refresh`, `backup`, and `restore-check`. See the [acceptance checks](docs/acceptance.md) and [recovery guide](docs/runbook.md#back-up-and-recover). |

### Move agents between hosts

Enroll a Linux server, PC, or VM on the same Tailscale network. You can create an agent there with `agent add --host`, or move an existing agent from the VPS with `agent move`. The agent keeps its Matrix identity, workspace, and Amplifier sessions. New assignments pause while the source finishes its current turn and the destination starts. The dashboard shows its current host and readiness. Open **Persona and duties** for its move history.

```sh
./constellation_matrix agent move helper --to worker.example.ts.net
```

For enrollment, recovery, and migrating an older remote worker, follow the [host relocation runbook](docs/runbook.md#move-agents-between-hosts).

### Daily Helper hints

Helper shares one tip per day in **Agents → People → Helper**, starting after its welcome. Hints become eligible at 10:00 in the deployment timezone and include features such as installing a remote worker or moving an always-on agent. Say **“stop sending hints”** or **“resume daily hints”** in the private chat. [Timing and Italian commands](docs/team.md#daily-helper-hints)

### Control Room and operation timings

**The Architect** accepts requests directly in **Control Room** and can receive verified handoffs from Task Manager in Office. The encrypted room contains workspace links, maintenance polls, receipts, and operational alerts. Mention **@The Architect** and say **Diagnose services** for a health overview or **Diagnose <service>** for a targeted check. Dot can search the room’s decryptable history and relay requests from ChatGPT. Approvals are cast by the operator in Element.

**Operation timings** shows measured work, queue and approval durations for the last seven or thirty days. A weekly Control Room summary identifies groups that consumed the most time in measured phases. [Maintenance and timing guide](integrations/root/README.md)

## Planned features

- Run a Linux Digital Twin on the same VPS so The Architect can test installer and infrastructure changes with a separate Docker Engine. [Digital Twin plan](next_features_plans/plan.md#linux-digital-twin)

- Copy files from workspaces and approved folders to the shared desktop from local and remote agents. The plan includes approval for protected agents. [Shared desktop copy plan](next_features_plans/plan.md#shared-desktop-file-copying)
- Attach a named file from the VPS shared desktop to a Telegram message. [Telegram attachment plan](next_features_plans/plan.md#attach-a-file-from-the-vps)

## Uninstall

From the repository on the VPS, remove the workspace services and keep your data and backups.

```sh
./uninstall.sh
```

For a fresh install, purge the workspace. This permanently deletes its data, backups, and Docker volumes.

```sh
./uninstall.sh --purge
```

See the runbook for custom data directories and remote workers. [Uninstall options](docs/runbook.md#uninstall)

Built on [Microsoft Constellation](https://github.com/microsoft/amplifier-constellation) and [Amplifier](https://github.com/microsoft/amplifier).
