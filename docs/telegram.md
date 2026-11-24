# Telegram integration

This optional integration adds a Telegram Space, private portals, and an encrypted `Telegram Control` room. The Telegram connector is separate from `Agents` and attaches over scoped ACP to one persistent Constellation Instance running Amplifier. The integration is ready for an isolated deployment and requires live acceptance before operational use. [Acceptance evidence](acceptance.md#7-verify-optional-integrations)

## Install the bridge

Select Telegram in the Constellation installer. Use `sudo ./constellation_matrix telegram` for operator commands.

Create a private local file with mode `600` containing the operator's Telegram `api_id` (integer) and `api_hash` (string). These are application credentials from Telegram. The owner logs into Telegram separately through the bridge bot in Element. Keep credentials, codes, and passwords in the private setup flow. [Telegram authentication](https://docs.mau.fi/bridges/go/telegram/authentication.html)

```bash
sudo ./constellation_matrix telegram prepare /path/to/telegram-api.json
sudo ./constellation_matrix telegram activate
sudo ./constellation_matrix telegram status
```

`activate` briefly restarts Synapse. It starts mautrix with an empty portal allowlist and the model connector stopped. In Element, open a private conversation with `@telegrambot:<SERVER_NAME>` and use `!tg login`. `!tg list-logins` shows the login ID. `!tg resolve-identifier` can verify a known Telegram identifier without opening a portal. Confirm the peer, login, and conversation type before adding an entry:

```bash
sudo ./constellation_matrix telegram allow '<peer-type>:<peer-id>' '<login-id>'
```

Use `user` for a DM, `chat` for a basic group, and `channel` for a verified supergroup. Use positive Telegram IDs. The bridge splits portals by receiving account. Wait for an eligible message or use the documented owner-only portal management flow for that verified peer. Test the manual Telegram → Element → Telegram round trip first. Check the linked room's privacy and its outbound sender on Telegram. Space membership only controls navigation. [Bridge configuration](https://docs.mau.fi/configs/mautrix-telegram/v26.11.html) · [Go bridge setup](https://docs.mau.fi/bridges/go/setup.html)

## Start the drafting agent

The dedicated Constellation Instance runs Amplifier ACP with the Amplifier ChatGPT provider in `chatgpt_codex` mode. It uses the Codex subscription through device sign-in. The default model is `gpt-6.2-sol` with low reasoning effort for drafting. On first provisioning, set `TELEGRAM_MODEL` if the subscription needs a different supported model. [Amplifier ChatGPT provider](https://github.com/microsoft/amplifier-module-provider-openai-chatgpt)

```bash
sudo ./constellation_matrix telegram prepare-agent
sudo ./constellation_matrix telegram sign-in
sudo ./constellation_matrix telegram probe
sudo ./constellation_matrix telegram start-agent
```

Run `sign-in` from an interactive VPS terminal. Open the verification page printed there and enter the one-time code using the Codex account. The Instance saves its OAuth credentials in its own private persistent directory. The connector uses its own scoped ACP token. `probe` runs two synthetic turns and checks that the second turn resumes the Amplifier session before the connector can start.

If an earlier setup stopped with `HTTP 400 M_UNKNOWN` while creating a Matrix v12 room, rerun `prepare-agent` with the updated installer. It reuses the connector identity and initialized rooms. If a failed request left an alias on an empty room owned by the connector, it recreates the room and assigns that alias to it.

The release pins mautrix Telegram `v26.11`, Constellation Instance `0.22.17`, Amplifier ACP `0.1.7` at `3da5681942d9e9b4c51376e47ec85e69a001623a`, and the Amplifier ChatGPT provider at `cae3eaa32e16b139b8e889b8cc477c3f2d9dee31`.

The Telegram model uses a standalone Amplifier bundle. The connector records scoped delivery metadata in Context Intelligence. Dependencies are prepared when the engine image is built. If an older engine failed to activate `hook-context-intelligence`, rerun `prepare-agent` with the updated release and then rerun `probe`. A completed Codex sign-in is reused.

Accept the `Telegram` Space and `Telegram Control` invitations in Element. Control is encrypted, invite-only, and non-federated. Its members are the operator, the connector, and the optional configured Dot account. Invite `@telegram-agent:<SERVER_NAME>` to the verified portal and explicitly enable the operator's Telegram login as its relay with `!tg set-relay <login-id>`. The connector joins only a portal explicitly selected by the operator during binding. Check the agent attribution at the Telegram destination. [Relay mode](https://docs.mau.fi/bridges/general/relay-mode.html)

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

```bash
sudo ./constellation_matrix telegram bind /path/to/pilot-dm.json
```

Keep the connector running while binding. It completes a Matrix sync after joining a portal before the binding activates. A later sync gap or loss of membership pauses that binding. Use `report` to see the reason for a pause recorded by the connector.

The operator may add a verified portal to the Space if that navigation step needs to be retried. For a group, identify and verify its type before preparing a binding. `manual` records no automatic work. `draft` sends a candidate only into Control. `auto` queues a reply only in an explicitly configured binding, within its trigger, expiry, quota, audience, and outbox checks. The pilot starts with draft mode.

## Operate in Element

- Writing directly in a portal can publish to Telegram immediately. Use Control to request, approve, or reject drafts.
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
