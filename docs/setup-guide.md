# Finalize your Constellation setup

The terminal opens the Element sign-in screen after deploying the workspace. Press Enter once you have signed in. Press Ctrl+C to pause. Resume from the VPS checkout or append `--print` to print the sign-in instructions and guide locations. If you installed with a custom data directory, pass the same `--data-dir` before `setup-guide`.

```sh
./constellation_matrix setup-guide
```

Enter passwords, API keys, and verification codes in their destination app or private VPS terminal. The guide asks for sudo when it needs to change deployment state. The printable guide reads setup information without sudo.

## 💬 Element Desktop

1. **Browser.** Download and install Element Desktop. If the deployment uses Tailscale, connect this computer to the tailnet before opening the private workspace URLs. [Element download](https://element.io/download)
2. **Element.** Choose **Sign in**. Under **Account provider**, click **Edit** to the right of the current server (usually `matrix.org`), above **Continue**.
3. **Element.** Paste the HTTPS Matrix homeserver printed by `setup-guide` into **Other account provider** and select **Continue**.
4. **Operator handoff.** Enter the printed Matrix username and the operator password you chose during installation. Select **Sign in**, then open **Agents → Office**. [Element onboarding](https://docs.element.io/latest/element-support/quick-start-guide/onboarding/)

## 📌 Finish in Element

- **Agents → VPS Alerts → Pinned messages → Workspace shortcuts.** Open the dashboard to see resources and agent readiness. Open Context Intelligence to launch Neo4j Browser and sign in as `neo4j` with the dedicated password. The pin includes the command to retrieve the password privately on your VPS.
- **Agents → VPS Alerts → Pinned messages → Connect Dot.** Create the Secure MCP Tunnel and restricted runtime key, then connect it to the **Constellation Office** plugin. Ask Dot to run `list_agents`. An empty list is expected before you activate any Office agents. [Dot setup](dot.md)
- **Telegram → Rooms → Telegram Bridge → Pinned messages → Set up Telegram.** Use the pin to activate the bridge, scan the login QR code, verify a chat, and test draft approvals. Contacts and groups appear under **Telegram → People**. [Telegram setup](telegram.md)

The pins are published after you join the room and Element has registered your device’s encryption keys. You can finish these steps while the agents are being configured.

## 🤖 Select agents

On a fresh interactive install, **Agents → Office** displays a **Finish setting up Office** message. Continue in the same VPS terminal if the installer is open. After confirming Element sign-in, use the arrow keys and Enter to review the six preselected starter agents and adjust your roster. Choose **Done** to register the agents. The installer uses sudo to save the agent registrations in the protected deployment directory and start local Amplifier containers. If prompted, enter the Linux password for the VPS account shown in the sudo prompt.

The installer opens Amplifier sign-in for each selected local agent. If you closed the installer or it was interrupted, run `./constellation_matrix agent configure` once your agents are registered to resume the remaining provider setup. Run `./constellation_matrix setup-guide` if you still need to choose or register your agents. Remote workers complete enrollment on their own host and are confirmed on the VPS. [Agent setup](runbook.md#initial-setup) · [Remote workers](runbook.md#add-a-remote-worker)

## 👋 Receive the welcome and send a task

When all selected agents are ready, Hiring Manager sends the encrypted welcome in its private chat if selected. Otherwise, a ready Office agent sends it in Office. The installer prints the message link after delivery. The welcome links to the **Workspace shortcuts** pin in **VPS Alerts**.

On a systemd VPS, Hiring Manager can propose hires and retirements as soon as setup finishes. Review each exact operation in its private chat. [Manage Office workers](team.md#first-interactions)

When Task Manager is installed, start with Task Manager for coordinated Office tasks.

- **Element.** In **Agents → Office**, select `@Task Manager` with the mention picker and send “@Task Manager, coordinate a three-step checklist for getting started in Office and post the result in this thread.”
- **Dot.** If enabled, open a ChatGPT conversation with **Constellation Office** and ask “Dot, send this task to Office starting with Task Manager: coordinate a three-step checklist for getting started and post the result in the Office thread.”

Use the Office agent named in your welcome if you skipped Task Manager. If your roster has no Office agents, add one before submitting a task. [Add a worker](runbook.md#add-a-worker)
