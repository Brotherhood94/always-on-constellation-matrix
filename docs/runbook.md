# Deployment and recovery runbook

## Prerequisites

1. Obtain matching `matrix-stack` and `matrix-agents` installer sources (the latter has been distributed as `matrix-agents-pilot.tar.gz`) from the authorized distribution. A full build needs the revision that contains the Manager service, the shared connector policies, and Manager backup coverage. Check the bundle contents before installation. Obtain the separate Dot, external worker, and [Chief of Staff](chief-of-staff.md#activate-the-components) deployment sources for the optional services you plan to enable.
2. Prepare an Ubuntu 24.04 VPS with root console access, active host Tailscale, a tailnet HTTPS name, and cgroup v2. Confirm port 18008 is free on loopback and that Tailnet Serve ports 443 and 8443 are available. Keep a separate root console available for recovery.
3. Confirm the permanent Synapse `server_name` before creating accounts. Use the deployment's tailnet DNS name for the reported setup. It forms part of every local Matrix ID. Record real operator values privately.

| Example value | Use |
| --- | --- |
| `<SERVER_NAME>` | Permanent homeserver name and HTTPS hostname |
| `<OWNER_HANDLE>` | Personal Synapse handle without the leading `@` |
| `@manager:<SERVER_NAME>` | One example of a full Matrix ID for an invitation |
| `<EXTERNAL_HOST>` | Separate Linux host for the external worker, if used |
| `<TUNNEL_ID>` | Secure MCP Tunnel associated with the private ChatGPT workspace |
| `<TASK_ID>` | Unique ID for one plan. Reuse it when retrying the same publication. |

## 1. Install Matrix and connect Element

1. From the paired `matrix-stack` source directory in the root console, run `bash ./install '<OWNER_HANDLE>'`, replacing the placeholder. The installer sets up Docker and Compose if needed, creates private rooms and accounts, and configures persistence, Tailscale Serve, and systemd. Enter passwords at the installer prompt.
2. With Tailscale connected, choose **Sign In** and a custom homeserver in Element Desktop on the Mac. Use `https://<SERVER_NAME>` and the personal Synapse credentials. Save the Element recovery key in the password manager.
3. Sign in to the same homeserver and account in Element X on the iPhone. Verify the new device and confirm encrypted messages and attachments work in both directions. Set `VPS Alerts` to notify for all messages and pin the private dashboard URL, `https://<SERVER_NAME>:8443/`.
4. From the source directory, run `bash ./verify-phase1` as root. Confirm encrypted test alert delivery, private endpoint access, resource accounting, and isolated restore. Schedule a separate reboot check before adding workers.

## 2. Provision the VPS agents

1. From the matching `matrix-agents` source directory, provision Analyst, Reviewer, Coder, and Manager in order using `bash ./install-analyst`, `bash ./install-reviewer`, `bash ./install-agent coder`, and `bash ./install-agent manager`.
2. After provisioning each identity, invite its full Matrix ID to `Agents` in Element and run `/opt/matrix-agents/connect-codex <agent>` as root. Complete the dedicated device sign-in and confirm the configured model and effort pass the probe. Use the model configuration from the matching Compose revision.
3. Include dashboard registration in every agent installer. The shared `matrix-agents` project discovers agents from their provisioned room ID and container metadata. For a separate Compose project, set the `net.matrix_agents.dashboard: "true"` service label and `AGENT_USERNAME`, `AGENT_NAME`, `AGENT_MODEL`, and `AGENT_REASONING`, or register its local project and service with `/opt/matrix-stack/dashboard_inventory.py`. Pass `--bundle` for an Amplifier bundle whose model and effort the collector should read. Include the worker's hostname, model, effort, and expected availability in the deployment check. Confirm its separate workspace and persistent state under `/srv/agents/<agent>`. Run the applicable checks in [Acceptance](acceptance.md#checks-for-each-deployment).
4. For a separate external worker, install its host-specific Constellation Agent Instance and Matrix connector under a dedicated Linux user. Enable the applicable user services for startup. Include `register-external-agent` in provisioning to add the identity to the shared router and dashboard, then join `Agents` and verify an encrypted round trip and a managed handoff. Use the external deployment source and preserve per-host workspace, credentials, and backup scope.

## 3. Connect Dot and optional GitHub access

1. Where Dot is needed, use the matching Dot deployment bundle to install the bridge, Matrix identity, and tunnel service. Invite its Matrix account to `Agents`. Run `/opt/matrix-dot/connect-tunnel <TUNNEL_ID>` in the root console and supply the runtime key at the hidden prompt. Confirm the loopback readiness path at `http://127.0.0.1:18786/readyz`.
2. Connect the tunnel as a private plugin in the intended ChatGPT workspace. The app uses the workspace's tunnel association and a separate file-backed header between the tunnel client and the local bridge. Refresh the plugin after changes to the starter-agent schema. Test a plan addressed to Manager and retrieve its result by task ID.
3. If Coder needs GitHub, use `bash ./enable-coder-github` from the matching source on the VPS. The deployed integration uses a dedicated classic credential with `repo` and `workflow` scopes. The connector rejects repository deletion and the token excludes `delete_repo`. GitHub account permissions and repository rules govern each operation. Keep the credential in the separate read-only mount, rotate it with `/opt/matrix-agents/connect-github`, and verify access with an authorized `GET /user`.

## 4. Monitor and recover

| Resource | Sample | Alert | Recovery |
| --- | --- | --- | --- |
| CPU | Every minute | Above 50% for five samples | Below 45% for five samples |
| RAM | Every minute | Above 50% for five samples | Below 45% for five samples |
| Disk | Hourly | Above 25% | Below 22.5% |

Thresholds measure the project's share of VPS resources. The collector tracks the project's cgroup v2 CPU and memory usage and the allocated disk space for its data, volumes, logs, and local backups. The private dashboard retains 30 days of samples and lists the agents, host, model, and effort when available. A registered external worker appears in inventory. The resource samples cover the VPS workload. The durable Monitor queue sends one encrypted notice at alert and recovery transitions.

- **Restart.** `sudo systemctl restart matrix-stack.service` brings up both local Compose projects with their configured services. Check `matrix-monitor.service`, `matrix-backup.timer`, and `matrix-dot-tunnel.service` when Dot is enabled. Check the external worker's user services separately.
- **Back up.** Restic stores the latest seven successful encrypted snapshots locally in `/var/backups/matrix`. The persistent timer runs nightly at 03:30 in the configured time zone. Snapshots include PostgreSQL, Synapse configuration, signing identity and media, local Matrix encryption state, queues, and configured local agent workspaces and ACP state. A local snapshot shares the VPS failure domain. Preserve the Restic password, Element recovery key, Dot tunnel credential, and any GitHub credential through the authorized recovery process.
- **Restore.** Run the paired isolated restore check. Verify the snapshot, data, and original server identity, and keep restored and live copies of a Matrix device from running concurrently. Reconnect independently provisioned integrations and verify them before switching service. Exercise reboot and mobile alert delivery for the complete deployment during the agreed maintenance window.

## 5. Operate the optional Chief of Staff

Follow the [Chief of Staff guide](chief-of-staff.md) to enable direct observation, review the graph, and draft a persona. The direct reader, memory, and Workshop have separate health and acceptance checks. The optional [memory backup integration](chief-of-staff.md#recovery) extends the stack snapshot to Context Intelligence and Neo4j.

### Agent capability catalog

The VPS publishes a read-only `agents.json` alongside the resource dashboard. It contains the operator-verified role, capability codes, resource scopes, limitations, and current inventory state of each agent. External registration indicates that the agent is configured. The dashboard identifies unverified availability separately.

During onboarding, register the local agent through its installer, or run the existing external registration followed by `python3 /opt/matrix-stack/agent_catalog.py register AGENT PROFILE.json`. Supply `--name NAME --host HOST` when creating a new registry entry directly. Use the same command to update its profile, and `remove AGENT` during decommissioning. New local Compose workers read `/agent-catalog/agents.json` via `MATRIX_CATALOG_PATH` and mount the dashboard directory at `/agent-catalog:ro`. Each profile contains `role`, `limits`, and `capabilities`, where each capability has `code`, `description`, and `resources`. Use exact scopes such as `host:epyc100` or an authorized resource category such as `repo:*`. Existing credentials and runtime restrictions govern resource access. Keep each profile free of credentials.

Stage the updated workers, Dot, resource collector, and Chief of Staff on the VPS with `install-macai stage`. Verify the read-only catalog from the external host, then run `install-epyc100` there. Run `install-macai activate` on the VPS after both hosts are ready. `install-macai pause` switches new requests back to the legacy flow while versioned managed threads already in progress retain their routing rules. The stage command prints the backup location for rollback. Refresh the Dot plugin once to discover `list_agents` and the updated `publish_plan` input.

Run those commands from the [capability catalog release directory](../integrations/capability-catalog/). For the optional Epyc100 connector, first create `~/.local/share/epyc100-agent/matrix-catalog.json` as the external account. Use the operator's full local Matrix ID and that account's userspace Tailscale socket. Keep the file at mode `0600` and replace the example values.

```json
{
  "matrix_server_name": "matrix.example.ts.net",
  "matrix_owner": "@operator:matrix.example.ts.net",
  "tailscale_socket": "/home/worker/.local/state/tailscale/tailscaled.sock",
  "tailscale_binary": "/home/worker/.local/bin/tailscale"
}
```

To verify the rollout, register a fixture profile with a unique capability, route a synthetic task to it, and remove it from the registry after testing. Confirm that an out-of-scope target is rejected and an explicitly requested direct response stays in the original thread. Simulate a fresh capability gap and check the private Chief of Staff room. An unavailable worker is an operational blocker.

## 6. Pilot the optional Telegram integration

Use the matching source archive and follow the [Telegram operator guide](telegram.md). The first activation starts the bridge with an empty allowlist and the model connector stopped. Complete live acceptance on the verified DM before onboarding the group. Keep the Telegram application credentials in a private file and complete Codex device sign-in from a VPS terminal. Use the [implementation handoff](handoffs/telegram-constellation-amplifier.md) for deployment gates and recovery boundaries.

## 7. Apply the optional integration updates

1. On the matching base deployment, apply [Telegram Control for Dot](telegram.md#dot-and-chat-updates) before staging the capability catalog when using both integrations. The catalog installer carries forward the existing Dot Control mount. Both installers check their expected connector layout.
2. Stage and activate [capability routing](#agent-capability-catalog). Once the catalog is active, apply [delegated failure reporting](../integrations/delegated-failure/README.md) to Coder and Manager.
3. If the direct Chief of Staff and Context Intelligence are enabled, follow the [execution timeline guide](../integrations/context-execution/README.md). Back up the current memory before the cutover and verify both the new graph and the existing backups.
4. If Root is enabled, follow the [Root Operations guide](../integrations/root/README.md) to install the base, activate its Matrix and model sessions, then install its two ordered updates. Verify the approval and execution receipts in a disposable scenario before use.
