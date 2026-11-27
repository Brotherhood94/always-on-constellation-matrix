# Finalize your Constellation setup

The terminal opens the Element sign-in screen after configuring the selected agents. Press Enter once you have signed in. Press Ctrl+C to pause. Resume from the VPS checkout or append `--print` to print the sign-in instructions and guide locations. If you installed with a custom data directory, pass the same `--data-dir` before `setup-guide`.

```sh
./constellation_matrix setup-guide
```

Enter passwords, API keys, and verification codes in their destination app or private VPS terminal.

## 🤖 Select agents and connect a provider

1. Choose your team and select or connect a provider account.

2. Choose the model and reasoning effort. Press **←** or **B** to return to the previous menu.

3. Review the provider, account, model, and reasoning effort. The **Shared provider profile** screen shows the values every agent inherits. Apply them to the whole team or choose different settings for individual agents.

4. Wait for the installer to verify the agents, then sign in to Element.

## 💬 Element Desktop

1. **Browser.** Download and install Element Desktop. If the deployment uses Tailscale, connect this computer to the tailnet before opening the private workspace URLs. [Element download](https://element.io/download)
2. **Element.** Choose **Sign in**. Under **Account provider**, click **Edit** to the right of the current server (usually `matrix.org`), above **Continue**.
3. **Element.** Paste the HTTPS Matrix homeserver printed by `setup-guide` into **Other account provider** and select **Continue**.
4. **Operator handoff.** Enter the printed Matrix username and the operator password you chose during installation. Select **Sign in**, then open **Agents → Office**. [Element onboarding](https://docs.element.io/latest/element-support/quick-start-guide/onboarding/)

## 📌 Finish in Element

- **Agents → Control Room → Pinned messages → Workspace shortcuts.** Open the dashboard to see resources and agent readiness. Open Context Intelligence to launch Neo4j Browser and sign in as `neo4j` with the dedicated password. The pin includes the command to retrieve the password privately on your VPS.
- **Agents → Control Room → Pinned messages → Connect Dot.** Create the Secure MCP Tunnel and restricted runtime key, then connect it to the **Constellation Office** plugin. Ask Dot to run `list_agents`. Compare the roster with the agents you configured. [Dot setup](dot.md)
- **Telegram → Rooms → Telegram Bridge → Pinned messages → Set up Telegram.** Follow the QR sign-in steps when the terminal asks you to link Telegram. After setup, mention Task Manager in Office for Telegram work or Telegram Agent here for a private request. Contacts and groups appear under **Telegram → People**. [Telegram setup](telegram.md)

Control Room publishes its pins after you join and Element registers your first device’s encryption keys. Later sign-ins reuse the same pins. On a new device, restore your Matrix encryption keys if Element cannot show the original messages.

## 🔌 Connect Dot

If you selected Dot, the terminal prints the links, tunnel and key permissions, the VPS command, and the **Constellation Office** connection check. Use `./constellation_matrix setup-guide --print` to see the instructions again. [Dot setup](dot.md)

## 📨 Configure Telegram Agent

When Telegram is enabled, the installer registers **Telegram Agent** in Office and includes it in the numbered provider setup. Complete bridge credentials and QR sign-in after the Element page. Scan the QR code displayed in the VPS terminal. Enter your Telegram two-step verification password at the hidden terminal prompt if Telegram asks for it.

The terminal can reuse a private `~/telegram-api.json` on the VPS with mode `0600`. At the Telegram API JSON prompt, paste the path to your private file on the VPS. Press Enter to use the displayed default. If no default is available, pressing Enter opens the App api_id and masked App api_hash prompts.

If you closed the installer, resume the Telegram step on its own.

```sh
./constellation_matrix telegram setup
```

[Telegram credentials and setup](telegram.md)

## 👋 Receive the welcome and send a task

When all selected agents are ready, Hiring Manager sends the encrypted welcome in its private chat if selected. Otherwise, a ready Office agent sends it in Office. The installer prints the message link after delivery. The welcome links to the **Workspace shortcuts** pin in **Control Room**.

Hiring Manager can propose hires and retirements as soon as setup finishes. Review each exact operation in its private chat. [Manage Office workers](team.md#first-interactions)

When Task Manager is installed, start with Task Manager for coordinated Office tasks.

- **Element.** In **Agents → Office**, select `@Task Manager` with the mention picker and send “@Task Manager, coordinate a three-step checklist for getting started in Office and post the result in this thread.”
- **Dot.** If enabled, open a ChatGPT conversation with **Constellation Office** and ask “Dot, send this task to Office starting with Task Manager: coordinate a three-step checklist for getting started and post the result in the Office thread.”

Use the Office agent named in your welcome if you skipped Task Manager. If your roster has no Office agents, add one before submitting a task. [Add a worker](runbook.md#add-a-worker)
