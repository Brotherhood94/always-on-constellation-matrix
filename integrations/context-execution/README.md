# Context Intelligence execution timelines

This update builds on the Context Intelligence image from the paired Chief of Staff memory deployment, `matrix-context-intelligence:6.13.0-1622b48`. The overlay uses sources from [Microsoft Amplifier Context Intelligence at 1622b48](https://github.com/microsoft/amplifier-context-intelligence/tree/1622b48b05fcd93f935e5d55b962081a2c0cb229) with the execution timeline changes. [MIT license](LICENSE)

Use `NEXT_EVENT` for consecutive events within a task or a standalone native session. Use `NEXT_TASK` for consecutive tasks within one execution trace. The writer sorts by the recorded event time and uses a stable tie-breaker. A late event adjusts the neighbouring edges, and a retry does not add another edge.

The Matrix observer uses the encrypted thread as the trace and the original assignment event as the task. A new assignment in an existing thread starts the next task. A native Context Intelligence writer can supply `data.execution.trace_id`, `data.execution.task_id`, and an optional nonnegative integer `data.execution.sequence`. Writers may contribute from separate authenticated sessions to the same trace within an authorized workspace.

`created_by` is assigned from the authenticated contributor. `performed_by` on Matrix evidence records the account that performed the action. The Chief of Staff can therefore record evidence about another agent while retaining both identities.

In Neo4j Browser, show the step chains with:

```cypher
MATCH p = (a:Event)-[:NEXT_EVENT]->(b:Event)
RETURN p LIMIT 80
```

Show the task chains with:

```cypher
MATCH p = (a:ExecutionTask)-[:NEXT_TASK]->(b:ExecutionTask)
RETURN p LIMIT 80
```

The cutover creates two new data volumes and keeps the current Context Intelligence address, workspace grants, API keys, Neo4j credentials, and Matrix device. It gives the Chief of Staff a new observation cache and excludes events predating the cutover. The previous volumes and cache are detached until the operator validates the new instance and approves purging them. Existing Restic snapshots keep their normal retention.

Use `bash install cutover` from the VPS root console. Verify a new Matrix thread with a delegation and a follow-up assignment. Confirm `NEXT_EVENT`, `NEXT_TASK`, and contributor attribution. Use `bash install purge-previous SAVED_DIRECTORY` only after the operator validates and approves the reset.

Run `bash tests/isolated` from this directory to exercise the timeline with a disposable server and Neo4j database. The test uses synthetic contributors and checks ordering, late events, deduplication and workspace scope.
