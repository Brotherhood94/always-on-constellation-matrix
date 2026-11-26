# Acceptance checks

Record the source revision, component pins, and result for each target independently.

| Feature | Automated check | Deployment acceptance |
| --- | --- | --- |
| Installation and registry | [Installer tests](../tests/test_installer.py) and [standard Docker install smoke](../tests/install-smoke) cover roster selection, reruns, agent registration retries, and pending sign-in in the catalog. | Sign in to the actual subscription, then route a task to the new agent. |
| Guided setup and welcome | [Installer tests](../tests/test_installer.py) cover resume and sender choice. [Install smoke](../tests/install-smoke) checks staged enrollment. [Matrix layout tests](../tests/layout_check.py) check the remote worker membership gate. [Matrix operations smoke](../tests/operations-fixture.mjs) checks both welcome destinations, pinned links, and the Manager example from Element and Dot. | Confirm Element sign-in, select and activate agents, then open the printed welcome link in Element. Verify both links in the workspace pin and send the suggested first task. |
| Worker lifecycle | [Lifecycle tests](../tests/test_roster.py) cover default enablement, approval receipts, and retry. [Matrix operations smoke](../tests/operations-fixture.mjs) covers encrypted approval and an in-flight retirement. [Install smoke](../tests/install-smoke) checks actual removal and credential cleanup. | Select and activate Chief of Staff on a systemd VPS. Approve a hire in Chief’s DM, sign in and complete a small task, then approve retirement. Verify the dashboard and the private archive. |
| Agent placement | [Relocation tests](../tests/test_mobility.py) cover the move journal, leases, checkpoint isolation, and injected failures. | Enroll two hosts. Create an agent on the first, move it to the second, return it to the VPS, and repeat a move from the VPS. Verify the Matrix device, ACP session, shared file, host health, and dashboard history. Reboot a worker during transfer and test resume and rollback. |
| Daily Helper hints | [Clock and state tests](../tests/helper-hints.test.mjs) cover timing, retries, relocation, and opt-out in English and Italian. [Helper Matrix fixture](../tests/helper-fixture.mjs) checks the private chat. | Ask a question in Helper’s DM after moving it. Verify a hint after 10:00 local time, disable hints, and check the preference survives a restart or move. Resume hints in the chat. |
| Element spaces and DMs | [Synapse smoke](../tests/synapse-smoke) checks layout, membership, encryption, and pin permissions. | Open the Agents and Telegram Spaces in Element. Verify Telegram Agent and Telegram Bridge appear in the top-level Telegram Space. |
| Office assignments and reactions | [Routing](../agents/tests/pilot.mjs), [managed tasks](../agents/tests/manager.mjs), and [durable delivery](../agents/tests/instance-acp.mjs) test mentions, provenance, thread routing, and reaction state. | Observe 👀 and one terminal reaction on a real request. |
| Reply limit and delegated failures | [Capability routing](../agents/tests/capability-routing.mjs) exercises replies 15 and 16, scope, and failure return to Manager. | Confirm Manager closes a real handoff and reports a controlled blocker. |
| Attachment access and shared desktop | [Workspace tools](../agents/tests/workspace-mcp.mjs) check attachment reads and file boundaries. [Desktop SFTP](../tests/desktop-sftp) transfers bytes and reads them as a second worker. | Relay an encrypted attachment across agents, then repeat the shared file check from a configured remote host. |
| Dashboard and pins | [Browser smoke](../tests/dashboard-browser) checks rendering and stale data. [Pin tests](../tests/dashboard.test.mjs) and [Matrix operations smoke](../tests/operations-fixture.mjs) cover encrypted messages, URL replacement, and operator pins. | Open both shortcuts and authenticate to the same graph browser from the dashboard. |
| Context Intelligence | [Isolated graph smoke](../integrations/context-execution/tests/isolated) verifies authenticated assignments, handoffs, evidence, task outcomes, and duplicates. | Trace one Office task from an assignment through at least one accepted handoff to verified closure. |
| Chief of Staff and Persona Workshop | [Review tests](../integrations/chief-of-staff/direct/workshop.test.mjs), [capability review](../integrations/chief-of-staff/direct/capability-review.test.mjs), and [Workshop smoke](../tests/workshop-smoke) cover evidence, scoped proposals, and stable managed targets. | Review a real Office task and export a persona draft if Chief of Staff proposes one. |
| VPS Manager | The [approval fixture](../integrations/root/tests/run) tests review, exact execution, redaction, file rollback, and Office receipts. The [Synapse fixture](../tests/synapse-smoke) checks migration of the private DM. | Reject and accept separate read-only proposals in Root Operations. |
| Telegram | [Policy tests](../integrations/telegram/policy.test.mjs), [bridge smoke](../integrations/telegram/tests/bridge-smoke), and [offline engine smoke](../integrations/telegram/tests/engine-smoke) cover verified Office handoffs, automatic admission and binding, delivery recovery, and session restore. | Mention Task Manager in Office. Use a designated test chat to check one exact delivery and a conversation summary in the original thread. |
| Dot | [Matrix operations smoke](../tests/operations-fixture.mjs) checks publication retry and thread isolation. [Telegram tool tests](../integrations/telegram/policy.test.mjs) cover draft and update requests. | Repeat the publication and read checks through the actual OpenAI tunnel. |
| Boot and uninstall | [Lifecycle tests](../tests/test_lifecycle.py) check service enablement and scoped removal. [Install smoke](../tests/install-smoke) verifies that uninstall retains state and an explicit purge deletes it. | Reboot the staging host and confirm the enabled services and agents return. |
| Recovery | [Recovery tests](../tests/test_recovery.py) and [install smoke](../tests/install-smoke) check failure recovery and encrypted Restic restore. | Verify a checkpoint and controlled restart in staging before cutover. |

