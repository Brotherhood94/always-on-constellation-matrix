# Deployment and recovery runbook

The stages below record the deployment handoff for future deployment. Confirm the paired source archives, their installation instructions, and support for this VPS before execution. The references to entry points are for operator planning.

## Preparation

1. Obtain the paired `matrix-stack` and `matrix-agents-pilot` source archives. Verify availability, provenance, and the paired revisions against the authorized distribution. Before any privileged install stage, confirm the installer entry points and syntax, supported configuration schema, and required artifacts in those revisions.
2. Choose the permanent Synapse `server_name` before installation. Record deployment parameters in an operator-controlled location.
3. Verify tailnet membership and private routing, HTTPS readiness for 443 and 8443, and cgroup v2 on the target host.

| Parameter | Placeholder | Purpose |
| --- | --- | --- |
| Synapse server name | `<SERVER_NAME>` | Permanent homeserver identity |
| Owner handle | `<OWNER_HANDLE>` | Owner input to the stack installer |
| Worker Matrix IDs | `<AGENT_MATRIX_ID>` | Repeat for Analyst, Reviewer, and Coder room invitations |
| Dot Matrix ID | `<DOT_MATRIX_ID>` | Invite the separate Dot device's account to the agent room |
| Tunnel ID | `<TUNNEL_ID>` | Associate the bridge with the authorized tunnel |
| Task ID | `<TASK_ID>` | Reuse the same opaque ID for retries of a single publication |

## Matrix stack and workers

1. From the root console, use the source-provided `matrix-stack` install stage with the owner handle and complete `verify-phase1`.
2. Onboard a desktop with Element Desktop and a mobile device with Element X. Verify the intended accounts and devices, encrypted room access, and receipt of an encrypted alert.
3. Use the `matrix-agents` install stages in order. The reported entry points are `install-analyst`, `install-reviewer`, and `install-agent coder`. Invite the worker Matrix IDs to the agent room.
4. Connect each worker using the source-provided `/opt/matrix-agents/connect-codex <agent>` entry point and permitted device authentication. Verify the Codex-backed ACP connection and room dispatch for each worker.

## Dot

1. Obtain the official OpenAI Secure MCP Tunnel v0.0.21 binary and verify its official checksum. Confirm the Dot deployment inputs through the authorized instructions before proceeding.
2. Set up the root-protected `/etc/matrix-dot` and the authenticated Unix bridge socket. The reported association entry point is `/opt/matrix-dot/connect-tunnel <TUNNEL_ID>`. The reported grants were Tunnels Read + Manage for association and Tunnels Read + Use for the runtime credential. Confirm the current permission labels and grants before provisioning.
3. Invite the separate Dot Matrix identity to the agent room and configure the private workspace connector. Verify tunnel and bridge authentication independently, then check the scoped operations from [Architecture](architecture.md) and record the result in [Acceptance](acceptance.md).

## Optional Coder GitHub integration

Give Coder a dedicated runtime credential. Use the integration installer from the paired source for setup, rotation, and recovery if it is available. Confirm its entry point and procedure in that revision before configuring the integration. Keep the runtime credential outside Restic and recover it separately through the credential provisioner. Broader repository access supports account-wide operations and increases the impact of compromise. Where the intended operations permit it, prefer access limited to selected repositories and the minimum required permissions. The reported connector blocks repository deletion, and the reported runtime token lacks repository deletion permission. Those controls apply to Coder's integration. GitHub Actions credentials have their own permissions.

## Monitoring and recovery

- Check `matrix-stack.service`, `matrix-monitor.service`, `matrix-backup.timer`, and `matrix-dot-tunnel.service` where Dot is present. The reported readiness endpoint is loopback TCP 18786, path `/readyz`.
- CPU and RAM are sampled every minute via cgroup v2. The trigger and clear thresholds are 50% and 45%, with five samples. Disk is sampled hourly with trigger and clear thresholds of 25% and 22.5%. The alert outbox is durable and encrypted. Monitoring applies no resource caps.
- Restic stores backups locally in `/var/backups/matrix`. The reported schedule is nightly at 03:30 in the configured deployment time zone with retention of seven successful snapshots. Reported coverage includes a PostgreSQL dump, Synapse identity and media, and encrypted bot state and queues. Verify post-Dot/Coder coverage as an open acceptance check.
- The local repository shares the VPS failure domain. The tunnel runtime key is external to the backup and must be recovered separately through the authorized credential process.
- Restore into isolation and confirm snapshot coverage and device isolation before starting restored components. Keep existing and restored Matrix devices from running concurrently. Validate service readiness, Synapse identity and media, encrypted rooms, worker state and queued work, and the separately recovered Dot connection before returning to service.
