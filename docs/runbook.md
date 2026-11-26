# Install and operate Constellation

## Initial setup

On Ubuntu 24.04, install standard Docker Engine with Compose V2, Python 3, curl, and Restic. For Tailscale Serve, join the VPS to your tailnet, enable HTTPS certificates, and use the node's tailnet DNS name for Matrix. Serve gives authorized devices private HTTPS access to the deployment. [Docker Engine](https://docs.docker.com/engine/install/ubuntu/) · [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

```sh
./constellation_matrix install
```

Choose the suggested Tailscale DNS name if available. On the next screen, type the Matrix username you want to use in Element and press Enter. When rerunning setup, press Enter with the field empty to keep the existing username. Terminals without full-screen support use line prompts.

The workspace and integrations checklist opens with **Done** highlighted. On a fresh install, **Element workspace**, **Telegram**, and **Dot** are checked. Use the arrow keys and Enter to toggle Telegram or Dot, then choose **Done**. Element workspace is required. Reruns start with the integrations already saved for that workspace.

Enter a Matrix operator password of at least 12 characters, then re-enter it on the confirmation screen. Both fields mask what you type. Save it for signing in to Element. On a fresh install, choose and confirm a separate Context Intelligence password for Neo4j Browser. Use at least 12 printable characters and avoid `/` and a leading `-`.

After the workspace boots, sign in to Element and confirm sign-in in the terminal. Then use ↑ and ↓ to browse the agent checklist and Enter to toggle agents. Choose **Done** to register the roster. An empty choice lets you add agents later. [Guided setup](setup-guide.md)

The installer starts the selected local Amplifier Instances together and opens provider setup one at a time. The dashboard shows **Sign-in required** for pending agents. Use the following command to resume an interrupted provider setup.

```sh
./constellation_matrix agent configure
```

