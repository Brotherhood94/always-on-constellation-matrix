# Send Telegram messages through Dot

This plan covers a future release. The current [Telegram Control workflow](../docs/telegram.md#dot-and-chat-updates) supports drafts, updates, and operator approval. The [remote worker runbook](../docs/runbook.md#add-a-remote-worker) covers the separate setup on the VPS and worker.

## Entry points and chat matching

- In ChatGPT, accept natural-language send requests through Dot’s private, operator-only **Constellation Office** app. In Element, accept the same requests from the operator with a real `@telegram-agent` mention in the encrypted **Telegram → Telegram Control** room. Reuse that room for notices and receipts.
- Use one routing interface for supported channels. Telegram Control implies Telegram. Dot routes explicit Telegram requests to that adapter. If the channel is unclear, ask which one to use. Keep the interface ready for a later WhatsApp adapter.
- Use Dot’s existing model to interpret ChatGPT requests and the existing Telegram Amplifier Instance for Element requests. Extract the requested action, recipient name, text, and optional attachment path. Use deterministic rules to pick the destination from verified bindings.
- Derive the directory from active, verified bindings. Use each chat’s full display name, stable binding ID, chat type, and optional operator-defined aliases. Recheck the live binding and room state so additions, renames, pauses, and removals take effect. A group participant becomes a separate destination only when their own chat is registered.
- Respect an explicitly requested DM or group type. Prefer the exact binding ID or normalized full name or alias, then accept a unique whole-word prefix. For “Scrivi ad Eugenio su Telegram che domani sarà una bella giornata,” select **Eugenio INF** if it is the only eligible match. If candidates tie, show their full names and chat types and ask the operator to choose. If none match, ask for the registered chat name.

## Send preview and delivery

- An explicit operator send request can send to any active, verified binding that permits sending. An explicit draft request keeps the existing review and approval flow.
- Post the full destination name and chat type, exact message, and any attachment name to the encrypted Control thread. Start the 10-second preview window after Matrix confirms the notice. Dot shows the same full destination and thread link in ChatGPT. Sending proceeds automatically after the window without a per-message approval.
- Tie the pending send to the selected binding and its verified audience, the exact payload, and the notice event. Recheck destination, relay, audience, and send permissions immediately before publication. If anything changed, stop and report it in the same thread. Use the existing delivery journal and stable transaction identities across retries and restarts.
- Return status in the Control thread. Let Dot retrieve status for requests it opened. Report **Delivered** after bridge confirmation. Preserve unknown outcomes for reconciliation and leave enough evidence to distinguish a Matrix acceptance from Telegram delivery.

## Attach a file from the VPS

- Accept one explicitly named file from `<data-dir>/desktop/telegram-outbox/`. Give the Telegram connector read-only access to that folder. Accept only relative paths to regular files. Reject traversal and symlinks, and enforce the tested size limit before announcing the send.
- Create a private snapshot before publishing the preview so the exact announced file is delivered even if the shared copy changes. Publish the file and text as one captioned Telegram post using encrypted Matrix media. Report a missing, unreadable, or unsupported file before sending.

## Verification

- Exercise exact and partial names, aliases, ambiguous names, DM versus group, group-member exclusion, and additions and removals from the directory. Run the Eugenio example through both entry points with synthetic accounts.
- Check that the full-name notice precedes sending by at least 10 seconds, that direct sends proceed automatically, and that drafts still wait for approval. Change a binding during the preview and verify that nothing is sent.
- Verify file containment, attachment snapshot integrity, replay across restart, and unknown bridge receipts. Run one text send and one captioned file send to a designated live Telegram test chat.
