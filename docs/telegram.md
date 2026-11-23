# Telegram integration

This optional integration adds a Telegram Space, private portals, and an encrypted `Telegram Control` room. The Telegram connector is separate from `Agents` and attaches over scoped ACP to one persistent Constellation Instance running Amplifier. The integration is ready for an isolated deployment and requires live acceptance before operational use. [Acceptance evidence](acceptance.md#telegram-acceptance)

## Install the bridge

Use a verified source archive from a recorded Git commit. Copy the contents of `integrations/telegram` into `/opt/matrix-telegram` on the VPS. The installer expects the existing Compose projects under `/opt/matrix-stack`, a JSON-compatible Synapse configuration, the stack's local account registration helper, and the existing PostgreSQL and Matrix networks. It adds a dedicated PostgreSQL database and roles and an AppService registration. Synapse configuration is backed up before activation.

The installer reads the operator's Matrix ID from the existing stack or agent configuration. On a deployment without that setting, pass `MATRIX_OWNER='@<OWNER_HANDLE>:<SERVER_NAME>'` to both `prepare` and `prepare-agent`.

Create a private local file with mode `600` containing the operator's Telegram `api_id` (integer) and `api_hash` (string). These are application credentials from Telegram. The owner logs into Telegram separately through the bridge bot in Element. Keep credentials, codes, and passwords in the private setup flow. [Telegram authentication](https://docs.mau.fi/bridges/go/telegram/authentication.html)

```bash
/opt/matrix-telegram/telegram prepare /root/telegram-api.json
/opt/matrix-telegram/telegram activate
/opt/matrix-telegram/telegram status
```

`activate` briefly restarts Synapse. It starts mautrix with an empty portal allowlist and the model connector stopped. In Element, open a private conversation with `@telegrambot:<SERVER_NAME>` and use `!tg login`. `!tg list-logins` shows the login ID. `!tg resolve-identifier` can verify a known Telegram identifier without opening a portal. Confirm the peer, login, and conversation type before adding an entry:

```bash
/opt/matrix-telegram/telegram allow '<peer-type>:<peer-id>' '<login-id>'
```

Use `user` for a DM, `chat` for a basic group, and `channel` for a verified supergroup. Use positive Telegram IDs. The bridge splits portals by receiving account. Wait for an eligible message or use the documented owner-only portal management flow for that verified peer. Test the manual Telegram → Element → Telegram round trip first. Check the linked room's privacy and its outbound sender on Telegram. Space membership only controls navigation. [Bridge configuration](https://docs.mau.fi/configs/mautrix-telegram/v26.11.html) · [Go bridge setup](https://docs.mau.fi/bridges/go/setup.html)

## Start the drafting agent

The dedicated Constellation Instance runs Amplifier ACP with the Amplifier ChatGPT provider in `chatgpt_codex` mode. It uses the Codex subscription through device sign-in. The default model is `gpt-6.2-sol` with low reasoning effort for drafting. On first provisioning, set `TELEGRAM_MODEL` if the subscription needs a different supported model. [Amplifier ChatGPT provider](https://github.com/microsoft/amplifier-module-provider-openai-chatgpt)

```bash
/opt/matrix-telegram/telegram prepare-agent
/opt/matrix-telegram/telegram sign-in
/opt/matrix-telegram/telegram probe
/opt/matrix-telegram/telegram start-agent
```

Run `sign-in` from an interactive VPS terminal. Open the verification page printed there and enter the one-time code using the Codex account. The Instance saves its OAuth credentials in its own private persistent directory. The connector uses its own scoped ACP token. `probe` runs two synthetic turns and checks that the second turn resumes the Amplifier session before the connector can start.

The release pins mautrix Telegram `v26.11`, Constellation Instance `0.22.17`, Amplifier ACP `0.1.7` at `3da5681942d9e9b4c51376e47ec85e69a001623a`, and the Amplifier ChatGPT provider at `cae3eaa32e16b139b8e889b8cc477c3f2d9dee31`.

Accept the `Telegram` Space and `Telegram Control` invitations in Element. The connector checks that Control is encrypted, invite-only, non-federated, and joined only by the operator and connector. Invite `@telegram-agent:<SERVER_NAME>` to the verified portal and explicitly enable the operator's Telegram login as its relay with `!tg set-relay <login-id>`. The connector joins only a portal explicitly selected by the operator during binding. Check the agent attribution at the Telegram destination. [Relay mode](https://docs.mau.fi/bridges/general/relay-mode.html)

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
/opt/matrix-telegram/telegram bind /root/pilot-dm.json
```

The operator may add a verified portal to the Space if that navigation step needs to be retried. For a group, identify and verify its type before preparing a binding. `manual` records no automatic work. `draft` sends a candidate only into Control. `auto` queues a reply only in an explicitly configured binding, within its trigger, expiry, quota, audience, and outbox checks. The pilot starts with draft mode.

## Operate in Element

- Writing directly in a portal can publish to Telegram immediately. Use Control to request, approve, or reject drafts.
- To request a draft for a group, send `telegram draft <binding-id> <source-event-id>` as a fresh message in Control. Use the latest admitted remote text from the intended portal.
- Control shows the exact draft, source link, expiry, and the specific `telegram approve <draft-id> <version>` and `telegram reject <draft-id> <version>` commands. Send commands as new messages. A new human event, policy change, expiry, or audience change can make a draft stale.
- `telegram pause <binding-id>` in Control pauses an individual binding. `telegram pause` there pauses all bindings. If Matrix is unavailable, use `telegram pause` from the VPS root console. The pause applies before a send enters the in-flight boundary.
- VPS commands `report`, `pause-binding <id>`, and `status` show private operational state. A prepared Matrix request uses a stable transaction and ciphertext. Trusted mautrix status is correlated by Matrix event ID. `matrix accepted`, `bridge confirmed`, and `unknown` are distinct. Reconcile unknown outcomes before re-enabling that binding.
- After a global pause, verify destination, audience, and any in-flight effects. Run `arm-resume`, `resume`, and then `bind` for the selected conversations. A changed audience or endpoint requires pausing and `rotate` before rebinding. Remove the connector from the portal and remove its relay when revoking its ability to send.

A Telegram relay uses the selected Telegram login with bridge attribution. Matrix encryption covers the Matrix side up to mautrix. Telegram uses the protections of the selected Telegram chat. [Bridge encryption](https://docs.mau.fi/bridges/general/end-to-bridge-encryption.html)

## Recovery and monitoring

The Compose services use the existing Matrix cgroup and restart with Docker. `telegram metrics` exports sanitized health and queue counts to `telegram.json` under the private resource dashboard origin. `telegram enable-operations` installs the corresponding systemd timers from the fixed `/opt/matrix-telegram` path. Enable timers after acceptance on the host.

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
