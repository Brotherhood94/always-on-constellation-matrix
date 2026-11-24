# Acceptance checks

Record the source revision, component pins, and result for each target independently.

| Feature | Automated check | Deployment acceptance |
| --- | --- | --- |
| Installation and registry | `tests/unit`, `tests/compose-smoke`, and `tests/synapse-smoke` | Run a clean install twice. Sign in to each selection, add one profile, and confirm routing and inventory after restart. |
| Element spaces and DMs | `tests/synapse-smoke` | Open Agents and verify two-person encrypted Chief of Staff and Root chats. |
| Office policies and ACP | `tests/unit` | Start an encrypted managed handoff from Element and Dot. Check transient 👀, the final ✅ or ❌, session recovery, attachments, and one delivery after retry. |
| Shared desktop | `tests/unit` and `tests/desktop-sftp` | Write a file locally, read and edit it on the remote worker, then confirm the new contents from Office. |
| Dashboard and links | `tests/unit` and `tests/dashboard-browser` | Open both pins in Element. Confirm that **Open Context Intelligence** in the dashboard reaches the same authenticated Neo4j Browser. |
| Context Intelligence | `integrations/context-execution/tests/isolated` | Verify one real thread timeline and contributor attribution for at least two agents. |
| Root and Telegram | `integrations/root/tests/run`, `tests/unit`, and the Telegram smoke scripts | Approve and reject harmless Root proposals. Send one approved Telegram fixture draft, review a chat update, and verify audience isolation. |
| Dot | `MATRIX_OPERATIONS_SMOKE=1 tests/synapse-smoke` | Use the private app over OpenAI’s tunnel to publish, retry, and read a synthetic task. Verify a different task ID stays out of scope. |
| Recovery | `tests/unit` and `VERIFY_RESTIC=1 tests/install-smoke` | Restore an isolated checkpoint before cutover, then verify service recovery after a controlled restart. |

Use separate projects, ports, and data for VPS staging. Live acceptance requires operator authentication for sudo, Element, the provider subscriptions, Telegram, and Dot. Record the results under the target where they actually ran.

## Refactor verification record

- Local isolated checks covered coordinator install and rerun, the graph browser proxy, encrypted Matrix pins and Dot task isolation, Constellation startup and ACP session restore, Root approvals, Telegram drafts and updates, and Context Intelligence timelines. The Restic checkpoint and restore ran on a native Linux fixture.
- The VPS staging checkout passed configuration validation and Python tests. The privileged VPS installation, operator sign-ins, private endpoint checks from Element, and live cutover are pending.
- The repository contains the CI workflow. A hosted CI result is recorded after the workflow has run.
