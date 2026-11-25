# Context Intelligence execution timelines

The [deployment image](../../deploy/context.Dockerfile) builds Context Intelligence at the pinned upstream source revision and includes the timeline handlers in this directory. Office connectors contribute authenticated execution metadata. The Chief of Staff observes encrypted Matrix threads and records event and task references.

In Neo4j Browser, inspect steps within a task or adjacent tasks in a thread.

```cypher
MATCH p = (a:Event)-[:NEXT_EVENT]->(b:Event)
RETURN p LIMIT 80
```

```cypher
MATCH p = (a:ExecutionTask)-[:NEXT_TASK]->(b:ExecutionTask)
RETURN p LIMIT 80
```

Each new Office assignment gets an `ExecutionTask`, keyed by its original request event. Assignments in the same Matrix thread share an `ExecutionTrace`. Each handoff records its source, target, and dispatch event. The recipient’s authenticated writes show whether it accepted, returned, or was blocked on the handoff.

```cypher
MATCH (task:ExecutionTask {workspace: 'agents-team'})-[:HAS_HANDOFF]->(handoff:OfficeHandoff)
RETURN task.office_request AS request, handoff.source AS sender,
       handoff.target AS recipient, handoff.status AS handoff_status,
       handoff.dispatch_event AS dispatch_event
LIMIT 80
```

The task starts `in_progress`. It becomes `verified` when Manager, or the initial assignee for standalone work, references an encrypted Office reply or a delivered file as evidence. A terminal blocker from the same assignee sets `blocked`. Use `HAS_OUTCOME` to see the exact event. A direct status reply or failed subtask leaves the overall status open.

```cypher
MATCH (task:ExecutionTask {workspace: 'agents-team'})
WHERE task.office_request IS NOT NULL
OPTIONAL MATCH (task)-[:HAS_OUTCOME]->(outcome:Event)
RETURN task.office_thread AS thread, task.office_request AS request,
       task.office_assignee AS assignee, task.office_status AS status,
       outcome.office_reply AS closing_reply, outcome.office_references AS evidence
LIMIT 80
```

`created_by` identifies the authenticated contributor. Late events can fill in earlier missing handoff steps. Retries preserve the original event identity. Office receives only the sanitized VPS Manager receipt, whose `office_operation` matches `vps_operation_id` on audit events in the separate `root-operations` workspace. These Office relationships apply to new activity after the upgrade.

Run `bash tests/isolated` in this directory for the disposable Context Intelligence and Neo4j check.