Use separate projects, ports, and data for VPS staging. Live acceptance requires operator authentication for Element, the provider subscriptions, and enabled integrations. Deployment management commands use sudo. Record the results under the target where they actually ran.

## Post-deployment scenarios

Start with the five starter roles signed in. For the optional coding and file-review scenarios, register Coder and Reviewer from `agents/examples/`. On a fresh installation, “Manager” in those scenarios refers to Task Manager. Use the Element mention picker for every `@Name` below. Give each run a unique ID such as `acceptance-01` and use synthetic files and test chats. Record the scenario number, result, and thread or receipt link. Mark scenarios for disabled integrations **Skipped**.

### 1. Open the workspace

Run `sudo ./constellation_matrix status --refresh`. Open **Agents → VPS Alerts → Pinned messages**. Open **Open dashboard** and **Open Context Intelligence**, then follow the Context Intelligence link from the dashboard.

**Pass** when the signed-in agents show as available, both shortcuts are in one readable **Workspace shortcuts** pin, and both graph links open the same authenticated Neo4j Browser. Check that the Office and VPS Alerts rooms use encryption and that each enabled private agent chat has only the operator and that agent.

### 2. Assign a single Office task

Send this as a new Office message.

> @Coder Create `acceptance-01.txt` in your workspace with exactly `CONSTELLATION-OK`. Attach it to this thread.

**Pass** when Coder adds 👀 during work, publishes the attachment in the original thread, clears 👀 after delivery, and places one ✅ on the original request. Download the file and check its contents. Send the plain text `Coder` as a separate message and confirm that the agent stays idle.

### 3. Route work through Manager

Upload a synthetic `numbers.csv` containing a header `value` followed by `2`, `3`, and `5` on separate lines. Use the upload as the attachment to this request or reply to the upload with the request.

> @Manager Ask Coder to total the attached values and save the result in `desktop/acceptance-01/total.txt`. Ask Reviewer to read the same file and independently verify the total. Close this thread with the verified result.

**Pass** when relevant handoffs stay in this thread with one actionable Matrix mention each, Reviewer can read the same file, the result is 10, and Manager posts the final result and one ✅. While work is active, send “@Manager What is the current status?” in the thread and check that it gets a direct answer without a second assignment. The exact 15/16 reply boundary has a deterministic [routing fixture](../agents/tests/capability-routing.mjs).

### 4. Exercise the blocked path

