# The starter team

The installer preselects six agents after you sign in to Element. Keep the ones you need, or start with an empty Office. Each selected agent has its own Matrix identity, provider configuration, persistent Amplifier sessions, and Persona Workshop target. The Hiring Manager, Coach, and Scouter work in Office and also have private, encrypted operator chats in the Agents Space. VPS Manager uses **Agents → Rooms → Root Operations** for host approvals. Helper answers the operator in **Agents → People → Helper**.

On the dashboard, open **Persona and duties** next to any agent. Its page shows what it owns, how it works, its limits, whom it can contact, its verified tools, and its Workshop revision when available. Bookmark the page or open a contact’s profile from it. A remote specialist or an older profile with only a capability description also gets a page.

## Ask Helper

In Element, open **Agents → People → Helper** and ask a plain-language question. For example, “How do I set up Element Desktop?” or “Is Context Intelligence healthy, and when was it last checked?” Helper cites the deployed guide and identifies stale or unavailable health data. In People, Helper and the management agents add 👀 when they begin handling your message.

For a question that belongs in an Office task, ask **@Task Manager** to consult Helper. Task Manager is the only agent that can initiate this handoff. The answer returns to the original Office thread. Helper’s private chat uses a separate session and accepts questions from the operator.

### Daily Helper hints

After its welcome, Helper shares one randomly selected tip per day in its private chat. Tips are sent at or after 10:00 in the deployment timezone. To set the timezone in an unattended install, use `"helper_hints_timezone": "Europe/Rome"` or another IANA timezone. The default is UTC. Helper skips missed days and cycles through the features enabled in the deployment.

- Turn hints off with **“stop sending hints”** or **“smetti di mandarmi i suggerimenti”**.
- Turn them back on with **“resume daily hints”** or **“riattiva i suggerimenti giornalieri”**.

