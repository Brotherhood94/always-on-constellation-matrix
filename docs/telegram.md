# Telegram integration

This optional integration adds a private, top-level **Telegram** Space with **Telegram Control**, the **Telegram Bridge** DM, and the private rooms for verified chats. The Telegram connector is separate from `Agents` and attaches over scoped ACP to one persistent Constellation Instance running Amplifier. The integration is ready for an isolated deployment and requires live acceptance before operational use. [Acceptance evidence](acceptance.md#7-verify-optional-integrations)

The installer joins your Matrix account to **Telegram** and **Telegram Control**. Open **Telegram → Telegram Control → Pinned messages → Set up Telegram** for the copyable setup steps.

Already set up? [Add a contact or group](#add-a-telegram-contact-or-group) or [send a message](#write-to-a-contact-or-group).

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
sudo ./constellation_matrix telegram activate
sudo ./constellation_matrix telegram status
```

When activation prompts, enter your existing Matrix operator password to link the bridge DM. For unattended activation, pass a private `--owner-password-file`. If more than one verified private bridge DM exists, select the room you already use.

1. **Element.** In the top-level **Telegram** Space, open **Telegram Bridge**, the private chat with `@telegrambot:<SERVER_NAME>`. Send `!tg login qr` to display a QR code.
2. **Telegram mobile.** The operator opens **Settings → Devices → Link Desktop Device** and scans the code shown in Element. Wait for the bridge to confirm sign-in. If the code expires, request a new one in the same DM. [Bridge sign-in](https://docs.mau.fi/bridges/go/telegram/authentication.html)

## Add a Telegram contact or group

Each connected Telegram conversation has its own Matrix room, called a portal. The portal appears under **Telegram** once you bind it. Your contact continues to use their existing Telegram chat. Start with one trusted contact for the first delivery test.

1. **Element.** In the bridge bot’s private chat, send `!tg list-logins` and copy the login ID for your connected Telegram account.
2. **Element or Telegram.** Identify the conversation you want to connect.

   - **Private chat.** In the bridge bot DM, send `!tg resolve-identifier <login-id> <telegram-username>` with the contact’s Telegram username. Check the resolved contact and use their peer ID in the form `user:1001`.
   - **Group.** Confirm the group name and membership in Telegram. Use `chat:<peer-id>` for a basic group or `channel:<peer-id>` for a supergroup without topics. Use the positive Telegram peer ID. For a supergroup message link in the form `https://t.me/c/<peer-id>/<message-id>`, use the first number after `/c/`. [Telegram message links](https://core.telegram.org/api/links#message-links)

3. **VPS terminal.** Allow the verified peer for the connected login. Replace the sample values with the peer and login you recorded.

   ```bash
   sudo ./constellation_matrix telegram allow 'user:1001' '2002'
   ```

4. **Telegram and Element.** Ask the contact or a group member to send a fresh test message in Telegram. Open the resulting portal in Element, accepting the room invitation if prompted. Check its name and membership, then send a short test reply in that room and confirm it arrives in the intended Telegram conversation. [Bridge configuration](https://docs.mau.fi/configs/mautrix-telegram/v26.11.html)
5. **VPS terminal and Element.** If this is your first chat, [start the drafting agent](#start-the-drafting-agent). Then [bind the verified portal](#bind-verified-chats-to-the-telegram-space) to add it to **Telegram**. Repeat the allow and bind steps for each additional conversation.

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

The release pins mautrix Telegram `v26.11`, Constellation Instance `0.22.17`, Amplifier ACP `0.1.7` at `3da5681942d9e9b4c51376e47ec85e69a001623a`, and the Amplifier ChatGPT provider at `cae3eaa32e16b139b8e889b8cc477c3f2d9dee31`.

The Telegram model uses a standalone Amplifier bundle. The connector records scoped delivery metadata in Context Intelligence. Dependencies are prepared when the engine image is built. If an older engine failed to activate `hook-context-intelligence`, rerun `prepare-agent` with the updated release and then rerun `probe`. A completed Codex sign-in is reused.

## Bind verified chats to the Telegram Space

**Element.** Telegram Control is encrypted, invite-only, and non-federated. Its members are the operator, the connector, and the optional configured Dot account. In the verified portal, invite `@telegram-agent:<SERVER_NAME>` and enable the operator's Telegram login as its relay with `!tg set-relay <login-id>`. The connector joins the portal during binding. [Relay mode](https://docs.mau.fi/bridges/general/relay-mode.html)

Keep a private binding file for each selected chat. Use `mode: draft` and `trigger: inbound` for the first DM. Use `mode: draft` and `trigger: request` for an eligible group. A basic group and a supergroup without topics are supported by the admission policy. A forum or broadcast channel needs its own mapping and acceptance.

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

**VPS terminal.** Give each chat a unique `id`, such as `sam-private` or `project-group`. Set `peer` to the value you allowed and use the connected Telegram login ID for both `login` and `receiver`. In the portal in Element, find its **Internal Room ID** under **Room settings → Advanced** and use that as `room`. Save the binding as a private file. [Element room settings](https://docs.element.io/latest/element-support/matrix-rooms/managing-a-room-room-settings/)

```bash
sudo ./constellation_matrix telegram bind /path/to/pilot-dm.json
```

Keep the connector running while binding. It completes a Matrix sync after joining the portal before the binding activates. When the command succeeds, open **Telegram** in Element and look for the contact or group’s portal alongside **Telegram Control**. A later sync gap or loss of membership pauses that binding. Use `report` to see the reason for a pause recorded by the connector.

**Telegram and Element.** Have the test contact send a new message. In `Telegram Control`, check that the draft cites the test portal and review its destination and text. The operator selects **Approve and send** on that draft. Confirm exactly one message arrives in the intended Telegram chat with the expected attribution.

`manual` records no automatic work. `draft` sends a candidate only into Control. `auto` queues a reply only in an explicitly configured binding, within its trigger, expiry, quota, audience, and outbox checks. The pilot starts with draft mode.

## Operate in Element

### Write to a contact or group

1. In Element, open **Telegram** and select the contact or group’s portal. Check the room name and the conversation before sending.
2. Type a new message in that portal and press Enter. To respond to a specific message, choose **Reply** on it first. Messages sent from the portal can publish immediately to Telegram using the connected login.
3. For a reviewed reply, switch to **Telegram Control** and follow the draft steps below. If Dot is enabled, you can also ask it to draft a new message to a connected chat, then approve it in Control.

### Review and manage drafts

- To request a draft for a group, send `telegram draft <binding-id> <source-event-id>` as a fresh message in Control. Use the latest admitted remote text from the intended portal.
- Control shows the exact draft, source link, and expiry. In Element Desktop or Element X, read the draft and use **Approve and send** or **Reject** on the matching poll. The first valid choice by the operator closes that review. The draft's own `telegram approve <draft-id> <version>` and `telegram reject <draft-id> <version>` commands remain available as new messages in Control. A new human event, policy change, expiry, or audience change can make a draft stale.
- `telegram pause <binding-id>` in Control pauses an individual binding. `telegram pause` there pauses all bindings. If Matrix is unavailable, use `telegram pause` from the VPS root console. The pause applies before a send enters the in-flight boundary.

### Dot and chat updates

When Dot is enabled, run `sudo ./constellation_matrix telegram dot-control enable` and accept the invitation with the Dot device as directed by provisioning. Refresh the private Dot MCP app to load `prepare_telegram_message` and `request_telegram_update`.

- In Dot, ask for a draft to one connected Telegram chat. Specify private chat or group and its name. For example, “Prepare a private message to Sam Example asking whether tomorrow at 15:00 works.” The exact text and verified destination appear in Control. A draft that identifies a received Matrix event in the same chat can be published as a reply.
- Only the configured operator can approve or reject an outbound message. Dot can submit a draft and see the link to its request. The plugin uses the existing private tunnel and has no send or approval tool.
- In Control, start a new message and select the real `@telegram-agent` mention. Ask “What happened today in Project Room?” or “What did Sam say in the private chat today?” The answer stays in the request thread, mentions the operator and Dot, and links to the messages it used.
- Follow up in that same thread with a real `@telegram-agent` mention to reuse its connected chat. For example, “What did they say before?” requests another update. “Rispondi” prepares a reviewable draft for the latest verified incoming text. The approval stays in the original Control thread. Start a new request to switch chats.
- Updates read one approved portal at a time. If the request is ambiguous, the connector asks for a chat name. It reads up to 30 available text messages from today in Europe/Rome, the last 24 hours by default, or the last seven days if requested. It marks partial coverage when it cannot read or verify every candidate. Telegram backfill stays disabled. The model receives only text from the selected portal for that request and has no access to Matrix or Telegram tools.
- VPS commands `report`, `pause-binding <id>`, and `status` show private operational state. A prepared Matrix request uses a stable transaction and ciphertext. Trusted mautrix status is correlated by Matrix event ID. `matrix accepted`, `bridge confirmed`, and `unknown` are distinct. Reconcile unknown outcomes before re-enabling that binding.
- After a global pause, verify destination, audience, and any in-flight effects. Run `arm-resume`, `resume`, and then `bind` for the selected conversations. A changed audience or endpoint requires pausing and `rotate` before rebinding. Remove the connector from the portal and remove its relay when revoking its ability to send.

A Telegram relay uses the selected Telegram login with bridge attribution. Matrix encryption covers the Matrix side up to mautrix. Telegram uses the protections of the selected Telegram chat. [Bridge encryption](https://docs.mau.fi/bridges/general/end-to-bridge-encryption.html)

## Repair an existing Telegram layout

Run this from the updated checkout to move the existing Space, Control room, bridge DM, and verified chats to the top-level **Telegram** Space. Use your current Matrix operator password when prompted. Your existing room history and bindings carry over.

```sh
sudo ./constellation_matrix telegram layout
```

If multiple verified bridge DMs match, choose the room you already use. For an unattended repair, pass a private `--owner-password-file` and, when needed, `--bridge-room` with the chosen Matrix room ID.

## Recovery and monitoring

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
