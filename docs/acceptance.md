# Acceptance and evidence

Record the deployment date, source revision, applicable version pins, result, and a sanitized evidence summary. Use `Pass`, `Fail`, or `Open` for the deployment under test.

## Reference deployment evidence

| Check | Observed result |
| --- | --- |
| Private Matrix, encrypted alert, backup restore, and reboot | Phase 1 checks passed, and encrypted alerts and access recovered after the Phase 1 reboot. |
| Analyst and Reviewer | A live Matrix thread completed an Analyst → Reviewer → Analyst handoff. The earlier worker backup and isolated restore also passed. |
| Direct replies, rich text, and attachments | Direct status replies and outcome mentions were exercised. Rich text and encrypted attachment handoffs were verified on local Synapse. The live homeserver advertised a 60,000,000-byte upload limit. |
| Managed tasks | Five- and six-reply paths, a blocked worker, direct status, and terminal reactions passed on local Synapse. The live Dot → Manager → external worker → Manager test returned the external hostname, closed the original thread, and exposed the completed outcome to Dot. |
| Optional GitHub integration | A live read-only `GET /user` confirmed Coder's intended GitHub login. Repository creation, PR merge, and the repository deletion guard were exercised in simulation. |

## Checks for each deployment

1. Verify tailnet-only access to HTTPS ports 443 and 8443, the Synapse client endpoint, private registration and federation configuration, and both invite-only encrypted rooms. Exchange an encrypted message and attachment between verified Element Desktop and Element X devices.
2. Verify the agent inventory, isolated workspaces, runtime identity, distinct ACP sessions per thread, and persistent workspace across threads. Where an external worker is enabled, verify its user units and run a read-only host check through Manager.
3. Check dispatch with a real mention, quote-only mention, multiple targets, unauthorized sender, and unrelated room. Ask a direct status question and verify a requester mention in the same thread. Exercise delegated review and a six-reply Manager chain. Check that only Manager emits the whole-task outcome on a managed route.
4. Observe 👀 after confirmed runtime start and its removal on delivery. Verify exactly one final notification to Dot and the owner and ✅ or ❌ on the original assignment. Confirm ordinary waiting emits no terminal reaction. Exercise an encrypted attachment through a handoff and verify the configured limit and user-visible rejection.
5. Publish a test plan through Dot with a synthetic task ID. Test scoped lookup, retry with the same ID, and confirm one Matrix publication. Exercise a blocked or completed managed task and check the returned status.
6. Verify collector cadence, a resource alert and recovery, and queue delivery after a controlled interruption. Confirm snapshot retention and exercise isolated restore and a controlled reboot, including the complete worker set and Dot where enabled.
7. Where GitHub is enabled, check the connected account via Coder and record the login in private operator evidence. Use a disposable repository when testing writes and repository-policy enforcement.

Full-stack restore and reboot with the current set of workers and Dot, and locked-screen Element X alert delivery with the desktop offline, remain deployment acceptance checks. [Runbook](runbook.md#4-monitor-and-recover)
