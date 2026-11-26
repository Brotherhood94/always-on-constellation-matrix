# Telegram integration

The optional integration creates a private, top-level **Telegram** Space. **Rooms → Telegram Bridge** contains setup, bridge commands, recipient selection, drafts, updates, and approvals. **People** contains bridged contacts and groups. Only the operator, `@telegrambot`, and `@telegram-agent` join Telegram Bridge. Open its **Set up Telegram** pin to get started.

Telegram Agent also joins **Agents → Office** as the Telegram specialist. Its dedicated Constellation Instance runs Amplifier over scoped ACP. [Acceptance scenarios](acceptance.md#7-verify-optional-integrations)

Already set up? [Search for a Telegram user](#search-for-a-telegram-user), [use Telegram from Office](#use-telegram-from-office), [open a contact or group in Telegram Bridge](#add-a-telegram-contact-or-group), or [send a message](#write-to-a-contact-or-group).

## Ask Telegram Agent

In **Agents → Office**, choose **@Task Manager** from Element’s mention picker and add one request. Task Manager mentions Telegram Agent in the same thread. Telegram Agent works through the bridge and mentions you with the outcome. You can also mention **@Telegram Agent** directly in **Telegram → Rooms → Telegram Bridge** for a private request.

## Use Telegram from Office

| Action | Text after the Task Manager mention |
| --- | --- |
| Register a contact | `Telegram: open the chat with @sample_contact.` |
| Register an existing group | `Telegram: open the group Project Room.` |
| Create a group | `Telegram: create a group called Project Room with @sample_contact.` |
| Add a participant | `Telegram: add @sample_contact to the group Project Room.` |
| Send a message | `Telegram: send a message to Project Room saying «Tomorrow works for me».` |
| Preview a draft | `Telegram: prepare a message for Project Room saying «Tomorrow works for me».` |
| Read a conversation | `Telegram: summarize what happened today in Project Room.` |
| Unlink a chat | `Telegram: remove Project Room from my rooms.` |

An explicit send delivers to the verified chat after resolution and returns the bridge’s receipt. Quoted text retains its punctuation and line breaks. Drafts are previews. Search and registration can look up contacts and known groups outside the Space. Drafts, sends and summaries use the registered chats listed under **Telegram → People**. Telegram Agent chooses a clear match automatically. An ambiguous match gets one question in the originating thread. Reply there with `telegram choose 2`, using the displayed number.

Group creation uses Telegram’s standard group type and needs at least one participant. The result includes the group link and each named participant’s status. Groups already accessible to your linked Telegram account can also accept new participants. Unlinking a chat removes the agent’s access and keeps your conversation.

### Dot and chat updates

Ask “Dot, ask Task Manager in Office to send a Telegram message to Project Room saying «Hello»” or “Dot, summarize what happened today in Project Room on Telegram through Office.” Dot uses `publish_telegram_task` and reads its own result with `get_task`. Telegram Agent tags Dot in the original Office thread. Office members can read summaries posted in the room. Requests made in Telegram Bridge receive their summary there.

## Get the Telegram API credentials

In a browser, sign in at [Telegram API development tools](https://my.telegram.org/apps) and open or create an application. The installer accepts the numeric **App api_id** and prompts privately for the **App api_hash**, or imports an existing private `~/telegram-api.json` with mode `0600`. Use `telegram_api_file` in an unattended install config to import that file and start the bridge before the operator signs in. The installer keeps the bridge’s credentials in its private deployment data.

## Install the bridge

Select **Telegram** in `./constellation_matrix install`. This also installs Telegram Agent and includes it in the provider setup sequence. After the installer activates the bridge:

1. In Element, open **Telegram → Rooms → Telegram Bridge** and send `!tg login qr`.
2. On your phone, use **Telegram → Settings → Devices → Link Desktop Device** to scan the QR code. If the bridge asks for your two-step verification password, send a new message to Telegram Bridge with `!tg ` followed by your Telegram password.
3. Return to the terminal after the bot confirms sign-in. The wizard verifies the login and starts Telegram Agent.

If a QR code expires, send `!tg cancel` and then `!tg login qr`. To resume any unfinished step, run:

```sh
./constellation_matrix setup-guide
```

## Bind verified chats to the Telegram Space

A registration from Office verifies the bridge portal, joins the configured operator and Telegram Agent, and creates its binding automatically. Existing linked chats can acquire a binding when first used. Telegram Agent respects a paused binding and an explicitly unlinked chat.

## Repair an existing workspace

Use `./constellation_matrix telegram layout` to update the bridge, Synapse admission module, Telegram Agent and initial instructions while retaining the existing Telegram login and room IDs. The operator authenticates this configuration change during the command. Daily tasks use the running connector. To add Telegram Agent to a legacy bridge-only deployment, run `./constellation_matrix telegram setup --enable-agent`.

An upgrade preserves delivery receipts and reconciles in-flight messages. Existing unapproved previews require a new explicit send request. If Telegram reports an uncertain send or group creation, check the receipt and the destination before issuing a new task.

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
