# TheArchitect

Select **TheArchitect** when configuring your team. In **Agents → Rooms → Control Room**, request **Diagnose services** or a scoped change. The same room has workspace shortcuts, diagnostics, alerts, and native Element approval polls. Task Manager can forward a verified Office request. Office receives status and a link to the private receipt.

Review the exact operation before voting. On approval, the control service executes the permitted action and confirms its outcome. It records maintenance time in Context Intelligence with separate approval and execution durations.

## Diagnose workspace services

1. In **Office**, mention **TheArchitect**. Ask **Diagnose services** for a local health overview or name a target such as **Diagnose synapse**, **Diagnose Context Intelligence**, **Diagnose Dot**, **Diagnose Telegram**, or **Diagnose backup.timer**. You can also describe the issue in the thread.
2. Review and vote **Accept** in **Control Room**. The broker inspects the deployment’s local Compose services and systemd units. A targeted check counts recognized signals in the last 30 minutes of logs, within the limits shown in the review. The private receipt records the findings. Office receives the operation ID and a link to the receipt.
3. If the receipt points to a fault, mention TheArchitect again in the same thread with `operation <id>` and ask for a repair. Review the repair and its verification in a new poll. For a blocked Telegram draft, use Task Manager to repeat the draft after the repair. An upstream service or an engine on a worker needs a check in its own environment.

If Amplifier produces an incomplete proposal, TheArchitect asks it to correct the validation error once before reporting a failure. The service overview and direct diagnostic requests use the same typed check without a model-generated shell script.

## Repository maintenance and automatic recovery

From a committed checkout on the VPS with TheArchitect active, enable maintenance and choose which local Office connectors it may recover. The first command opens a checklist. The second enables a single connector by ID. Use `--no-auto-restarts` to enable repository releases alone.

```bash
./constellation_matrix maintenance enable
./constellation_matrix maintenance enable --restart-service task-manager
```

For unattended installs, set `maintenance: true` and optionally supply `maintenance_restart_services` as a list of agent IDs. An empty list leaves recovery disabled. Repeat `--restart-service` to select multiple connectors. Each run of `enable` sets the complete list. Managed system services, remote agents, and management connectors stay outside automatic recovery.

### Release a repository change

1. In **Agents → Office**, mention **TheArchitect** and ask it to propose a small dashboard or Office connector change. For example, “Change the dashboard page title to Office status.”
2. TheArchitect edits a per-request copy of the deployed source. The broker checks the diff and runs connector and dashboard tests in a container with networking disabled. The release supports edits to existing dashboard HTML, CSS and JavaScript or Office connector JavaScript, up to 5,000 bytes of diff. Installer, Compose, permissions, dependencies, and host service changes go through the VPS terminal.
3. In **Control Room**, review the exact diff, revision, and affected services. Vote **Accept** to apply it. The broker verifies replacement connectors and attempts to restore their previous images and dashboard if verification fails. If the active revision changed while you were reviewing, start a new request.
4. Read the Office receipt and check the active revision on the dashboard. Use the operation ID from the receipt to export the diff for a normal Git review.

   ```bash
   ./constellation_matrix maintenance export <operation-id> > workspace-change.diff
   ```

If automatic rollback reports that recovery is required, inspect the affected services and retry rollback using the same operation ID.

```bash
./constellation_matrix maintenance rollback <operation-id>
```

### Recover an unhealthy connector

The broker checks the same container twice, at least 30 seconds apart, for a Docker health failure or an out-of-memory exit. It leaves deliberately stopped services alone, waits at least 15 minutes between attempts for each connector, and records the outcome in **Control Room**. Pause recovery before planned service work and resume it afterward.

```bash
./constellation_matrix maintenance pause-recovery
./constellation_matrix maintenance resume-recovery
```

The host broker uses standard Docker. The TheArchitect container receives a draft directory and a dedicated approval connection. The installer can show optional `docker` group instructions for your Linux login. Docker group membership gives that login root-equivalent access and is unnecessary for broker-managed recovery.

## Verification

Run `bash integrations/root/tests/run` for the disposable encrypted Matrix approval and host broker fixtures. Run `bash tests/synapse-smoke` for account migration and private room checks. Run `python3 integrations/root/tests/maintenance-docker` with `MAINTENANCE_TEST_IMAGE` set to a locally built connector image to exercise a sealed release and an unhealthy connector recovery in isolated Docker containers.

## Start a digital twin

In **Control Room**, ask TheArchitect to **start a digital twin**. Follow the response to list models for a connected account. Send the `twin create` command with the selected model, reasoning effort, and comma-delimited agent IDs. The approval shows the snapshot revision and duration. The twin starts with a separate Matrix workspace and temporary provider grants.

```sh
./constellation_matrix twin list
./constellation_matrix twin status <id>
./constellation_matrix twin access <id>
./constellation_matrix twin stop <id>
```

The status command shows the loopback Matrix port. Use SSH forwarding and retrieve the private test login with `twin access`. Stop and expiration revoke the provider grants.

## Operation timings

The **Operation timings** dashboard section shows 7- and 30-day aggregates. Work measures execution observed by a producer. Queue and approval durations are shown separately when known. The weekly Monday report posts to **Control Room** at or after 10:00 in the deployment timezone. Use the twin filter to explore measurements for isolated runs.
