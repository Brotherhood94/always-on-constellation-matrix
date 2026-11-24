# Root Operations

Root is a dedicated Constellation Instance with Amplifier over ACP. It uses its own Codex subscription sign-in and private state. The Matrix gateway and the Amplifier process run in unprivileged containers. A separate host-side systemd broker runs the exact approved action and records its exit code.

## Install and activate

1. Start with the [matching Matrix deployment](../../docs/runbook.md) and with Context Intelligence available. Build the Telegram engine and connector images pinned in [the Telegram integration](../../docs/telegram.md). Root uses these images with its own instance files and Matrix device.
2. From this directory in the VPS root console, run `bash root-agent install`. The installer provisions the private review room and a scoped Context Intelligence writer. Accept the **Root Operations** invitation. If automatic invitation is unavailable, invite `@root:<SERVER_NAME>` to **Agents**.
3. Run `/opt/matrix-root-operations/root-agent sign-in` interactively and complete the device authorization. Run `/opt/matrix-root-operations/root-agent activate` to verify the model and start the gateway.
4. Run `bash updates/reviewed-scripts/install`, then `bash updates/proposal-validation/install` from this directory. The installers verify the expected source baselines and print the rollback locations. The first also verifies the loopback Synapse port and restarts the broker. The second updates the gateway's proposal parser.

## Propose and approve an operation

Mention Root in an authorized **Agents** thread. Root prepares a proposal and publishes the purpose, host, impact, expected result, and exact script with its SHA-256 fingerprint in **Root Operations**. The Matrix poll links to the proposal and offers **Accept** and **Reject**. Only the configured operator account can decide. The first valid vote is final and an unanswered proposal expires after 15 minutes.

Use a fixed operation for a matching registered command. For a new script, Root can draft a proposal through Amplifier. A caller who already has the exact script can provide a final visible `ROOT_REVIEW:` line containing a single JSON object with `operation: "reviewed-script"`, `summary`, `impact`, `verification` and `script`. Optional fields are `recovery` and `failure_reasons`. The gateway validates the submitted bytes directly. When Manager hands off, its separate routing control line follows the submission and is removed by the Matrix connector.

The broker checks the approval and script fingerprint. A repeat receipt returns the existing outcome. Rejection ends the request without starting the script. Root publishes the result in the originating thread and mentions the delegating agent or Manager as appropriate. An approved failure includes the exit code and any declared cause. A corrected request needs a fresh vote.

## Audit and verify

The private room records the decision separately from execution. Context Intelligence stores the request, proposal, decision and outcome as chronological evidence. Audit records carry the authenticated contributor, operation ID and Matrix references. The executor discards command output and returns the exit status.

For an isolated check, run `node tests/protocol-test.mjs` and `bash tests/run` from this directory. The latter uses the pinned Telegram connector image, a disposable Synapse, and a simulated Amplifier response. It tests encrypted approval, rejection, the original thread receipt, exact script bytes and the broker's replay handling. A matching locally built connector image can be supplied through `TELEGRAM_CONNECTOR_TEST_IMAGE`.
