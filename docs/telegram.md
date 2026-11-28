# Telegram integration

The optional integration creates a private, top-level **Telegram** Space for bridged contacts and groups under **People**. The **Set up Telegram** pin and private Telegram requests live in **Agents → Control Room** on the Docker control runtime. The room includes the operator, The Architect, Telegram Agent, and Dot when enabled. Telegram sign-in runs in the VPS terminal.

Older deployments may still show **Telegram → Rooms → Telegram Bridge**. After upgrading, `./constellation_matrix layout` restores the managed layout and keeps the old room’s encrypted history available.

Telegram Agent also joins **Agents → Office** as the Telegram specialist. Its dedicated Constellation Instance runs Amplifier over scoped ACP. [Acceptance scenarios](acceptance.md#7-verify-optional-integrations)

Already set up? [Search for a Telegram user](#search-for-a-telegram-user), [use Telegram from Office](#use-telegram-from-office), [open a contact or group](#add-a-telegram-contact-or-group), or [send a message](#write-to-a-contact-or-group).

## Ask Telegram Agent

In **Agents → Office**, choose **@Task Manager** from Element’s mention picker and add one request. Task Manager mentions Telegram Agent in the same thread. Telegram Agent works through the bridge and mentions you with the outcome. You can also mention **@Telegram Agent** directly in Office. Use **Agents → Control Room** for a private request or to check **Telegram status**. A send requested there produces a poll for the operator’s approval.

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

Ask “Dot, ask Task Manager in Office to send a Telegram message to Project Room saying «Hello»” or “Dot, summarize what happened today in Project Room on Telegram through Office.” Dot uses `publish_telegram_task` and reads its own result with `get_task`. Telegram Agent tags Dot in the original Office thread. Office members can read summaries posted in the room. Requests made in Control Room receive their summary there. To skip Task Manager for an explicit task, ask Dot to address Telegram Agent directly.

## Administer Telegram from Control Room

Use Element’s mention picker to select **@Telegram Agent** in **Agents → Control Room**. Send one request:

- `Telegram status` checks whether the bridge has a linked account.
- `Telegram: resync groups` refreshes Telegram group discovery and reconciles linked rooms.
- `Telegram: revoke access to Project Room` removes the agent from one verified chat and pauses its binding. The operator keeps the original chat in **Telegram → People**.
- `Telegram: disconnect account` logs out the one verified linked account. Use `./constellation_matrix telegram setup` on the VPS to link it again.
- `Telegram: restart bridge` asks The Architect to prepare a restart of the Telegram Bridge service.

Vote **Accept** or **Decline** in the encrypted poll. The first operator vote is final. A restart uses The Architect’s approval poll. A Telegram action with an uncertain result needs reconciliation before another attempt.

If Dot is connected, ask it to request the same action in Control Room. Cast your vote in Element.

## Get the Telegram API credentials

1. Sign in with your phone number and the confirmation code Telegram sends to your account. [Telegram API development tools](https://my.telegram.org/apps)

2. Open **API development tools**. Create an application or reopen your existing application. Copy **App api_id** and **App api_hash**. [Telegram API credentials](https://core.telegram.org/api/obtaining_api_id)

3. In a second VPS terminal, create a private file outside the checkout and open it in a text editor. If you already have a private Telegram API JSON file, skip to step 4.

   ```sh
   (umask 077; touch ~/telegram-api.json)
   chmod 600 ~/telegram-api.json
   nano ~/telegram-api.json
   ```

   Replace both sample values. Keep the numeric ID unquoted and the hash quoted.

   ```json
   {"api_id": 123456, "api_hash": "REPLACE_WITH_YOUR_APP_API_HASH"}
   ```

4. Paste the VPS path at the **Telegram API JSON path** prompt. Press Enter to use the displayed default. If no default file is available, Enter opens the ID prompt and the masked hash prompt.

For unattended setup, use `telegram_api_file` in the install config. The installer imports the credentials into private deployment data.

## Install the bridge

Select **Telegram** in `./constellation_matrix install`. This also installs Telegram Agent and includes it in the provider setup sequence. After the installer activates the bridge:

1. Keep the installer open on the VPS. It displays a QR code in the terminal.
2. On your phone, open **Telegram → Settings → Devices → Link Desktop Device** and scan the code.
3. If Telegram requires two-step verification, enter the password at the terminal’s hidden prompt. The wizard verifies the login and starts Telegram Agent.

The QR code refreshes automatically. To resume or link an account after an unattended install, run this on the VPS.

```sh
./constellation_matrix telegram setup
```

## Bind verified chats to the Telegram Space

A registration from Office verifies the bridge portal, joins the configured operator and Telegram Agent, and creates its binding automatically. Existing linked chats can acquire a binding when first used. Telegram Agent respects a paused binding and an explicitly unlinked chat.

## Repair an existing workspace

Use `./constellation_matrix telegram layout` to update the bridge, Synapse admission module, Telegram Agent and initial instructions while retaining the existing Telegram login and room IDs. The operator authenticates this configuration change during the command. Daily tasks use the running connector. To add Telegram Agent to a legacy bridge-only deployment, run `./constellation_matrix telegram setup --enable-agent`.

An upgrade preserves delivery receipts and stops the room migration while an in-flight delivery is unconfirmed. Eligible pending drafts move to new approval polls in Control Room. If Telegram reports an uncertain send or group creation, check the receipt and the destination before issuing a new task.

## Recovery and monitoring

Telegram Agent has its own contributor identity in the shared Context Intelligence instance. Office handoffs and outcomes carry event references and status. Telegram message text, drafts, and conversation summaries stay in the relevant encrypted Matrix room and Telegram.

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
