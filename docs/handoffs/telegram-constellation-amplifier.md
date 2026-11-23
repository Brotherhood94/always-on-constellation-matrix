# Telegram implementation handoff

The implementation resides in `integrations/telegram`. This handoff retains the design and deployment boundaries. Record real Telegram identifiers, account details, and test evidence in the private operator notebook. The user selected a DM for the first pilot and a separate group for the isolation test.

## Integration map

| Component | Existing entry point | Telegram addition |
| --- | --- | --- |
| Matrix transport | The matched `matrix-stack` installer owns `compose.yaml`, `configure.py`, `provision.py`, `stack` and the Synapse data directory. | Optional separate Compose project, one AppService registration appended to Synapse config, existing client and database networks, dedicated database and roles. |
| Agent lifecycle | The matched `matrix-agents` connector owns `acp.mjs`, `matrix-session.mjs`, `store.mjs`, and the `Agents` mention router. The separate installed External Worker uses Instance and Amplifier ACP. | A dedicated Instance and Amplifier communication bundle. A distinct connector and SQLite state retain destination, approval, sync, and outbox identity. |
| Provenance and delivery | mautrix Telegram and mautrix-go preserve portal/message mappings. | Read-only SQL views verify the current exact destination and sender against the pinned bridge state. Matrix acceptance and correlated bridge status are separate events. |
| Health and recovery | The Matrix stack owns the cgroup, dashboard, Restic repository and operating-system timers. | Optional aggregate health JSON and an encrypted tagged backup, with offline quarantine. |

The reference installer layout came from local bundles `matrix-stack.tar.gz` (`a5eac47139bd…`) and `matrix-agents-pilot.tar.gz` (`2ea7735d1f81…`). They describe the discovery baseline for this integration. The actual VPS exposes Docker `29.10.0` and Compose `5.8.0` to the SSH operator. The installation commands validate the required paths and networks as root. The agent-side dependency pins are documented in the [operator guide](../telegram.md).

## Deployment gates

1. Bring up the bridge with an empty allowlist and the model connector stopped. Confirm the pinned image, dedicated database and existing Synapse access.
2. Verify the first DM's peer, receiving account, encrypted room, and owner round trip. Explicitly set a relay only on the approved portal.
3. Provision the dedicated Instance and prove a synthetic Amplifier turn and `session/load` with the selected provider. Start the connector and enroll the DM in draft mode.
4. Verify private draft review, one approved Telegram send, correct attribution, and a trusted delivery receipt. Verify directed drafting on the second, separately mapped group. Test cross-chat isolation and pause before considering automation.
5. Complete the [acceptance evidence](../acceptance.md#telegram-acceptance), isolated backup/restore, and current `Agents` and Dot regressions. Leave any topic mapping or external-provider dependency open until tested.
