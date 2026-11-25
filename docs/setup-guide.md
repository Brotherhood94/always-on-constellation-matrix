# Finalize your Constellation setup

Use this checklist after deploying the workspace. Run the following from the repository on the VPS for the actual homeserver, username, URLs, and commands. If you installed with a custom data directory, pass the same `--data-dir` before `setup-guide`.

```sh
sudo ./constellation_matrix setup-guide
```

If any local agents are still waiting for a provider, run `sudo ./constellation_matrix agent configure` and finish their Amplifier sign-in. The installer prints this guide again after the final local agent is configured. [Agent setup](runbook.md#initial-setup)

If someone is guiding the setup through computer use, take over for the password, API key, verification code, and QR steps marked below. Enter credentials directly in their destination app or private terminal.

## 💬 Element Desktop

1. **Browser.** Download and install Element Desktop. If the deployment uses Tailscale, connect this computer to the tailnet before opening the private workspace URLs. [Element download](https://element.io/download)
2. **Element.** Choose **Sign in**. Under **Account provider**, click **Edit** to the right of the current server (usually `matrix.org`), above **Continue**.
3. **Element.** Paste the HTTPS Matrix homeserver printed by `setup-guide` into **Other account provider** and select **Continue**.
4. **Operator handoff.** Enter the printed Matrix username and the operator password you chose during installation. Select **Sign in**, then open **Agents → Office**. [Element onboarding](https://docs.element.io/latest/element-support/quick-start-guide/onboarding/)

## 📊 Dashboard

- **Browser.** Open the printed dashboard URL and confirm it shows the deployed services and agents. In Element, both the dashboard and Context Intelligence links also appear under **Agents → VPS Alerts → Pinned messages** once the operator has joined VPS Alerts and the monitor has published them.

## 🧠 Context Intelligence

1. **Browser.** Open the printed Context Intelligence URL or use **Open Context Intelligence** in the dashboard. Both open Neo4j Browser.
2. **Operator handoff.** Run the printed password command privately on the VPS. The default is shown below. Back in Neo4j Browser, enter `neo4j` and the retrieved password, then select **Connect**.

```sh
sudo cat -- /srv/constellation-matrix/context/neo4j-password
```

## 🔌 Dot · Secure MCP Tunnel

If you selected Dot, create and associate a tunnel in OpenAI Platform, issue a restricted runtime key, and start the tunnel in the VPS terminal. The operator enters the key at the hidden prompt. Connect the same tunnel to the private Dot plugin in ChatGPT and verify that it can read a reply to a task it opens. [Dot setup and test](dot.md)

## ✈️ Telegram · Bridge and drafting agent

If you selected Telegram, create or open a Telegram API application, save its two credentials in a private JSON file on the VPS, and start the bridge. The operator completes Telegram verification in the browser and scans the bridge login QR code on their phone. Allow one verified test chat. For drafting, complete the separate Codex device sign-in, bind that chat, and approve one test delivery in Element. [Telegram setup and test](telegram.md#get-the-telegram-api-credentials)
