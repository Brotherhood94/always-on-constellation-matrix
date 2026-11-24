# Install and operate Constellation

## Initial setup

On Ubuntu 24.04, install Python 3 and Docker Engine with Compose V2. Set up rootless Docker for your account and select its context. Confirm that `docker info` lists `rootless` under Security Options. A new VPS may need an administrator to install `uidmap`, allocate subordinate UID and GID ranges, and enable lingering for the account if the daemon must start at boot and run after logout. [Docker rootless setup](https://docs.docker.com/engine/security/rootless/)

For Tailscale Serve, enable HTTPS certificates and have the node administrator delegate Serve with `sudo tailscale set --operator=<your-linux-user>`. Use the node's tailnet DNS name for Matrix. Serve gives authorized devices private HTTPS access to the deployment. [Tailscale CLI](https://tailscale.com/docs/reference/tailscale-cli) · [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

```sh
./constellation_matrix install
./constellation_matrix agent sign-in manager
./constellation_matrix agent sign-in coder
./constellation_matrix status
```

The wizard asks for the exact roster. An empty selection provisions the workspace so you can add agents later. Rootless installations store state in `${XDG_STATE_HOME:-$HOME/.local/state}/constellation-matrix`. The installer sets service file ownership inside the rootless Docker user namespace. Use `--data-dir` before the subcommand to scope a second installation. Pick a dedicated directory outside the checkout. In config-file mode, keep the owner password file outside the checkout with mode `0600`. Resolve relative paths from the directory where you run the command. Use `./constellation_matrix validate --config /path/to/install.json` to check the configuration before installation.

`./constellation_matrix status` reads the dashboard snapshots and displays their timestamps and freshness. Run `./constellation_matrix status --refresh` to publish current Docker inventory. In rootless mode, the dashboard metrics and scheduled backup timers from the administrator setup are unavailable. Run backups on demand. Services use Docker restart policies once the user daemon is running.

The default private endpoints use HTTPS 443 for Matrix, 8443 for the dashboard, 8444 for Neo4j Browser, and TLS TCP 17687 for Bolt. For a different private proxy, set `tailscale` to false and provide the dashboard and browser URLs in the install config. Proxy to the configured loopback ports, which default to 18008 for Matrix, 18082 for the rootless dashboard, 17474 for Neo4j Browser, and 17688 for Bolt.

Sign in to Element with your Matrix operator account. The Agents Space links Office, VPS Alerts, and the private agent chats you selected. In Element Web or Desktop, disable **Show all rooms** in **Settings → Sidebar** if you want rooms in Spaces omitted from your Home view. The monitor publishes its shortcuts once it sees the operator in VPS Alerts. Sign in to Neo4j Browser as `neo4j` using the deployment’s local `context/neo4j-password` file.

## Administrator install

Use this mode for Root Operations, the system service for Dot's OpenAI tunnel, and remote workers. Install Docker Engine with Compose V2, Python 3, curl, and Restic on the VPS. The default state directory is `/srv/constellation-matrix`.

```sh
sudo ./constellation_matrix install
sudo ./constellation_matrix agent sign-in manager
```

Use the same administrator account for agent changes, Telegram management, live status, and recovery commands in this guide. Read cached status as a regular user. Moving between modes requires a separate deployment directory, project, and ports.

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
./constellation_matrix agent add --profile /path/to/qa.json
./constellation_matrix agent sign-in qa
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
./constellation_matrix backup
./constellation_matrix restore-check
```

The checkpoint briefly stops the deployment’s running services to capture consistent PostgreSQL, Neo4j, Matrix, agent, and desktop state. The encrypted Restic repository keeps seven successful checkpoints. Keep the Restic password outside the VPS’s failure domain. The restore check verifies an unpacked copy in an isolated directory.

For a fresh live cutover, first test a separate installation with a different project name, data directory, and local ports. Leave `tailscale` and `systemd` off for the isolated fixture. Before stopping the current deployment, inventory its services, data, mounts, and Serve routes and prove that you can restore an encrypted backup. Switch only the routes owned by this deployment. Verify the fresh setup before removing the superseded state.
