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
| Chief of Staff | The direct Matrix reader recorded a live encrypted thread opened in Element. A subsequent review read interactions from Neo4j, published an encrypted proposal in the dedicated room, and archived it in the graph. |
| Persona Workshop | Docker testing verified compilation, both export formats, and draft persistence across container recreation. The reference VPS created and exported a draft for Coder from an archived observation. On-host review of the draft and reuse check remain open. |
| Collaboration scenarios | Four scenarios are prepared for live execution and review. |

## Checks for each deployment

1. Verify tailnet-only access to HTTPS ports 443 and 8443, the Synapse client endpoint, private registration and federation configuration, and both invite-only encrypted rooms. Exchange an encrypted message and attachment between verified Element Desktop and Element X devices.
2. Verify the agent inventory, isolated workspaces, runtime identity, distinct ACP sessions per thread, and persistent workspace across threads. Where an external worker is enabled, verify its user units and run a read-only host check through Manager.
3. Check dispatch with a real mention, quote-only mention, multiple targets, unauthorized sender, and unrelated room. Ask a direct status question and verify a requester mention in the same thread. Exercise delegated review and a six-reply Manager chain. Check that only Manager emits the whole-task outcome on a managed route.
4. Observe 👀 after confirmed runtime start and its removal on delivery. Verify exactly one final notification to Dot and the owner and ✅ or ❌ on the original assignment. Confirm ordinary waiting emits no terminal reaction. Exercise an encrypted attachment through a handoff and verify the configured limit and user-visible rejection.
5. Publish a test plan through Dot with a synthetic task ID. Test scoped lookup, retry with the same ID, and confirm one Matrix publication. Exercise a blocked or completed managed task and check the returned status.
6. Verify collector cadence, a resource alert and recovery, and queue delivery after a controlled interruption. Confirm snapshot retention and exercise isolated restore and a controlled reboot, including the complete worker set and Dot where enabled.
7. Where GitHub is enabled, check the connected account via Coder and record the login in private operator evidence. Use a disposable repository when testing writes and repository-policy enforcement.
8. Where the Chief of Staff is enabled, compare one encrypted source thread, its graph events, the review in `Chief of Staff`, and the archived observation. Verify the contributor and the initial mentions and handoffs. Where Persona Workshop is enabled, verify both exports and their provenance. Repeat the same draft request and check that it reuses the existing export. Verify backup and restore coverage for each enabled component.

Full-stack restore and reboot with the current set of workers and Dot, and locked-screen Element X alert delivery with the desktop offline, remain deployment acceptance checks. [Runbook](runbook.md#4-monitor-and-recover)

## Chief of Staff collaboration scenarios

Use fictional inputs and two new `Agents` threads per scenario. Each pair exercises Dot, Manager, Analyst, Coder, Reviewer, and the registered External Worker within the six-reply limit per managed task. The Chief of Staff observes the exchanges. Keep any files in the assigned agent's workspace and use real Matrix mentions when starting from Element.

| Scenario | Dot request | Element request | Evidence to compare |
| --- | --- | --- | --- |
| Requirements through independent verification | Ask Manager for a parser for `north,3`, `south,2`, and `north,-1`. Route through Analyst, Coder, Reviewer, and back to Coder before Manager closes. | Ask External Worker to total the same records directly. | Both totals should be 2. Check acceptance criteria, independent verification, and who closed the task. |
| Ambiguous acceptance criteria | Ask Manager for a report that counts each category and includes blank rows in the total. External Worker counts `A, A, blank, B`, Analyst clarifies the rule, and Coder supplies an example. | Ask Reviewer for a direct judgment on `A=2, B=1, total=4` and when a separate blank row would be required. | Check that the revised criterion and assumptions survive the handoffs. |
| Blocker and recovery | Ask Manager for a validator while deliberately withholding its input schema. Analyst identifies the missing requirement. | Open a new Manager assignment with schema `id,score`, valid rows `a,3` and `b,2`, and a rule that scores are integers. External Worker checks expectations. Coder implements the check, Reviewer verifies that `c,nope` is rejected, and Coder returns the evidence for Manager to close. | The first thread identifies the blocker, who can resolve it, and the next action. The second records verified completion. |
| Direct answer and coordinated decision | Ask Reviewer to reply directly with the ambiguous word in “Retry soon after a failed check.” | Ask Manager for a two-line runbook. Give a one-minute cadence and an alert after three consecutive failures. External Worker checks that failures at 09:00, 09:01, and 09:02 first alert at 09:02. Analyst defines repeated failure, and Coder drafts the text. | The direct reply should stay in its thread. The managed result should include its verification. |

After the runs, compare the eight source threads with captured interaction events. Check that initial mentions, handoffs, and reported outcomes match Matrix. Run the [Chief of Staff review](chief-of-staff.md#review-team-evidence) and compare any proposal in Element with its archived observation. Check that the references support the proposal, simulated blockers are interpreted in context, and the recommendation has a measurable verification step. Insufficient evidence is a valid outcome. Draft a persona only when the evidence supports a role-specific change and review the draft before any assignment.

## Telegram acceptance

| Check | Local evidence | Live acceptance still required |
| --- | --- | --- |
| TG-01 to TG-03, onboarding and sender identity | Pinned mautrix started against a disposable Synapse and PostgreSQL with an empty allowlist. | The approved DM and group, excluded chats, manual round trips, relay attribution, and group type. |
| TG-04 to TG-06, Amplifier and session isolation | A real Instance and Amplifier ACP completed a synthetic provider fixture turn and loaded the same session. SQLite tests verify independent chat bindings. | The selected provider, live repeatable session continuity, service restarts, and DM/group isolation. |
| TG-07 to TG-15, draft and publish | Automated policy tests cover Control sender and room, exact approval, stale drafts, modes, retries, uncertain delivery, and pause/rotation. | The two real portals, correlated mautrix status, the pause boundary, audience changes, and one approved external message. |
| TG-16 and TG-17, encryption and authority | Two disposable Matrix accounts exchanged an encrypted connector event. Repeating the exact encrypted request returned the same Synapse event ID. | Element Desktop and Element X key sharing and reboot. Verify the real bridge and relay can decrypt and send. Verify negative authority cases through the full live path. |
| TG-18 to TG-20, operations | Compose resource limits and health, backup quarantine, and existing-stack separation are implemented. | Live dependency outages, inventory/reporting, tagged Restic recovery, and existing Agents/Dot regressions. |

Only the operator can activate external delivery for a verified binding. Record live results against the matching release and sanitized receipts. [Telegram guide](telegram.md)
