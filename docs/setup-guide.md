# Finalize your Constellation setup

The installer opens this wizard after the local agents finish sign-in. Press Enter on **Continue** after completing each page. Type Q and press Enter to pause. Run the following from the VPS checkout to resume, or append `--print` for the full checklist. If you installed with a custom data directory, pass the same `--data-dir` before `setup-guide`.

```sh
sudo ./constellation_matrix setup-guide
```

If any local agents are still waiting for a provider, run `sudo ./constellation_matrix agent configure` and finish their Amplifier sign-in. [Agent setup](runbook.md#initial-setup)

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
2. **Operator handoff.** Enter `neo4j` and the dedicated Context Intelligence password you chose. To recover a stored or generated password, use the command printed by the wizard. The default is shown below. Select **Connect** in Neo4j Browser.

```sh
sudo cat -- /srv/constellation-matrix/context/neo4j-password
```

## 🔌 Dot · Secure MCP Tunnel

If you selected Dot, create and associate a tunnel in OpenAI Platform, issue a restricted runtime key, and start the tunnel in the VPS terminal. The operator enters the key at the hidden prompt. Connect the same tunnel to a private ChatGPT plugin named **Constellation Office** and verify that it can read a reply to a task it opens. [Dot setup and test](dot.md)

## ✈️ Telegram · Bridge and drafting agent

If you selected Telegram, create or open a Telegram API application, save its two credentials in a private JSON file on the VPS, and start the bridge. The operator completes Telegram verification in the browser and scans the bridge login QR code on their phone. Allow one verified test chat. For drafting, complete the separate Codex device sign-in, bind that chat, and approve one test delivery in Element. [Telegram setup and test](telegram.md#get-the-telegram-api-credentials)

## 👋 Hello Collaboration

Select **Finish** after confirming the setup pages. Once the selected agents have joined Matrix and completed activation, the wizard posts a welcome task from your Matrix account and prints the Office thread link. If an agent is still pending, finish its setup and rerun `setup-guide`. The wizard reuses the same thread after a successful launch.