> @Manager Ask Coder to open `desktop/acceptance-01/missing.csv`. The fixture deliberately leaves this file missing. If Coder cannot open it, report the missing input as a blocker.

**Pass** when Coder returns the blocker to Manager, Manager asks for the missing file or reports the terminal blocker, and the original request gets one ❌ when the task closes as blocked. The final report identifies the missing input.

### 5. Add a worker and verify shared state

Use the example profile as QA, activate it, and mention it in Office. Ask it to read `desktop/acceptance-01/total.txt` and append `QA checked` on a new line. Ask Coder in a fresh thread to read it back. If a remote worker is enabled, repeat the read and append with that worker and verify the result from the local worker. [Agent registration](runbook.md#add-a-worker) · [Remote worker setup](runbook.md#add-a-remote-worker)

**Pass** when the agent joins Office, appears in the dashboard and Persona Workshop’s Managed Agents, routes work after provider sign-in, and shares the same file with the other worker. The deployment’s `workshop-management/targets.json` also records the managed target. Confirm that each worker’s private workspace stays scoped to that worker.

### 6. Review the evidence and draft a persona change

Open Neo4j Browser from the dashboard and look for scenario 3 by time and Matrix reply ID. Group its rows by task and compare reply numbers against the Office thread.

```cypher
MATCH (e:Event {workspace: 'agents-team', event_name: 'agents:execution'})
RETURN e.occurred_at AS time, e.execution_task_id AS task,
       e.execution_sequence AS reply, e.created_by AS contributor, e.data AS evidence
ORDER BY time DESC LIMIT 30
```

If Chief of Staff is enabled, send `review team` in its DM. Follow an observation link back to the task. If the review proposes an edit or hiring action, use its observation ID to export a draft. [Chief of Staff guide](chief-of-staff.md#review-and-draft-a-profile-change)

**Pass** when the timeline distinguishes the contributors and the review cites the right Office evidence. A proposed edit must target the cited managed agent, and a hiring draft must link to the cited capability gap. Record a draft as **Skipped** if the review finds no qualifying proposal.

### 7. Verify optional integrations

| Integration | Action | Pass when |
| --- | --- | --- |
| Dot | Through the private app, publish the same small Office plan twice with task ID `acceptance-dot-01`. Read the result by ID and try an ID Dot never published. | Both publications return the same thread. Dot can read its task and declines the other ID. |
| Telegram | Complete QR sign-in in **Telegram → Rooms → Telegram Bridge**. In Office, mention Task Manager to open a designated test contact, create a test group, add a participant, preview a draft, send an exact test message, and request a summary. Repeat a read through Dot when enabled. | Contacts and groups appear automatically under **Telegram → People**. Telegram Agent replies in the originating Office thread with a real mention. The preview sends nothing, the explicit send delivers once, and Dot sees the result of its own task. Unlinking revokes agent access and preserves the operator’s history. |
| Helper | In **Agents → People → Helper**, open the pinned welcome. Ask how to set up Element and whether Context Intelligence is healthy. Then ask **@Task Manager** in Office to consult Helper on a setup question. | Helper cites the deployed setup guide, reports the observed service status and timestamp, and returns the handoff in the original Office thread. The private chat stays visible only to the operator and Helper. |
| Root Operations | Open **Agents → Rooms → Root Operations** and review the room after layout repair. | Its room ID, encrypted history, and approval flow continue to work. |
| VPS Manager | Mention VPS Manager in Office and request `matrix-stack-status`. Reject the proposal in **Root Operations**. Ask Manager in a new Office thread to delegate the same read-only check to VPS Manager. Review and accept the new proposal. | Each poll binds to its exact proposal. The original thread receives a sanitized receipt. The graph shows the delegated handoff and Manager closes with the receipt as evidence. |

Use the [Dot setup](dot.md), [Telegram Agent guide](telegram.md#operate-in-element), and [Root Operations guide](../integrations/root/README.md) for the required test accounts and connection steps.

### 8. Verify recovery in staging

With no active task in your isolated deployment, run the checkpoint and restore check. Reboot the isolated staging host. Verify the enabled units and Compose services, then repeat a small Office request with a fresh run ID. [Recovery commands](runbook.md#back-up-and-recover)

**Pass** when the encrypted checkpoint restores in isolation, the staging agent resumes with the same identity, the shared file is still readable, and each new request gets a single result.

### 9. Exercise the starter team

1. Open each roster entry under **Persona and duties**. Follow a contact link, return to the roster, and load the profile URL directly on a narrow screen. Include one user-defined specialist.
2. Ask Task Manager to hand a tool-discovery assignment to Scouter. Match the handoff, returned evidence and final outcome to the same Office request in Context Intelligence.
3. Submit two distinct Office tasks that require the same uncovered duty, such as writing release notes for one project and preparing a customer-facing change summary for another. Confirm Context Intelligence records a role gap with different capability or resource labels on each task. Wait for Hiring Manager’s semantic review and verify one private proposal cites both assignments. Reject it and confirm the roster is unchanged. Use a separate hiring proposal to verify approval and finish the new agent’s provider setup.
4. Ask Coach about an evidenced failed handoff, inspect the revised working style and outbound contacts, and undo it in Coach’s DM. A change involving VPS Manager contacts must ask for a vote.
5. Ask Scouter for a read-only tool for the new specialist. Check that a rejected grant never appears. Approve another proposal, verify it on the target, and check that another agent cannot use it.
6. In an isolated legacy deployment, preview and apply the team migration. Confirm the coordinator’s original Matrix ID, retained specialist, private Chief history, and unchanged welcome receipt. Rerun and compare all room IDs.

[Role workflows and examples](team.md)

## Refactor verification record

- On 26 November 2026, the hosted Linux run for `e4e5adc` passed clean installation, legacy roster migration and rerun, encrypted backup and restore, uninstall, and purge. During migration the installer honored a 239-second Synapse sign-in cooldown and retained the coordinator’s original Workshop target. Both verification jobs passed. [Hosted installer and Workshop results](https://github.com/Brotherhood94/always-on-constellation-matrix/actions/runs/45785497814)
- A fresh isolated Matrix workspace had all five starter identities in Office, four distinct encrypted operator chats, and a dashboard persona for every agent. The VPS checkout passed configuration validation, 94 installer and lifecycle tests, and seven Matrix helper tests. Live provider sign-in and deployment on the operator’s VPS still require an authenticated operator session.
- The five-role change passed an isolated Synapse migration rehearsal, including a blocked migration with open work, preservation of the original coordinator and Chief history, and rerun with the same private rooms. Separate devices sent and decrypted proposal-bound votes in all three management chats. The Workshop fixture applied two revisions to a real Instance, preserved its communication instructions, and restored the previous profile. The Neo4j fixture verified scoped Office evidence access and per-agent tool-check attribution. These checks use synthetic accounts and provider fixtures. [Matrix rehearsal](../tests/team_migration_fixture.py) · [Encrypted desk checks](../tests/team-desks-fixture.mjs) · [Workshop fixture](../tests/workshop-smoke) · [Graph fixture](../integrations/context-execution/tests/isolated)
- Local isolated checks covered coordinator install and rerun, the graph browser proxy, encrypted Matrix pins and Dot task isolation, Constellation startup and ACP session restore, Root approvals, Telegram drafts and updates, and Context Intelligence timelines. The Restic checkpoint and restore ran on a native Linux fixture.
- An isolated Linux fixture passed clean installation with Manager selected, rerun with the same Matrix room IDs, agent registration and retry with one stable Workshop target, dashboard and graph proxy access, live inventory, and encrypted checkpoint and restore. Provider sign-in remains an operator acceptance check.
- Hosted CI on `bb1d1db` passed the unit, dashboard, Compose, SFTP, Synapse, Root and Persona Workshop checks. [Hosted verification run](https://github.com/Brotherhood94/always-on-constellation-matrix/actions/runs/45452175583)
- The Element onboarding update passed the local unit and Compose checks, isolated Synapse layout and guide decryption checks, and the pinned mautrix bridge smoke. The `macai` install and authenticated Element, Dot, and Telegram checks require an operator session with sudo and the designated test accounts.
