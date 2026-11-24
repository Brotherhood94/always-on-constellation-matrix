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

`created_by` identifies the authenticated contributor. Evidence can separately identify the Matrix account that performed an action. Late events update the neighbouring edges and a retry preserves the original event identity.

Run `bash tests/isolated` in this directory for the disposable Context Intelligence and Neo4j check.
