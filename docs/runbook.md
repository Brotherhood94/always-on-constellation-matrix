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
3. Check that each worker is available in the dashboard. Confirm its separate workspace and persistent private state under `/srv/agents/<agent>`. Run the applicable checks in [Acceptance](acceptance.md#checks-for-each-deployment).
4. For a separate external worker, install its host-specific Constellation Agent Instance and Matrix connector under a dedicated Linux user. Enable the applicable user services for startup. Register the agent identity in the shared router and dashboard inventory, join `Agents`, and verify an encrypted round trip and a managed handoff. Use the external deployment source and preserve per-host workspace, credentials, and backup scope.

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