Helper confirms the change in the same chat. The preference follows Helper across restarts and [host moves](runbook.md#move-agents-between-hosts).

On an existing deployment, run this from the VPS checkout and complete Amplifier’s provider setup.

```sh
./constellation_matrix agent add --starter helper --configure
```

## Team connections

Each arrow in **Team connections** points from an assigner to an agent it may hand off to. Follow an agent name for its persona, or use **Connection list** to navigate the same routes. Pending and unavailable agents are labeled. An actual handoff also depends on capability, resource scope, readiness, and Office membership.

Open **View connection changes** for the workspace log. Follow an arrow to see the changes for that direction and its classified handoffs over the last 30 days. Each recorded handoff links to the Office thread and shows its progress and the overall task outcome when available. Older evidence whose direction cannot be verified is labeled unclassified.

The log shows its coverage start date. A fresh installation starts recording when it creates the roster. An upgrade takes a baseline from the existing roster on its first run. Coach changes, undo, registration, retirement, and profile changes are recorded when they affect the graph. The host monitor refreshes the report. For deployments with host monitoring disabled, run `sudo ./constellation_matrix status --refresh`. The current graph uses the agent catalog while the history catches up after a Context Intelligence outage.

## First interactions

| From | Starting contacts for new Office assignments |
| --- | --- |
| Task Manager | Hiring Manager, Coach, Scouter, VPS Manager, Helper, and registered specialists |
| Hiring Manager | Task Manager, Coach, Scouter |
| Coach | Task Manager, Hiring Manager, Scouter |
| Scouter | Task Manager, Hiring Manager |
| VPS Manager | Task Manager |
| Helper | Returns answers to Task Manager in the original thread |
| New specialist | Task Manager by default, or the explicit contacts in its profile |

A contact grants permission to initiate a handoff. The recipient must also own the requested capability and resource, be ready, and have joined Office. A teammate can return an assigned result in its original thread. An operator can address Office agents through Element’s mention picker and can ask Helper directly in its private chat. Private decisions stay in the relevant two-person chat.

1. **Coordinate work.** In **Agents → Office**, select **@Task Manager** from the mention picker and send “Coordinate a review of the tools our team needs for release notes. Ask Scouter to research one option and give me its publisher and permissions.” Task Manager can also finish simple work itself. If you use Dot, ask the **Constellation Office** plugin to send the task to Office starting with Task Manager.
2. **Improve collaboration.** In **Coach**, ask “Review the handoff in this Office thread and see whether the next agent received sufficient evidence.” Coach reads the task history, cites a participating agent’s event, and applies a justified working-style revision through Workshop. It can edit ordinary outbound contacts. Changes to contacts involving VPS Manager require your approval. Coach reports the change privately and follows up after later outcomes. Send `undo scouter`, substituting the affected agent ID, to restore the previous agreement.
3. **Fill a staffing gap.** Hiring Manager groups repeated capability gaps by meaning, even when Office uses different labels or project scopes. Proposals cite at least two distinct assignments for the same uncovered duty. You can also send `hire release-editor Prepare release notes from verified change summaries` in **Hiring Manager**. Review the proposed identity, persona, duties and scope, then vote on that proposal. An approved hire appears pending sign-in. Run `./constellation_matrix agent configure release-editor` on the VPS to choose its Amplifier provider. To retire it, send `retire release-editor` and review the duties, affected contacts and open assignments. Hiring Manager can retire another starter. To retire Hiring Manager itself, use the operator CLI.
4. **Equip an existing agent.** For example, an installed Coder may own repository inspection but lack GitHub access. In **Scouter**, name the agent, the repository and the read-only operation you need. Scouter also reviews tool and credential blockers from Context Intelligence, and looks for useful tools weekly. The proposal names the target, publisher, version, access and verification. Approve it in Scouter’s private chat. The target checks the adapter using its own credentials before it appears under verified tools. A missing credential is reported privately.
5. **Maintain the VPS.** Ask Task Manager to delegate a service check to VPS Manager. In **Root Operations**, review the proposed host operation and vote. VPS Manager posts the receipt in the originating thread. A staffing decision or contact change confers no host execution privilege.

The Scouter schedule is UTC. Set `"scout_weekly_utc": "Mon 09:00"` in the install configuration, choose another weekday and time, or use `"off"` to disable the scheduled search. Evidence-driven reviews still run.

### How recurring staffing gaps are grouped

Hiring Manager reads recent Office evidence from Context Intelligence. Its existing exact match catches the same capability and resource pair on two distinct assignments. For different pairs, its configured Amplifier provider reviews a bounded batch of explicitly reported, uncovered role gaps. For example, authoring release notes in one project and writing the customer-facing change summary for another can support one Release Editor proposal.

Automatic semantic reviews run at most once every ten minutes while the desk is idle. Hiring Manager rechecks the roster and assignment outcomes before drafting a proposal. The private proposal links to the original Office assignments. Repeat scans and overlapping exact matches reuse the existing review, and a provider outage leaves exact matching available. See the [staffing acceptance scenario](acceptance.md#9-exercise-the-starter-team).

## Add your own specialists

Use the optional [Coder](../agents/examples/coder.json), [Analyst](../agents/examples/analyst.json), [Reviewer](../agents/examples/reviewer.json), or [remote worker](../agents/examples/remote-worker.json) as a starting point. Set a fresh key, capabilities with resource scopes, purpose, duties, working style, and optional outbound contacts. See the [starter profiles](../deploy/matrix/starter-team.json).

```sh
sudo ./constellation_matrix agent add --profile /path/to/specialist.json --configure
```

The registration command joins the agent to Office, provisions its own Instance, and enrolls it in Persona Workshop. Remote workers finish enrollment on their own host. [Remote setup on both machines](runbook.md#add-a-remote-worker)

### Supported tool assignments

| Adapter | Enforced access | Target check |
| --- | --- | --- |
| GitHub CLI reader | A single named repository. Operations read repository details, issues, and pull requests. | The target reads that repository through the CLI. |
| Text skill | A public HTTPS skill file with its approved SHA-256 digest. | The target fetches the file and checks the digest. |
| MCP server | A public HTTPS Streamable HTTP endpoint, exact server version, and explicitly approved tool names. | The target connects, verifies the version, and lists the approved methods. |

For the GitHub reader, place a fine-grained read-only repository token in the target host’s private credential directory. A local worker uses `<data-dir>/state/<agent-id>/tool-credentials/github-token`. A remote worker uses `<worker-data-dir>/state/<agent-id>/tool-credentials/github-token` on its own host. Make the token readable only to the agent (UID 991, mode `0400`). For VPS Manager, use owner 991, group 992, mode `0440` so the verifier and the Instance can both read it. Supply it on the host and keep it out of Matrix and version control. After correcting a failed check, request a fresh scoped proposal.

## Upgrade an existing team

Updating the checkout preserves the installed roster. On the VPS, preview the identity mappings and optional retirements first.

```sh
sudo ./constellation_matrix agent migrate-team
```

When ready, finish outstanding assignments and approval polls. If the installation still has legacy Root, complete its [migration to VPS Manager](runbook.md#upgrade-from-root-to-vps-manager) first. Run the migration during a quiet period, then configure the new agents.

```sh
sudo ./constellation_matrix agent migrate-team --apply
./constellation_matrix agent configure
```

The current coordinator keeps its Matrix ID, provider, devices, and sessions under the display name **Task Manager**. VPS Manager keeps its existing identity. The old Chief of Staff account is deactivated, its private history remains accessible to the operator, and its Workshop database moves with the deployment. Hiring Manager, Coach, and Scouter get separate identities and fresh private chats. Existing specialists stay registered. To retire specific ordinary local workers as part of the transition, repeat `--retire <id>` in the preview and in the apply command.

A private migration journal records completed phases. If interrupted, resume with the same application command. Once complete, repeating it reuses the existing rooms and identities. For a full rollback, restore the deployment checkpoint taken before migration. [Backup and recovery](runbook.md#back-up-and-recover)
