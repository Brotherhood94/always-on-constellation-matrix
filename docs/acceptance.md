# Acceptance and evidence

Record the deployment date, applicable version pins, check result, and a sanitized evidence summary for each run. Use `Pass`, `Fail`, or `Open` for current acceptance.

## Prior observations

| Test | Reported result and scope |
| --- | --- |
| Earlier phase encrypted alert, restore, and reboot | Observed during an earlier rollout phase. |
| Live Dot invalid synthetic ID | Exercised with an intentionally invalid synthetic ID. The response is unrecorded in the handoff. |
| Live harmless Coder to Reviewer to Coder | Completed with three attributed replies. |
| Optional Coder GitHub connection | The operator reported a successful live `GET /user` initiated from Element. |
| Optional Coder GitHub creation, PR merge, and deletion guards | Tested in simulation only at handoff. |

## Open on the current stack

| Check | Acceptance evidence to capture |
| --- | --- |
| Full post-Dot/Coder backup and isolated restore | Record the snapshot and which current components and state it recovered, including PostgreSQL, Synapse identity and media, encrypted bot state, and queues. Restore in isolation and prevent any restored device and its existing counterpart from running concurrently. Record which inputs required separate provisioning, including the Dot tunnel runtime credential and the optional Coder GitHub credential. Verify the restored Dot connection. |
| Current-stack reboot | Verify that the listed units and timer recover as applicable, loopback readiness returns, the private client and dashboard paths work, and encrypted alerts, worker dispatch, and Dot operations work after reboot. |
| Mobile alert with desktop offline | With the desktop offline, verify Element X can receive and decrypt a newly generated VPS alert. |

## Checks for each deployment

1. Verify tailnet-only access to Matrix and the dashboard at the documented ports, disabled public registration and federation, the dashboard's minute refresh, and invite-only encrypted rooms.
2. Verify Element Desktop and Element X can read encrypted room traffic after device onboarding. Verify an encrypted VPS alert and exercise alert delivery after a temporary interruption.
3. Verify each worker's identity, UID, root mount mode, and independent persistent state. Verify distinct ACP sessions across threads and a persistent workspace across threads.
4. Exercise connector validation with a permitted single target, a quote-only mention, two targets, an unauthorized sender, and an unrelated room. Only the authorized single-target event in the configured room should dispatch. Exercise a delegated review with a single-target handoff back to its delegator. Verify both stop cases, record which replies count toward the six-reply limit, and compare with the connector source.
5. Exercise an authorized Dot publication with a synthetic task ID and verify scoped status lookup. Test a retry after an uncertain result using the same task ID. Record whether a duplicate Matrix post occurs and compare with the verified retry contract before relying on deduplication. Check rejection at the tunnel and bridge authentication boundaries separately.
6. Verify queue and prepared-reply recovery across a controlled restart, including stable Matrix transaction IDs on retry. Verify monitoring cadence, alert threshold transitions, backup timing and retention, and snapshot coverage against [Runbook](runbook.md).
7. If the optional Coder GitHub integration is enabled, run a live `GET /user` through Coder's dedicated GitHub integration. Record the authenticated login in private operator evidence and confirm it matches the intended account. Record the identity check separately from the earlier simulation-only repository creation, PR merge, and deletion-guard results.
