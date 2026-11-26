# Telegram integration

The optional integration creates a private, top-level **Telegram** Space. **Rooms → Telegram Bridge** contains setup, bridge commands, recipient selection, drafts, updates, and approvals. **People** contains bridged contacts and groups. Only the operator, `@telegrambot`, and `@telegram-agent` join Telegram Bridge. Open its **Set up Telegram** pin to get started.

Telegram Agent also joins **Agents → Office** as the Telegram specialist. Its dedicated Constellation Instance runs Amplifier over scoped ACP. [Acceptance scenarios](acceptance.md#7-verify-optional-integrations)

Already set up? [Search for a Telegram user](#search-for-a-telegram-user), [use Telegram from Office](#use-telegram-from-office), [open a contact or group in Telegram Bridge](#add-a-telegram-contact-or-group), or [send a message](#write-to-a-contact-or-group).

## Ask Telegram Agent

In **Telegram → Rooms → Telegram Bridge**, select **@Telegram Agent** from Element’s mention picker. Adapt one of these examples to your chat.

- “@Telegram Agent, add Sam Example to my Telegram rooms.” You can also specify `@sample_contact`, a numeric Telegram user ID, or a known group name or ID.
- “@Telegram Agent, prepare a private message to Sam Example saying tomorrow works for me.”
- `@Telegram Agent, manda messaggio a Sam "ciao bello".`
- “@Telegram Agent, summarize what I missed today in Project Room.”

The agent uses the linked Telegram account to resolve contacts and groups. When several chats match, reply to the agent’s private thread with `telegram choose 2`, using the number it displayed. Opening a chat links it under **Telegram → People**. To remove the link, ask the agent to unlink the chat from Telegram. Drafts and updates require a [verified chat binding](#bind-verified-chats-to-the-telegram-space). When you ask to send a message, review the draft and choose **✅ Accept** in the private thread.

The bridge and pinned guide can be active while the agent is still being set up. If the Telegram status lists only the guide and bridge, [finish setting up and starting the agent](#start-the-drafting-agent) before trying an example.

## Search for a Telegram user

In **Telegram → Rooms → Telegram Bridge**, send `!tg search Sample Name` and replace `Sample Name` with a name or Telegram username. The bridge replies with matching names and numeric user IDs. This command uses your linked Telegram account and works before you set up the drafting agent.

The bridge checks saved contacts by name and username, then searches Telegram usernames if none match. Check the result before opening its chat. Copy the ID into `!tg pm 1001` in **Telegram Bridge**, or use `telegram add user:1001` after mentioning **@Task Manager** in **Office**. [Bridge search](https://github.com/mautrix/go/blob/v0.33.0/bridgev2/commands/startchat.go#L332-L370) · [Telegram search API](https://core.telegram.org/method/contacts.search)

## Use Telegram from Office

In **Agents → Office**, start a new message and choose **@Telegram Agent** from Element’s mention picker for a direct Telegram task. For example, add `List my Telegram rooms` or `manda messaggio a Sam "ciao bello"`. Telegram Agent posts status and a link to its private thread in the original Office thread. Use **Telegram → Rooms → Telegram Bridge** when you want the original request itself to be private. The dashboard shows Telegram Agent and its setup status alongside the Office team.

Choose **@Task Manager** when you want the coordinator to hand off the request or to use one of the exact Office commands below. On an older workspace with Manager, mention **@Manager**.

You can also write “Find Sam Example on Telegram and prepare a private message saying tomorrow works for me.” Task Manager delegates to Telegram Agent and posts status and a link to the private thread in Office. Contact matches, summaries, and draft text appear in **Telegram → Rooms → Telegram Bridge**. The drafting connector must be configured for natural-language requests. The exact commands below remain available.

| Action | Text after the mention |
| --- | --- |
| Open a private chat | `telegram add @username` |
| Open a private chat by numeric peer ID | `telegram add user:1001` |
| Open an existing group | `telegram add https://t.me/c/1234567890/42` or `telegram add channel:1234567890` |
| Remove a person or group from the Space | `telegram remove "Sample Contact"` |
| Draft a private message | `telegram draft direct "Sample Contact": See you tomorrow!` |
| Draft a group message | `telegram draft group "Project Room": The meeting starts at 10.` |

For a private chat, you can also write “Add @username to Telegram Rooms” or “Aggiungi @username alle stanze di Telegram”. Task Manager opens one bridged room for that contact under **Telegram → People**.

The bridge uses the operator's linked Telegram account. Open the result from the private Telegram Bridge thread or find the new room under **Telegram → People**. For an undiscovered group, send `!tg sync-chats` in Telegram Bridge and retry. A basic group can be opened with `telegram add chat:<id>`. If several rooms share a name, remove one by its Matrix room ID. Removal unlinks the room, removes Telegram Agent’s access, and preserves your conversation and history. An explicit add can link it again.

If opening a private chat fails, send `!tg resolve-identifier @username` in **Telegram Bridge** to check the exact username. If you only know the name, use [Telegram Agent search](#search-for-a-telegram-user) to find the numeric ID. The bridge can open a numeric ID when it knows the contact through your linked Telegram account. If the bridge asks you to sign in, use `!tg login qr` and scan the code from your Telegram app. If the lookup succeeds and Office reports a connection that needs repair, run `./constellation_matrix telegram layout` on the VPS.

Drafting requires the [drafting connector](#start-the-drafting-agent) and a [verified binding](#bind-verified-chats-to-the-telegram-space) for the destination. An Office draft command opens a private Telegram Bridge thread. Only the operator can approve and send the exact text there. To keep your request private from the start, open **Telegram → Rooms → Telegram Bridge** and mention the agent there. The exact draft command also works in that room without a mention. Dot is optional for both paths.

## Get the Telegram API credentials

Select Telegram in the Constellation installer. Run the VPS commands from the workspace checkout. If you installed with a custom data directory, pass the same `--data-dir` before `telegram`.

1. **Browser.** Open Telegram API development tools and enter your Telegram phone number in international format. The operator enters the confirmation code received in the Telegram app. [Telegram API development tools](https://my.telegram.org/apps)
2. **Browser.** Open **API development tools** after signing in if the site shows an account menu. Open your existing application, or complete the application form and select **Create application**. Copy **App api_id** and **App api_hash** for the bridge. [Obtaining Telegram API credentials](https://core.telegram.org/api/obtaining_api_id)
3. **VPS terminal.** Reuse a valid private `telegram-api.json` if you have one. To create it, run the following as the VPS operator to keep it in your home directory outside the checkout, with mode `0600`.

```bash
(umask 077; touch "$HOME/telegram-api.json")
chmod 600 "$HOME/telegram-api.json"
nano "$HOME/telegram-api.json"
```

**Operator handoff.** In the private editor, replace both example values with the application credentials. Keep `api_id` a JSON number and `api_hash` a quoted string.

```json
{"api_id": 1234567, "api_hash": "REPLACE_WITH_YOUR_API_HASH"}
```

## Install the bridge

**VPS terminal.** Use the path to the private JSON file. Activation briefly restarts Synapse and starts mautrix with an empty portal allowlist.

```bash
sudo ./constellation_matrix telegram prepare "$HOME/telegram-api.json"
./constellation_matrix telegram activate
./constellation_matrix telegram status
```

Enter your existing Matrix operator password to link the bridge DM. The command asks for sudo when it needs to update Synapse or Docker. For unattended activation, pass a private `--owner-password-file`. If more than one verified private bridge DM exists, select the room you already use.

1. **Element.** In the top-level **Telegram** Space, open **Telegram Bridge**, the private chat with `@telegrambot:<SERVER_NAME>`. Copy this line, paste it into that chat’s message box, and press Enter. The bridge replies in the same chat with a QR code.

   ```text
   !tg login qr
   ```

2. **Telegram mobile.** The operator opens **Settings → Devices → Link Desktop Device** and scans the code shown in Element. Wait for the bridge to confirm sign-in. If the code expires, request a new one in the same DM. [Bridge sign-in](https://docs.mau.fi/bridges/go/telegram/authentication.html)

## Add a Telegram contact or group

1. **Element.** Open **Telegram → Telegram Bridge** and send the command for the conversation you want to open. [Bridge room commands](https://github.com/mautrix/go/blob/v0.33.0/bridgev2/commands/startchat.go)

   - **Look up before opening.** Send `!tg resolve-identifier @username` for a person or `!tg resolve-identifier channel:1234567890` for a known group. If the reply shows a command with `--open`, copy it into Telegram Bridge and send it to open the room. An existing room appears as a clickable link in the lookup reply.
   - **Private chat.** Send `!tg pm @username` or `!tg pm 1001`, replacing `1001` with the person's numeric Telegram user ID. If you only know their name, send `!tg search <name>` and check the matching profile first.
   - **Group without topics.** In Telegram, copy a link to a message in the group. For `https://t.me/c/1234567890/42`, send `!tg create-portal channel:1234567890` in Telegram Bridge. Use the first number from the link. [Telegram message links](https://core.telegram.org/api/links#message-links)
   - **Basic group or group with a public link.** In Telegram Desktop, open the group and choose **⋯ → Export chat history → JSON**. Check the chat name in `result.json` and use the top-level `id`. Send `!tg create-portal chat:<id>` for a basic group or `!tg create-portal channel:<id>` for a supergroup without topics. [Telegram Desktop export](https://telegram.org/blog/export-and-more) · [Export fields](https://core.telegram.org/import-export#chat)

2. **Element.** Click the chat name in the bridge’s reply and join if prompted. Find the chat under **Telegram → People**. Check the recipient before sending a message. For a chat you previously unlinked from the Space, use the [Office add command](#use-telegram-from-office) to link it again.

If the bridge has not found the group yet, send `!tg sync-chats`, wait for it to finish, then retry `!tg create-portal`. If you use multiple Telegram accounts, send `!tg list-logins` to find your own account ID and put it before the username or group ID in the command. [Telegram bridge group sync](https://github.com/mautrix/telegram/blob/8d85e547208fcdeae47b08f57bca0a7c8cf910af/pkg/connector/commands.go)

If Element also lists the room in **Home**, open **Settings → Sidebar** and turn off **Show all rooms**. [Element sidebar settings](https://docs.element.io/latest/element-support/element-webdesktop-client-settings/sidebar-settings/)

### Open a room on an incoming message

To let a new incoming message open a room, allow that specific chat. In Telegram Bridge, `!tg resolve-identifier @username` looks up the recipient ID. If the reply shows `Found Sample Contact (1001)`, use `user:1001`. Use `!tg list-logins` to find your own Telegram account ID. Run this on the VPS with those two values, then open the room when a new message arrives.

```bash
sudo ./constellation_matrix telegram allow 'user:1001' '2002'
```

## Start the drafting agent

The dedicated Constellation Instance runs Amplifier ACP with the Amplifier ChatGPT provider in `chatgpt_codex` mode. It uses the Codex subscription through device sign-in. The default model is `gpt-6.2-sol` with low reasoning effort for drafting. On first provisioning, set `TELEGRAM_MODEL` if the subscription needs a different supported model. [Amplifier ChatGPT provider](https://github.com/microsoft/amplifier-module-provider-openai-chatgpt)

**VPS terminal.** Run the commands in order, using an interactive session for `sign-in`.

```bash
sudo ./constellation_matrix telegram prepare-agent
sudo ./constellation_matrix telegram sign-in
sudo ./constellation_matrix telegram probe
sudo ./constellation_matrix telegram start-agent
```

**Operator handoff.** When `sign-in` prints a verification page and one-time code, open that page in your browser and complete device verification with the intended Codex account. Resume in the terminal after sign-in succeeds. The Instance saves its OAuth credentials in its own private persistent directory. The connector uses its own scoped ACP token. `probe` runs two synthetic turns and checks that the second turn resumes the Amplifier session before the connector can start.

If an earlier setup stopped with `HTTP 400 M_UNKNOWN` while creating a Matrix v12 room, rerun `prepare-agent` with the updated installer. It reuses the connector identity and initialized rooms. If a failed request left an alias on an empty room owned by the connector, it recreates the room and assigns that alias to it.

The release pins mautrix Telegram `v26.11` with a small patch that displays a single chat link in `!tg pm` replies. It also pins Constellation Instance `0.22.17`, Amplifier ACP `0.1.7` at `3da5681942d9e9b4c51376e47ec85e69a001623a`, and the Amplifier ChatGPT provider at `cae3eaa32e16b139b8e889b8cc477c3f2d9dee31`.

The Telegram model uses a standalone Amplifier bundle. The connector records scoped delivery metadata in Context Intelligence. Dependencies are prepared when the engine image is built. If an older engine failed to activate `hook-context-intelligence`, rerun `prepare-agent` with the updated release and then rerun `probe`. A completed Codex sign-in is reused.

## Bind verified chats to the Telegram Space

**Element.** Telegram Agent automatically joins eligible chats after you open and join them. In the verified portal, enable the operator’s Telegram login as its relay with `!tg set-relay <login-id>`. Configure a per-chat binding to enable reading and drafting. Dot submits private requests to the connector through a local socket. [Relay mode](https://docs.mau.fi/bridges/general/relay-mode.html)

To let the drafting agent or Dot use a room, keep a private binding file for that chat. Use `mode: draft` and `trigger: inbound` for the first DM. Use `mode: draft` and `trigger: request` for an eligible group. A basic group and a supergroup without topics are supported by the admission policy. A forum or broadcast channel needs its own mapping and acceptance.

```json
{
  "id": "pilot-dm",
  "peer": "user:1001",
  "login": "2002",
  "receiver": "2002",
  "room": "!replace-with-verified-room:matrix.example",
  "mode": "draft",
  "trigger": "inbound"
}
```

**VPS terminal.** Give each chat a unique `id`, such as `sam-private` or `project-group`. Set `peer` to its verified Telegram ID with the `user:`, `chat:`, or `channel:` prefix. Use the connected Telegram login ID for both `login` and `receiver`. In the portal in Element, find its **Internal Room ID** under **Room settings → Advanced** and use that as `room`. Save the binding as a private file. [Element room settings](https://docs.element.io/latest/element-support/matrix-rooms/managing-a-room-room-settings/)

```bash
sudo ./constellation_matrix telegram bind /path/to/pilot-dm.json
```

Keep the connector running while binding. It completes a Matrix sync after joining the portal before the binding activates. When the command succeeds, open **Telegram** in Element and look for the contact or group’s portal under **People**. A later sync gap or loss of membership pauses that binding. Use `report` to see the reason for a pause recorded by the connector.

**Telegram and Element.** Have the test contact send a new message. In **Telegram Bridge**, check that the draft cites the test portal and review its destination and text. The operator selects **✅ Accept** on that draft. Confirm exactly one message arrives in the intended Telegram chat with the expected attribution.

`manual` disables automatic drafting. `draft` prepares a candidate for review. Upgrades convert legacy `auto` bindings to review mode. Every agent-generated outbound message requires your poll vote.

## Operate in Element

### Write to a contact or group

1. In Element, open **Telegram** and select the contact or group’s portal. Check the room name and the conversation before sending.
2. Type a new message in that portal and press Enter. To respond to a specific message, choose **Reply** on it first. Messages sent from the portal can publish immediately to Telegram using the connected login.
3. For a reviewed reply, switch to **Telegram Bridge** and follow the draft steps below. If Dot is enabled, you can also ask it to draft a new message to a connected chat, then approve it in Telegram Bridge.

### Review and manage drafts

- To request a draft for a group, send `telegram draft <binding-id> <source-event-id>` as a fresh message in Telegram Bridge. Use the latest admitted remote text from the intended portal.
- Telegram Bridge mentions you in a preview with the verified recipient, exact text, source link, and expiry. Vote **✅ Accept** or **❌ Decline** in the matching poll. The first valid operator vote closes the poll. New messages, changes to the binding or room membership, and expiry can make a draft stale.
- `telegram pause <binding-id>` in Telegram Bridge pauses an individual binding. `telegram pause` there pauses all bindings. If Matrix is unavailable, use `telegram pause` from the VPS root console. The pause applies before a send enters the in-flight boundary.

### Dot and chat updates

When Dot is enabled, run `sudo ./constellation_matrix telegram dot-control enable` and accept the invitation with the Dot device as directed by provisioning. Refresh the private Dot MCP app to load `prepare_telegram_message` and `request_telegram_update`.

- In Dot, ask for a draft to one connected Telegram chat. Specify private chat or group and its name. For example, “Prepare a private message to Sam Example asking whether tomorrow at 15:00 works.” The exact text and verified destination appear in Telegram Bridge. A draft that identifies a received Matrix event in the same chat can be published as a reply.
- Only the configured operator can approve or reject an outbound message. Dot can submit a draft and see the link to its request. The plugin uses the existing private tunnel and has no send or approval tool.
- In Telegram Bridge, select `@telegram-agent` from the mention picker and ask “What happened today in Project Room?” or “What did Sam say in the private chat today?” The answer mentions you in the request thread and links to its sources.
- Follow up in that same thread with a real `@telegram-agent` mention to reuse its connected chat. For example, “What did they say before?” requests another update. “Rispondi” prepares a reviewable draft for the latest verified incoming text. The approval stays in the original Telegram Bridge thread. Start a new request to switch chats.
- Updates read one approved portal at a time. If the request is ambiguous, the connector asks for a chat name. It reads up to 30 available text messages from today in Europe/Rome, the last 24 hours by default, or the last seven days if requested. It marks partial coverage when it cannot read or verify every candidate. Telegram backfill stays disabled. The model receives only text from the selected portal for that request and has no access to Matrix or Telegram tools.
- VPS commands `report`, `pause-binding <id>`, and `status` show private operational state. A prepared Matrix request uses a stable transaction and ciphertext. Trusted mautrix status is correlated by Matrix event ID. `matrix accepted`, `bridge confirmed`, and `unknown` are distinct. Reconcile unknown outcomes before re-enabling that binding.
- After a global pause, verify destination, audience, and any in-flight effects. Run `arm-resume`, `resume`, and then `bind` for the selected conversations. A changed audience or endpoint requires pausing and `rotate` before rebinding. Remove the connector from the portal and remove its relay when revoking its ability to send.

A Telegram relay uses the selected Telegram login with bridge attribution. Matrix encryption covers the Matrix side up to mautrix. Telegram uses the protections of the selected Telegram chat. [Bridge encryption](https://docs.mau.fi/bridges/general/end-to-bridge-encryption.html)

## Repair an existing workspace

Run from the updated VPS checkout. Enter your Matrix operator password when prompted. The repair keeps Root Operations in place and changes its sidebar classification to Rooms. It moves the managed Telegram pin to Telegram Bridge and moves linked contacts and groups under People. The old Telegram Agent room remains accessible through a history link in the new pin.

```sh
./constellation_matrix layout
```

Use `./constellation_matrix telegram layout` to repair only Telegram. If multiple private Bridge rooms match, choose the one you use, or pass `--bridge-room <room-id>`. Reconcile any delivery whose outcome is still uncertain before starting. Eligible unsent drafts move to Telegram Bridge with fresh polls. Logins, portal rooms, bindings, and delivery receipts are retained.

## Recovery and monitoring

Run `./constellation_matrix telegram status` to read the published health snapshot. Use `sudo ./constellation_matrix telegram status --refresh` to collect a new snapshot from Docker.

The Compose services use the existing Matrix cgroup and restart with Docker. `telegram metrics` exports sanitized health and queue counts to `telegram.json` under the private resource dashboard origin. `telegram enable-operations` installs backup and metrics timers for the selected deployment. Enable the timers after accepting the Telegram setup. Scheduled Telegram backups begin once the workspace has an encrypted Restic repository.

`telegram backup` pauses the Telegram containers while it captures the separate bridge database and persistent state into the existing encrypted Restic repository with tag `matrix-telegram`. It retains seven successful snapshots. `telegram restore-check` verifies a restored database archive and connector integrity offline. It prepares the restored state with bindings paused and pending work quarantined. In a recovery exercise the restored identities stay isolated until the operator reconciles remote delivery and current permissions. The base Matrix snapshot and the Telegram extension snapshot have separate timestamps.

`telegram deactivate` pauses the connector, stops the optional containers, removes its AppService registration from Synapse configuration, and restarts Synapse. It retains the encrypted backup material and private service state for review. Remove an individual portal through the existing bridge administration flow after revoking its agent binding.

## Reproduce the isolated checks

The fixture tests use private Docker networks and discard their own containers and volumes. They use synthetic credentials and an offline model fixture.

```bash
cd integrations/telegram
npm ci --ignore-scripts
npm test
TELEGRAM_DATA=/tmp/matrix-telegram-fixture docker compose --profile agent build connector telegram-engine
bash tests/bridge-smoke
bash tests/engine-smoke
```