Each agent runs Amplifier’s own provider manager. It offers Anthropic for Claude, OpenAI with an API key, and ChatGPT for Codex sign-in. The terminal clears between provider selection, credential prompts, sign-in, and model selection. Add or edit a provider, follow its login prompts, pick the model, and choose **Done**. Amplifier keeps credentials and settings in that Instance’s private persistent home. Constellation starts the agent’s Matrix connector after its request and session-resume checks pass. [Amplifier provider setup](https://github.com/microsoft/amplifier-app-cli#readme)

Check the agent name at the top of each page. **Add a provider** creates an additional connection for that agent. Choose **Done** to verify that agent and move to the next one in batch setup. Each agent can use a different provider and model.

After all selected agents are active and joined in Matrix, the installer publishes the welcome and prints its link. Chief of Staff uses its private chat when selected. Otherwise, a ready Office agent posts in Office. Remote workers complete enrollment on their own host and must be confirmed on the coordinator before the welcome. Run `./constellation_matrix setup-guide` to resume setup or retry a pending welcome. Append `--print` for the sign-in instructions and guide locations.

Pass an agent ID to `agent configure` to configure a single agent. Reconfiguring an active agent temporarily pauses its connector. Existing `agent sign-in` commands open the same setup flow.

State goes in `/srv/constellation-matrix` by default. Use `--data-dir` before the subcommand to select a dedicated directory for another deployment. Give that deployment a different Compose project and local ports. In config-file mode, provide `owner_password_file` and optionally `neo4j_password_file`. Keep both files outside the checkout with mode `0600`. The installer generates a Neo4j password when the file is omitted and preserves an existing Neo4j password on reruns. Resolve relative paths from the directory where you run the command. Use `./constellation_matrix validate --config /path/to/install.json` to check the configuration before installation.

## Host privileges

Run `install`, `setup-guide`, and `agent configure` from a regular shell. The CLI invokes sudo for the deployment steps that use Docker, protected files, or system services. Matrix operator sign-in for roster registration and Telegram layout runs in your regular session. Amplifier provider setup runs as the container’s ordinary user.

`status`, `telegram status`, and `setup-guide --print` read published information without sudo. Upgraded deployments can publish the setup information once with `sudo ./constellation_matrix status --refresh`. The same command refreshes Docker inventory. Pass your usual `--data-dir` when using a custom deployment directory.

Use explicit sudo for administrative operations such as uninstall, backup, host timers, remote mounts, and Dot tunnel installation. This workflow keeps standard Docker and the existing Docker socket permissions.

The default private endpoints use HTTPS 443 for Matrix, 8443 for the dashboard, 8444 for Neo4j Browser, and TLS TCP 17687 for Bolt. For a different private proxy, set `tailscale` to false and provide the dashboard and browser URLs in the install config. Serve the deployment’s `dashboard/` directory and proxy to the configured loopback ports, which default to 18008 for Matrix, 17474 for Neo4j Browser, and 17688 for Bolt.

## Connect in Element

1. Download and install Element Desktop on your computer. If you selected Tailscale Serve, connect that computer to your tailnet. [Download Element](https://element.io/download)
2. Open Element Desktop and choose **Sign in** on the welcome screen. Under **Account provider**, find the current server (usually `matrix.org`) and click **Edit** to its right, above **Continue**.
3. In **Choose your account provider**, paste the installer’s HTTPS Matrix homeserver URL into **Other account provider** and select **Continue**.
4. Back on the sign-in page, confirm **Account provider** matches your homeserver. Enter your Matrix operator credentials in **Username** and **Password**, then select **Sign in**. [Element onboarding](https://docs.element.io/latest/element-support/quick-start-guide/onboarding/)
5. In the left sidebar, open **Agents → Office**. The Space also links VPS Alerts and the private agent chats you selected. Open **Agents → VPS Alerts → Pinned messages** and look for **Workspace shortcuts**, which contains both links. The monitor publishes it after the operator joins VPS Alerts and Element registers its encryption keys. It refreshes the pin when the operator’s devices change.

In Element Web or Desktop, disable **Show all rooms** in **Settings → Sidebar** if you want rooms in Spaces omitted from your Home view.

## Sign in to Context Intelligence

Open **Context Intelligence** from the dashboard or VPS Alerts to launch Neo4j Browser. Enter `neo4j` and your Context Intelligence password, then select **Connect**. You can retrieve the stored password privately on the VPS.

```sh
sudo cat -- /srv/constellation-matrix/context/neo4j-password
```

Find the deployment URLs and the password retrieval command for your data directory in **Agents → VPS Alerts → Pinned messages → Workspace shortcuts**.

## Connect Dot and Telegram

For enabled integrations, open **Agents → VPS Alerts → Pinned messages → Connect Dot** and **Telegram → Telegram Control → Pinned messages → Set up Telegram** in Element. Set up Dot with an OpenAI Secure MCP Tunnel, then connect the private app in ChatGPT. For Telegram, prepare the bridge, sign in from its Element DM, and verify a chat. Follow the Telegram guide to bind that chat to the drafting agent for drafts and updates. [Dot setup](dot.md) · [Telegram setup](telegram.md)

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
sudo ./constellation_matrix agent add --profile /path/to/qa.json --configure
```

Run the command from the VPS checkout. Omit `--configure` to provision first, then use the command below to finish or resume native Amplifier sign-in.

```sh
./constellation_matrix agent configure qa
```

The registry has one active coordinator and observer when those roles are enabled. Pending workers become available for routing after provider sign-in, an ACP session restore probe, and connector startup.

### Retire a worker

```sh
sudo ./constellation_matrix agent remove qa
```

Confirm by typing the worker ID. Automation can pass `--yes`. The worker stops accepting assignments while it finishes accepted work. If the terminal reports that it is still draining, check the worker and run the same command to resume. Chief of Staff management also enables background retries.

Retirement removes the worker from Office, the dashboard, Docker Compose, and Persona Workshop. It revokes the Matrix account and Context Intelligence key. The worker’s private archive is under `archive/agents/qa` in the deployment data directory. Office history, Context Intelligence evidence, and the shared desktop remain available. Use a fresh ID when hiring a replacement, and remove the old ID from any install config that declares it.

Retirement supports ordinary local Office workers. [Chief of Staff approval](chief-of-staff.md#hire-or-retire-an-office-worker)

### Upgrade from Root to VPS Manager

From the updated VPS checkout, run the migration, then complete VPS Manager’s native Amplifier sign-in.

```sh
sudo ./constellation_matrix agent migrate-root
./constellation_matrix agent configure vps-manager
```

Confirm the migration by typing `migrate`. Use the same migration command to resume if interrupted. The old Root account is deactivated, its pending approvals are superseded, and its audit archive stays on the host. VPS Manager joins the existing encrypted **Root Operations** chat with a new Matrix identity. Update saved install configurations to use `vps-manager` where they previously listed `root`. [VPS Manager guide](../integrations/root/README.md)

## Add a remote worker

The VPS runs the workspace and holds the shared desktop. The worker runs its own Amplifier Instance and Matrix connector. Use the same repository revision on both Linux hosts. The commands below assume the default VPS data directory and Ubuntu 24.04 on the worker. Replace the example tailnet DNS names with those of your machines. If the VPS uses a custom data directory, pass `--data-dir` before each VPS subcommand.

Both hosts need to be on the same tailnet. The worker needs access to the VPS’s Matrix, dashboard, and Context Intelligence endpoints, plus TCP 2222 for desktop SFTP. The VPS needs access to TCP 19444 on the worker for Persona Workshop. The file transfer examples use your normal SSH login to the VPS.

### 1. Worker · Prepare the host and desktop key

Install standard Docker Engine with Compose V2 and Tailscale on the worker. Join the tailnet before continuing. Use the same checkout as the VPS. [Docker Engine](https://docs.docker.com/engine/install/ubuntu/) · [Tailscale installation](https://tailscale.com/docs/install)

Install Python and SSHFS, then generate a dedicated key for the boot-time mount. Reuse that key on retries. Send only the public key to the VPS. Set `vps_login` to your VPS SSH login and keep the same worker shell for the following steps.

```sh
sudo apt-get update
sudo apt-get install -y python3 sshfs
vps_login='operator@vps.example.ts.net'
handoff="$HOME/.constellation-remote-worker"
install -d -m 0700 "$handoff"
sudo install -d -m 0700 /etc/constellation-desktop
sudo ssh-keygen -t ed25519 -N '' -C constellation-desktop \
  -f /etc/constellation-desktop/id_ed25519
sudo cat /etc/constellation-desktop/id_ed25519.pub > "$handoff/desktop.pub"
ssh "$vps_login" 'install -d -m 0700 "$HOME/.constellation-remote-worker"'
scp "$handoff/desktop.pub" "$vps_login:.constellation-remote-worker/"
```

### 2. VPS · Authorize the key and export enrollment

If you selected **Remote Worker** during setup, its profile is already registered. If you skipped it, run the following from the VPS checkout to register the built-in preset.

```sh
handoff="$HOME/.constellation-remote-worker"
(umask 077; python3 - <<'PY' > "$handoff/profile.json"
import json
from pathlib import Path

preset = json.loads(Path('deploy/matrix/agent-profiles.json').read_text())['remote-worker']
print(json.dumps({**preset, 'key': 'remote-worker', 'placement': 'remote'}, indent=2))
PY
)
sudo ./constellation_matrix agent add --profile "$handoff/profile.json"
```

Authorize the worker key and copy the desktop host public key printed by `remote desktop` for use in the next step. The enrollment file contains the worker’s scoped Matrix and Context Intelligence credentials. Keep it outside the checkout with mode `0600`.

```sh
handoff="$HOME/.constellation-remote-worker"
worker_host='worker.example.ts.net'
sudo ./constellation_matrix remote desktop --public-key "$handoff/desktop.pub"
sudo ./constellation_matrix remote export remote-worker \
  --host "$worker_host" --output "$handoff/enrollment.json"
sudo chown "$(id -u):$(id -g)" "$handoff/enrollment.json"
```

### 3. Worker · Pin the VPS key, mount the desktop, and attach

Fetch the private enrollment over SSH. Substitute the actual VPS tailnet DNS name and the full `ssh-ed25519 …` public host key from the VPS terminal. This pins the dedicated SFTP endpoint before the worker connects.

```sh
scp "$vps_login:.constellation-remote-worker/enrollment.json" "$handoff/enrollment.json"
chmod 0600 "$handoff/enrollment.json"
vps_host='vps.example.ts.net'
desktop_host_key='ssh-ed25519 REPLACE_WITH_THE_VPS_DESKTOP_HOST_PUBLIC_KEY'
printf '[%s]:2222 %s\n' "$vps_host" "$desktop_host_key" \
  | sudo tee /etc/constellation-desktop/known_hosts >/dev/null
sudo chmod 0600 /etc/constellation-desktop/known_hosts
sudo install -d -m 0755 /mnt/constellation-desktop
```

On a dedicated worker, add this line to `/etc/fstab` with `sudoedit /etc/fstab`, using the same VPS tailnet DNS name. UID and GID `991` let the worker containers use the mount. [SSHFS mount options](https://github.com/libfuse/sshfs/blob/master/sshfs.rst) · [systemd mount options](https://www.freedesktop.org/software/systemd/man/latest/systemd.mount.html)

```fstab
desktop-share@vps.example.ts.net:/desktop /mnt/constellation-desktop fuse.sshfs noauto,_netdev,x-systemd.requires=tailscaled.service,port=2222,IdentityFile=/etc/constellation-desktop/id_ed25519,IdentitiesOnly=yes,UserKnownHostsFile=/etc/constellation-desktop/known_hosts,StrictHostKeyChecking=yes,BatchMode=yes,allow_other,default_permissions,uid=991,gid=991,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 0 0
```

Open `sudo systemctl edit docker.service` and add this drop-in. On boot, Docker waits for the desktop mount before it starts the worker containers. [systemd mount dependencies](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#RequiresMountsFor=)

```ini
[Unit]
RequiresMountsFor=/mnt/constellation-desktop
```

Mount the desktop, then run `remote attach` from the worker checkout. Complete the Amplifier provider and model prompts on the worker. Use that worker’s actual Tailscale DNS name for `--workshop-host`.

```sh
sudo systemctl daemon-reload
sudo mount /mnt/constellation-desktop
findmnt --mountpoint /mnt/constellation-desktop
worker_host='worker.example.ts.net'
sudo ./constellation_matrix --data-dir /srv/constellation-worker remote attach \
  --enrollment "$handoff/enrollment.json" --desktop-mount /mnt/constellation-desktop \
  --workshop-host "$worker_host"
```

Send the generated receipt, dedicated Workshop token, and TLS CA back to the VPS. The originals stay in the worker’s data directory.

```sh
for artifact in receipt.json workshop-token workshop-ca.pem; do
  sudo install -m 0600 -o "$(id -u)" -g "$(id -g)" \
    "/srv/constellation-worker/$artifact" "$handoff/$artifact"
done
scp "$handoff/receipt.json" "$handoff/workshop-token" "$handoff/workshop-ca.pem" \
  "$vps_login:.constellation-remote-worker/"
```

### 4. VPS · Connect Persona Workshop and confirm the worker

Use the HTTPS management origin printed by `remote attach`. `remote confirm` checks the enrollment device receipt and the worker’s Workshop connection before registering it for Office routing.

```sh
handoff="$HOME/.constellation-remote-worker"
worker_host='worker.example.ts.net'
sudo ./constellation_matrix persona-connect remote-worker \
  --url "https://$worker_host:19444" \
  --token-file "$handoff/workshop-token" --ca-file "$handoff/workshop-ca.pem"
sudo ./constellation_matrix remote confirm remote-worker --receipt "$handoff/receipt.json"
sudo ./constellation_matrix status --refresh
```

Check that **Remote Worker** appears as **Registered**. In Office, ask a local worker to write a test file under `desktop/`, then ask Manager to delegate reading and appending a line to **Remote Worker**. Have the local worker read the result back. After the next worker reboot, check the mount with `findmnt --mountpoint /mnt/constellation-desktop` and repeat the Office check. [Deployment scenarios](acceptance.md#post-deployment-scenarios)

After confirmation, remove the transfer copies of `enrollment.json` and `workshop-token` from both handoff directories. Each service uses the credentials stored in its own data directory.

## Back up and recover

```sh
sudo ./constellation_matrix backup
sudo ./constellation_matrix restore-check
```

The checkpoint briefly stops the deployment’s running services to capture consistent PostgreSQL, Neo4j, Matrix, agent, and desktop state. The encrypted Restic repository keeps seven successful checkpoints. Keep the Restic password outside the VPS’s failure domain. The restore check verifies an unpacked copy in an isolated directory.

For a fresh live cutover, first test a separate installation with a different project name, data directory, and local ports. Leave `tailscale` and `systemd` off for the isolated fixture. Before stopping the current deployment, inventory its services, data, mounts, and Serve routes and prove that you can restore an encrypted backup. Switch only the routes owned by this deployment. Verify the fresh setup before removing the superseded state.

## Uninstall

```sh
sudo ./uninstall.sh
```

This removes the workspace's Docker containers, systemd units, and matching Tailscale Serve routes. It keeps the deployment directory, Matrix state, agent workspaces, and encrypted backups for recovery. Use `--data-dir /path/to/deployment` if you installed into a custom directory.

For a completely fresh setup, run:

```sh
sudo ./uninstall.sh --purge
```

`--purge` permanently deletes the selected deployment directory and its Docker volumes. This includes Matrix rooms and accounts, provider credentials, agent workspaces, Context Intelligence, Telegram bindings, and backups stored in that directory. Run the installer again to create the new workspace.

For a remote worker, run the script on the worker with its own data directory. Unmount any SSHFS desktop inside that directory before using `--purge`. Remove the worker's mount entry and revoke its desktop key when retiring it permanently.
