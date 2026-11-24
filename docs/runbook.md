# Install and operate Constellation

## Initial setup

Install Docker Engine with Compose V2, Python 3, curl, Restic, and Tailscale on Ubuntu 24.04. Enable HTTPS certificates for the private tailnet node. The installer uses HTTPS 443 for Matrix, 8443 for the dashboard, 8444 for Neo4j Browser, and TLS TCP 17687 for Bolt. If you supply a different private reverse proxy, set `tailscale` to false and provide the dashboard and browser URLs in the install config.

```sh
sudo ./constellation install
sudo ./constellation agent sign-in manager
sudo ./constellation agent sign-in coder
./constellation status
```

The wizard asks for the exact roster. An empty selection provisions the workspace so you can add agents later. The installer stores all generated state under `/srv/constellation-matrix` by default. Use `--data-dir` before the subcommand to scope a second installation. The config-file mode accepts an owner password file with mode `0600`. Use `./constellation validate --config /path/to/install.json` to check the configuration without installing.

`./constellation status` reads the existing dashboard snapshots and displays their timestamps and freshness. Use `sudo ./constellation status --refresh` to query Docker and publish updated inventory. Installation, service management, provisioning and recovery use the VPS administrator account. Office, the Chief of Staff DM and the dashboard use their usual account access.

Sign in to Element with your Matrix operator account. The Agents Space links Office, VPS Alerts, and the private agent chats you selected. In Element Web or Desktop, disable **Show all rooms** in **Settings → Sidebar** if you want rooms in Spaces omitted from your Home view. The monitor publishes its shortcuts once it sees the operator in VPS Alerts. Sign in to Neo4j Browser as `neo4j` using the deployment’s local `context/neo4j-password` file.

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
sudo ./constellation agent add --profile /path/to/qa.json
sudo ./constellation agent sign-in qa
```

The registry has one active coordinator and observer when those roles are enabled. Pending workers become available for routing after provider sign-in, an ACP session restore probe, and connector startup.

## Add a remote worker

Select `remote-worker` during setup or add a profile with `"placement": "remote"`. Generate a dedicated Ed25519 SSH key on the remote host for the desktop. On the coordinator, approve its public key and export the enrollment file.

```sh
sudo ./constellation remote desktop --public-key /path/to/remote-worker.pub
sudo ./constellation remote export remote-worker --host worker.example --output /root/worker-enrollment.json
```

Transfer the enrollment file privately. On the worker, install Docker, Tailscale, and SSHFS. Verify the desktop server’s SSH host key against the key reported by `remote desktop`. Mount `desktop-share@<tailnet-host>:/desktop` on the remote host over port 2222 with `allow_other,default_permissions,uid=991,gid=991`. Configure the SSHFS mount to come up before Docker after a reboot. The key only supports SFTP to the shared desktop.

```sh
sudo ./constellation --data-dir /srv/constellation-worker remote attach \
  --enrollment /root/worker-enrollment.json --desktop-mount /mnt/constellation-desktop \
  --workshop-host '<remote-worker-tailnet-dns-name>'
```

Transfer the resulting `workshop-token`, `workshop-ca.pem`, and `receipt.json` to the coordinator through a private channel. Enroll the live Instance in Workshop with the reported HTTPS management origin.

```sh
sudo ./constellation persona-connect remote-worker \
  --url 'https://<remote-worker-tailnet-dns-name>:19444' \
  --token-file /root/workshop-token --ca-file /root/workshop-ca.pem
```

Then run `sudo ./constellation remote confirm remote-worker --receipt /path/to/receipt.json`. Verify a real Office handoff and a file round trip in both directions. The coordinator shows the remote worker as **Registered** after confirmation. Keep tailnet access scoped to the selected operator devices and worker hosts.

## Back up and recover

```sh
sudo ./constellation backup
sudo ./constellation restore-check
```

The checkpoint briefly stops the deployment’s running services to capture consistent PostgreSQL, Neo4j, Matrix, agent, and desktop state. The encrypted Restic repository keeps seven successful checkpoints. Keep the Restic password outside the VPS’s failure domain. The restore check verifies an unpacked copy in an isolated directory.

For a fresh live cutover, first test a separate installation with a different project name, data directory, and local ports. Leave `tailscale` and `systemd` off for the isolated fixture. Before stopping the current deployment, inventory its services, data, mounts, and Serve routes and prove that you can restore an encrypted backup. Switch only the routes owned by this deployment. Verify the fresh setup before removing the superseded state.
