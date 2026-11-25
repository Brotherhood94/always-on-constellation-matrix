# Install and operate Constellation

## Initial setup

On Ubuntu 24.04, install standard Docker Engine with Compose V2, Python 3, curl, and Restic. For Tailscale Serve, join the VPS to your tailnet, enable HTTPS certificates, and use the node's tailnet DNS name for Matrix. Serve gives authorized devices private HTTPS access to the deployment. [Docker Engine](https://docs.docker.com/engine/install/ubuntu/) · [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

```sh
sudo ./constellation_matrix install
sudo ./constellation_matrix agent configure
./constellation_matrix status
```

Use ↑ and ↓ to browse the agent checklist and inspect each agent’s role, suggested model, capabilities, and limits. Press Enter to select or deselect the highlighted agent. Move to **Done** and press Enter to confirm the roster. An empty selection provisions the workspace so you can add agents later.

Choose the suggested Tailscale DNS name if available. On the next screen, type the Matrix username you want to use in Element and press Enter. When rerunning setup, press Enter with the field empty to keep the existing username. Enter the Matrix operator password when prompted. Terminals without full-screen support use line prompts.

At the end of installation, choose **Open Amplifier provider setup** to configure the local agents one at a time. Each agent runs Amplifier’s own provider manager. It offers Anthropic for Claude, OpenAI with an API key, and ChatGPT for Codex sign-in. Add or edit a provider, follow its login prompts, pick the model, and choose **Done**. Amplifier keeps credentials and settings in that Instance’s private persistent home. Constellation activates the agent after its request and session-resume checks pass. [Amplifier provider setup](https://github.com/microsoft/amplifier-app-cli#readme)

Run `agent configure` later to continue with pending agents, or pass an agent ID to configure a single agent. Reconfiguring an active agent temporarily pauses its connector. Existing `agent sign-in` commands open the same setup flow. Remote workers configure Amplifier during enrollment on their own host.

State goes in `/srv/constellation-matrix` by default. Use `--data-dir` before the subcommand to select a dedicated directory for another deployment. Give that deployment a different Compose project and local ports. In config-file mode, keep the owner password file outside the checkout with mode `0600`. Resolve relative paths from the directory where you run the command. Use `./constellation_matrix validate --config /path/to/install.json` to check the configuration before installation.

`./constellation_matrix status` reads dashboard snapshots as a regular user. Run `sudo ./constellation_matrix status --refresh` to publish current Docker inventory.

The default private endpoints use HTTPS 443 for Matrix, 8443 for the dashboard, 8444 for Neo4j Browser, and TLS TCP 17687 for Bolt. For a different private proxy, set `tailscale` to false and provide the dashboard and browser URLs in the install config. Serve the deployment’s `dashboard/` directory and proxy to the configured loopback ports, which default to 18008 for Matrix, 17474 for Neo4j Browser, and 17688 for Bolt.

Sign in to Element with your Matrix operator account. The Agents Space links Office, VPS Alerts, and the private agent chats you selected. In Element Web or Desktop, disable **Show all rooms** in **Settings → Sidebar** if you want rooms in Spaces omitted from your Home view. The monitor publishes its shortcuts once it sees the operator in VPS Alerts. Sign in to Neo4j Browser as `neo4j` using the deployment’s local `context/neo4j-password` file.

## Boot and service placement

| Where | Services and startup |
| --- | --- |
| Docker Compose | Synapse, PostgreSQL, Neo4j, Context Intelligence, the private proxy, Persona Workshop, agent Instances and connectors, the VPS Alerts bot, and optional Dot bridge, Telegram and desktop SFTP containers. Docker uses restart policies, and the workspace’s systemd unit starts the primary Compose project at boot. |
| Tailscale | The optional host daemon serves the static dashboard directory and proxies private endpoints. The installer enables the daemon when Tailscale is selected. |
| systemd | The resource collector reads host cgroups. The backup timer coordinates consistent Docker and filesystem snapshots. The Root broker uses a host socket for approved host commands. The optional Dot tunnel uses a host service with systemd credentials for its private socket connection. |

The installer enables Docker, containerd, the stack unit, the collector, and the backup timer. Root’s socket is enabled when Root is activated. Dot’s tunnel service is enabled by `dot-connect`. Enable Telegram’s optional scheduled jobs separately with `sudo ./constellation_matrix telegram enable-operations`.

```sh
systemctl is-enabled docker.service containerd.service
systemctl is-enabled constellation-matrix-stack.service constellation-matrix-monitor.service constellation-matrix-backup.timer
sudo ./constellation_matrix status --refresh
```

Use your configured project name in the unit names. After a staging reboot, confirm the signed-in agents and integrations return to service, then run a small Office task. [Docker boot setup](https://docs.docker.com/engine/install/linux-postinstall/#configure-docker-to-start-on-boot-with-systemd) · [Deployment scenarios](acceptance.md#post-deployment-scenarios)

## Add a worker

Save a profile like the one below. Use capability names and resource scopes that describe the work its authorized resources actually support.

```json
{
  "key": "qa",
  "name": "QA",
  "model": "gpt-6.2-sol",
  "effort": "medium",
  "profile": {
    "role": "worker",
    "limits": "Verify artifacts provided through Office or the shared desktop.",
    "capabilities": [
      {"code": "verify-artifact", "description": "Run an independent artifact check.", "resources": ["task:*"]}
    ]
  }
}
```

```sh
sudo ./constellation_matrix agent add --profile /path/to/qa.json
sudo ./constellation_matrix agent configure qa
```

The registry has one active coordinator and observer when those roles are enabled. Pending workers become available for routing after provider sign-in, an ACP session restore probe, and connector startup.

## Add a remote worker

Select `remote-worker` during setup or add a profile with `"placement": "remote"`. Generate a dedicated Ed25519 SSH key on the remote host for the desktop. On the coordinator, approve its public key and export the enrollment file.

```sh
sudo ./constellation_matrix remote desktop --public-key /path/to/remote-worker.pub
sudo ./constellation_matrix remote export remote-worker --host worker.example --output /root/worker-enrollment.json
```

Transfer the enrollment file privately. On the worker, install Docker, Tailscale, and SSHFS. Verify the desktop server’s SSH host key against the key reported by `remote desktop`. Mount `desktop-share@<tailnet-host>:/desktop` on the remote host over port 2222 with `allow_other,default_permissions,uid=991,gid=991`. Configure the SSHFS mount to come up before Docker after a reboot. The key only supports SFTP to the shared desktop.

```sh
sudo ./constellation_matrix --data-dir /srv/constellation-worker remote attach \
  --enrollment /root/worker-enrollment.json --desktop-mount /mnt/constellation-desktop \
  --workshop-host '<remote-worker-tailnet-dns-name>'
```

Transfer the resulting `workshop-token`, `workshop-ca.pem`, and `receipt.json` to the coordinator through a private channel. Enroll the live Instance in Workshop with the reported HTTPS management origin.

```sh
sudo ./constellation_matrix persona-connect remote-worker \
  --url 'https://<remote-worker-tailnet-dns-name>:19444' \
  --token-file /root/workshop-token --ca-file /root/workshop-ca.pem
```

Then run `sudo ./constellation_matrix remote confirm remote-worker --receipt /path/to/receipt.json`. Verify a real Office handoff and a file round trip in both directions. The coordinator shows the remote worker as **Registered** after confirmation. Keep tailnet access scoped to the selected operator devices and worker hosts.

## Back up and recover

```sh
sudo ./constellation_matrix backup
sudo ./constellation_matrix restore-check
```

The checkpoint briefly stops the deployment’s running services to capture consistent PostgreSQL, Neo4j, Matrix, agent, and desktop state. The encrypted Restic repository keeps seven successful checkpoints. Keep the Restic password outside the VPS’s failure domain. The restore check verifies an unpacked copy in an isolated directory.

For a fresh live cutover, first test a separate installation with a different project name, data directory, and local ports. Leave `tailscale` and `systemd` off for the isolated fixture. Before stopping the current deployment, inventory its services, data, mounts, and Serve routes and prove that you can restore an encrypted backup. Switch only the routes owned by this deployment. Verify the fresh setup before removing the superseded state.

## Uninstall

```sh
sudo ./constellation_matrix uninstall
```

Pass the original `--data-dir` before `uninstall` for a separate workspace. This stops and removes the selected Compose projects, disables their installed systemd units, and removes matching Tailscale Serve routes. It keeps the deployment directory, Matrix state, agent workspaces, and encrypted backups for recovery. A reinstall using the same configuration can reuse that state. Reconnect optional integrations through their respective setup commands.

For a remote worker, run the same uninstall command on the worker with its own data directory. Then unmount its SSHFS desktop and remove its mount entry if you created one. Remove the worker's desktop key and revoke its access when retiring it permanently.
