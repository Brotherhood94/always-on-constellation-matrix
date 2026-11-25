# VPS Manager

Select **VPS Manager** during guided installation on a systemd host. Use `sudo ./constellation_matrix agent configure vps-manager` to finish or resume Amplifier sign-in. For a workspace with the legacy Root agent, follow the [migration steps](../../docs/runbook.md#upgrade-from-root-to-vps-manager).

1. In **Agents → Office**, choose **VPS Manager** from Element’s mention picker and ask for `matrix-stack-status`. You can also ask Manager to delegate the same check. A handoff must trace back to an Office assignment.
2. Open **Agents → Root Operations**. VPS Manager posts a request notice, the exact proposal, and an **Accept / Reject** poll. Review the target host, commands or file contents, verification, and recovery before voting. The first valid operator vote applies to that proposal. Polls expire after 15 minutes.
3. Read the full audit in Root Operations. The originating Office thread receives the outcome, an operation ID, and a link to the private review. Manager can use the Office receipt to verify the overall assignment.

VPS Manager prepares the proposal in its own Amplifier Instance. A separate host broker executes the approved plan with root privileges. Each operation requires a new vote, including read-only inspection. An interrupted execution with an unknown outcome requires the operator to check the host before approving a new operation.

| Operation | What the operator reviews |
| --- | --- |
| Inspect one file | The canonical absolute path. The private result contains a SHA-256 fingerprint and a bounded, redacted text preview. |
| Replace one file | The exact previous and replacement text, plus a separate verification command. The broker checks the previous bytes, saves the original privately, and replaces the file atomically. On verification failure it restores the original if the replacement still matches. |
| Run a host script | The exact Bash script and separate verification script. Both run as root. Review the proposal’s recovery instructions before approval. |

Keep credentials out of Office messages and proposal text. For an edit based on a completed inspection, refer to `operation <id>` in a new mention in the same Office thread. VPS Manager can use the approved private preview when drafting the next proposal. Changes to credentials should reference host-protected files rather than copying their contents into Matrix.

Run `bash integrations/root/tests/run` for the disposable encrypted Matrix and host broker fixtures. Run `bash tests/synapse-smoke` for the account migration and private room checks.
