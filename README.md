# Always-on Constellation via Matrix

Run a team of persistent Amplifier agents on a private Linux VPS. Assign work in Element or through Dot, follow the handoffs in one encrypted Office thread, and see the verified outcome on the original request.

## Install

Use an Ubuntu 24.04 VPS with Docker Engine, Docker Compose, Python 3, curl, Restic, and a connected Tailscale node with HTTPS enabled. Tailscale Serve gives authorized devices private HTTPS access to Element, the dashboard, and Neo4j Browser. Dot uses an outbound OpenAI Secure MCP Tunnel to reach its private MCP bridge. [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) · [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

```sh
sudo ./constellation install
sudo ./constellation agent sign-in manager
```

Select the agents you want. Manager coordinates assignments, Analyst investigates, Coder works on files, and Reviewer checks results. Choose a default Amplifier subscription label and override it for individual agents as needed. Run `agent sign-in` for each local agent you selected. Authorize each Instance in the provider’s device sign-in flow. For repeatable setup, copy [the example config](deploy/config.example.json), update the values, and pass it with `--config`.

In Element, sign in with your new Matrix account and open **Agents → Office**. Use Element’s mention picker to tag one agent in a new message. A good first request is “Ask Reviewer to check a short deployment checklist and summarize the result.” The agent adds 👀 on its assignment. Manager posts the verified result in the original thread and marks the initial request with ✅.

## Operate the workspace

- **Dashboard.** Open **Agents → VPS Alerts → Pinned messages → Open dashboard**. The dashboard shows VPS resource history, agent health, and integration status. **Open Context Intelligence** opens the authenticated Neo4j Browser. You can also find that link in the VPS Alerts pins.
- **Extend the team.** Add agents with `sudo ./constellation agent add --profile /path/to/agent.json`. Each general Office worker has a private workspace and read/write access to the [shared desktop](desktop/README.md). [Profile and remote-worker setup](docs/runbook.md)
- **Review work and maintenance.** Use the Chief of Staff DM for team reviews and proposed hires or profile edits. Root Operations approvals go to its own private chat. [Chief of Staff](docs/chief-of-staff.md) · [Root Operations](integrations/root/README.md)
- **Connect integrations.** Dot can publish Office tasks and fetch their outcomes through the private tunnel. Telegram Control can prepare a draft or report updates from an approved portal. [Dot and tunnel setup](docs/dot.md) · [Telegram](docs/telegram.md)
- **Verify and recover.** Run `./constellation status` for the latest dashboard snapshot. Use sudo for `status --refresh`, `backup`, and `restore-check`. Read the [acceptance checks](docs/acceptance.md) and the [communication policies](policy/README.md) before replacing an existing deployment.

[Architecture](docs/architecture.md) · [Microsoft Constellation](https://github.com/microsoft/amplifier-constellation) · [Amplifier](https://github.com/microsoft/amplifier)
