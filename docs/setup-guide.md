# Finalize your Constellation setup

The installer opens this wizard after deploying the workspace. Press Enter or → for the next page and ← to go back. Press Ctrl+C to pause. Resume from the VPS checkout with the command below, or append `--print` for the full checklist. If you installed with a custom data directory, pass the same `--data-dir` before `setup-guide`.

```sh
sudo ./constellation_matrix setup-guide
```

If someone is guiding the setup through computer use, take over for the password, API key, verification code, and QR steps marked below. Enter credentials directly in their destination app or private terminal.

## 💬 Element Desktop

1. **Browser.** Download and install Element Desktop. If the deployment uses Tailscale, connect this computer to the tailnet before opening the private workspace URLs. [Element download](https://element.io/download)
2. **Element.** Choose **Sign in**. Under **Account provider**, click **Edit** to the right of the current server (usually `matrix.org`), above **Continue**.
3. **Element.** Paste the HTTPS Matrix homeserver printed by `setup-guide` into **Other account provider** and select **Continue**.
4. **Operator handoff.** Enter the printed Matrix username and the operator password you chose during installation. Select **Sign in**, then open **Agents → Office**. [Element onboarding](https://docs.element.io/latest/element-support/quick-start-guide/onboarding/)

## 🧠 Context Intelligence

1. **Browser.** Open the printed Context Intelligence URL to launch Neo4j Browser.
2. **Operator handoff.** Enter `neo4j` and the dedicated Context Intelligence password you chose. To recover a stored or generated password, use the command printed by the wizard. The default is shown below. Select **Connect** in Neo4j Browser.

```sh
sudo cat -- /srv/constellation-matrix/context/neo4j-password
```

## 📊 Dashboard

- **Browser and Element.** Open the printed dashboard URL. Then open **Agents → VPS Alerts → Pinned messages** in Element and check both links in the **Workspace shortcuts** pin. The roster can be empty before the agent selection step.

## 🔌 Dot · Secure MCP Tunnel

If you selected Dot, create and associate a tunnel in OpenAI Platform, issue a restricted runtime key, and start the tunnel in the VPS terminal. The operator enters the key at the hidden prompt. Connect the same tunnel to a private ChatGPT plugin named **Constellation Office** and ask Dot to run `list_agents`. An empty roster is expected before agent selection. [Dot setup and test](dot.md)

## ✈️ Telegram · Bridge and drafting agent

If you selected Telegram, create or open a Telegram API application, save its two credentials in a private JSON file on the VPS, and start the bridge. The operator completes Telegram verification in the browser and scans the bridge login QR code on their phone. Allow one verified test chat. For drafting, complete the separate Codex device sign-in, bind that chat, and approve one test delivery in Element. [Telegram setup and test](telegram.md#get-the-telegram-api-credentials)

## 🤖 Select agents

After the workspace pages, use the arrow keys and Enter to select your roster. Choose **Done** to register the agents. Complete Amplifier sign-in for each selected local agent. If provider setup is interrupted, use `sudo ./constellation_matrix agent configure` to resume. Remote workers complete enrollment on their own host and are confirmed on the VPS. [Agent setup](runbook.md#initial-setup) · [Remote workers](runbook.md#add-a-remote-worker)

## 👋 Receive the welcome and send a task

When all selected agents are ready, Chief of Staff sends the encrypted welcome in its private chat if selected. Otherwise, a ready Office agent sends it in Office. The installer prints the message link after delivery. The welcome links to the **Workspace shortcuts** pin in **VPS Alerts**.

On a systemd VPS, Chief of Staff can propose hires and retirements as soon as setup finishes. Review each exact operation in its private chat. [Manage Office workers](chief-of-staff.md#hire-or-retire-an-office-worker)

When Manager is installed, start with Manager for coordinated Office tasks.

- **Element.** In **Agents → Office**, select `@Manager` with the mention picker and send “@Manager, coordinate a three-step checklist for getting started in Office and post the result in this thread.”
- **Dot.** If enabled, open a ChatGPT conversation with **Constellation Office** and ask “Dot, send this task to Office starting with Manager: coordinate a three-step checklist for getting started and post the result in the Office thread.”

Use the Office agent named in your welcome if you skipped Manager. If your roster has no Office agents, add one before submitting a task. [Add a worker](runbook.md#add-a-worker)
