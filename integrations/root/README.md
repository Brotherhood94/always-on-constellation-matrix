# VPS Manager

Select **VPS Manager** during guided installation on a systemd host. Use `sudo ./constellation_matrix agent configure vps-manager` to finish or resume Amplifier sign-in. For a workspace with the legacy Root agent, follow the [migration steps](../../docs/runbook.md#upgrade-from-root-to-vps-manager).

1. In **Agents → Office**, choose **VPS Manager** from Element’s mention picker and ask for `matrix-stack-status`. You can also ask Manager to delegate the same check. A handoff must trace back to an Office assignment.
2. Open **Agents → Rooms → Root Operations**. VPS Manager posts a request notice, the exact proposal, and an **Accept / Reject** poll. Review the target host, commands or file contents, verification, and recovery before voting. The first valid operator vote applies to that proposal. Polls expire after 15 minutes.
3. Read the full audit in Root Operations. The originating Office thread receives the outcome, an operation ID, and a link to the private review. Manager can use the Office receipt to verify the overall assignment.

VPS Manager prepares the proposal in its own Amplifier Instance. A separate host broker executes the approved plan with root privileges. Host requests require a new vote, including read-only inspection. An interrupted execution with an unknown outcome requires the operator to check the host before approving a new operation.

| Operation | What the operator reviews |
| --- | --- |
| Inspect one file | The canonical absolute path. The private result contains a SHA-256 fingerprint and a bounded, redacted text preview. |
| Replace one file | The exact previous and replacement text, plus a separate verification command. The broker checks the previous bytes, saves the original privately, and replaces the file atomically. On verification failure it restores the original if the replacement still matches. |
| Release a repository patch | The tested diff, its SHA-256, the current and proposed revisions, and the local connectors to update. |
| Run a host script | The exact Bash script and separate verification script. Both run as root. Review the proposal’s recovery instructions before approval. |

Keep credentials out of Office messages and proposal text. For an edit based on a completed inspection, refer to `operation <id>` in a new mention in the same Office thread. VPS Manager can use the approved private preview when drafting the next proposal. Changes to credentials should reference host-protected files rather than copying their contents into Matrix.

## Repository maintenance and automatic recovery

From a committed checkout on the VPS with VPS Manager active, enable maintenance and choose which local Office connectors it may recover. The first command opens a checklist. The second enables a single connector by ID. Use `--no-auto-restarts` to enable repository releases alone.

```bash
sudo ./constellation_matrix maintenance enable
sudo ./constellation_matrix maintenance enable --restart-service task-manager
```

For unattended installs, set `maintenance: true` and optionally supply `maintenance_restart_services` as a list of agent IDs. An empty list leaves recovery disabled. Repeat `--restart-service` to select multiple connectors. Each run of `enable` sets the complete list. Managed system services, remote agents, and management connectors stay outside automatic recovery.

### Release a repository change

1. In **Agents → Office**, mention **VPS Manager** and ask it to propose a small dashboard or Office connector change. For example, “Change the dashboard page title to Office status.”
2. VPS Manager edits a per-request copy of the deployed source. The broker checks the diff and runs connector and dashboard tests in a container with networking disabled. The release supports edits to existing dashboard HTML, CSS and JavaScript or Office connector JavaScript, up to 5,000 bytes of diff. Installer, Compose, permissions, dependencies, and host service changes go through the VPS terminal.
3. In **Root Operations**, review the exact diff, revision, and affected services. Vote **Accept** to apply it. The broker verifies replacement connectors and attempts to restore their previous images and dashboard if verification fails. If the active revision changed while you were reviewing, start a new request.
4. Read the Office receipt and check the active revision on the dashboard. Use the operation ID from the receipt to export the diff for a normal Git review.

   ```bash
   sudo ./constellation_matrix maintenance export <operation-id> > workspace-change.diff
   ```

If automatic rollback reports that recovery is required, inspect the affected services and retry rollback using the same operation ID.

```bash
sudo ./constellation_matrix maintenance rollback <operation-id>
```

### Recover an unhealthy connector

The broker checks the same container twice, at least 30 seconds apart, for a Docker health failure or an out-of-memory exit. It leaves deliberately stopped services alone, waits at least 15 minutes between attempts for each connector, and records the outcome in **Root Operations**. Pause recovery before planned service work and resume it afterward.

```bash
sudo ./constellation_matrix maintenance pause-recovery
sudo ./constellation_matrix maintenance resume-recovery
```

The host broker uses standard Docker. The VPS Manager container receives a draft directory and a dedicated approval connection. The installer can show optional `docker` group instructions for your Linux login. Docker group membership gives that login root-equivalent access and is unnecessary for broker-managed recovery.

## Verification

Run `bash integrations/root/tests/run` for the disposable encrypted Matrix approval and host broker fixtures. Run `bash tests/synapse-smoke` for account migration and private room checks. Run `python3 integrations/root/tests/maintenance-docker` with `MAINTENANCE_TEST_IMAGE` set to a locally built connector image to exercise a sealed release and an unhealthy connector recovery in isolated Docker containers.
