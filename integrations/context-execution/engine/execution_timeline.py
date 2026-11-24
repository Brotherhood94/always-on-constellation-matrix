"""Materialize chronological links while preserving the native event graph."""

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any, LiteralString

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
_MIN_EPOCH_MS = int(os.environ.get("CONTEXT_EXECUTION_MIN_EPOCH_MS", "0"))


def before_cutover(timestamp: str) -> bool:
    if not _MIN_EPOCH_MS:
        return False
    observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return int(observed.timestamp() * 1000) < _MIN_EPOCH_MS


def execution_ids(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    trace, task = value.get("trace_id"), value.get("task_id")
    if not all(isinstance(key, str) and _KEY.fullmatch(key) for key in (trace, task)):
        return None
    trace_key = hashlib.sha256(trace.encode()).hexdigest()
    task_key = hashlib.sha256(f"{trace}\0{task}".encode()).hexdigest()
    return f"execution-trace:{trace_key}", f"execution-task:{task_key}"


async def attach_execution(graph: Any, event_id: str, scope: tuple[str, str]) -> None:
    trace, task = scope
    await graph.upsert_node(trace, {"labels": ["ExecutionTrace"]})
    await graph.upsert_node(task, {"labels": ["ExecutionTask"], "execution_trace_id": trace})
    await graph.upsert_edge(trace, task, {"type": "HAS_TASK"})
    await graph.upsert_edge(task, event_id, {"type": "HAS_STEP"})


_LOCK: LiteralString = """
MATCH (owner:Node {workspace:$workspace,node_id:$owner})
SET owner.execution_timeline_revision = coalesce(owner.execution_timeline_revision,0) + 1
"""
_TRACES: LiteralString = """
UNWIND $tasks AS id
MATCH (task:ExecutionTask:Node {workspace:$workspace,node_id:id})
RETURN DISTINCT task.execution_trace_id AS trace
"""
_SESSION_EVENTS: LiteralString = """
MATCH (:Session:Node {workspace:$workspace,node_id:$owner})-[:HAS_EVENT]->(e:Event)
WHERE e.workspace=$workspace AND e.execution_task_id IS NULL
RETURN e.node_id AS id ORDER BY e.occurred_at, e.execution_sequence, id
"""
_TASK_EVENTS: LiteralString = """
MATCH (:ExecutionTask:Node {workspace:$workspace,node_id:$owner})-[:HAS_STEP]->(e:Event)
WHERE e.workspace=$workspace
RETURN e.node_id AS id ORDER BY e.occurred_at, e.execution_sequence, id
"""
_TRACE_TASKS: LiteralString = """
MATCH (:ExecutionTrace:Node {workspace:$workspace,node_id:$owner})-[:HAS_TASK]->(t:ExecutionTask)-[:HAS_STEP]->(e:Event)
WHERE t.workspace=$workspace AND e.workspace=$workspace
RETURN t.node_id AS id, min(e.occurred_at) AS first_at ORDER BY first_at, id
"""

_EVENT_EDGES: LiteralString = """
UNWIND $nodes AS id
MATCH (a:Node {workspace:$workspace,node_id:id})-[r:NEXT_EVENT {timeline_id:$owner,workspace:$workspace}]->(b:Node)
RETURN a.node_id AS source,b.node_id AS target
"""
_TASK_EDGES: LiteralString = """
UNWIND $nodes AS id
MATCH (a:Node {workspace:$workspace,node_id:id})-[r:NEXT_TASK {timeline_id:$owner,workspace:$workspace}]->(b:Node)
RETURN a.node_id AS source,b.node_id AS target
"""
_DELETE_EVENTS: LiteralString = """
UNWIND $pairs AS pair
MATCH (:Node {workspace:$workspace,node_id:pair[0]})-[r:NEXT_EVENT {timeline_id:$owner,workspace:$workspace}]->(:Node {workspace:$workspace,node_id:pair[1]})
DELETE r
"""
_DELETE_TASKS: LiteralString = """
UNWIND $pairs AS pair
MATCH (:Node {workspace:$workspace,node_id:pair[0]})-[r:NEXT_TASK {timeline_id:$owner,workspace:$workspace}]->(:Node {workspace:$workspace,node_id:pair[1]})
DELETE r
"""
_MERGE_EVENTS: LiteralString = """
UNWIND $pairs AS pair
MATCH (a:Node {workspace:$workspace,node_id:pair[0]}), (b:Node {workspace:$workspace,node_id:pair[1]})
MERGE (a)-[:NEXT_EVENT {timeline_id:$owner,workspace:$workspace}]->(b)
"""
_MERGE_TASKS: LiteralString = """
UNWIND $pairs AS pair
MATCH (a:Node {workspace:$workspace,node_id:pair[0]}), (b:Node {workspace:$workspace,node_id:pair[1]})
MERGE (a)-[:NEXT_TASK {timeline_id:$owner,workspace:$workspace}]->(b)
"""


async def _reconcile(tx: Any, ordered: LiteralString, workspace: str, owner: str) -> None:
    params = {"workspace": workspace, "owner": owner}
    result = await tx.run(ordered, **params)
    ids = [row["id"] async for row in result]
    await result.consume()
    if not ids:
        return
    task_order = ordered == _TRACE_TASKS
    read = _TASK_EDGES if task_order else _EVENT_EDGES
    result = await tx.run(read, nodes=ids, **params)
    current = {(row["source"], row["target"]) async for row in result}
    await result.consume()
    desired = set(zip(ids, ids[1:]))
    if obsolete := sorted(current - desired):
        command = _DELETE_TASKS if task_order else _DELETE_EVENTS
        await (await tx.run(command, pairs=[list(pair) for pair in obsolete], **params)).consume()
    if new := sorted(desired - current):
        command = _MERGE_TASKS if task_order else _MERGE_EVENTS
        await (await tx.run(command, pairs=[list(pair) for pair in new], **params)).consume()


async def update_timelines(tx: Any, groups: dict[str, list[dict]], workspace: str) -> None:
    sessions = {row["src_id"] for row in groups.get("HAS_EVENT", [])}
    tasks = {row["src_id"] for row in groups.get("HAS_STEP", [])}
    traces = {row["src_id"] for row in groups.get("HAS_TASK", [])}
    if tasks:
        result = await tx.run(_TRACES, tasks=sorted(tasks), workspace=workspace)
        traces.update({row["trace"] async for row in result if row["trace"]})
        await result.consume()
    timelines = {**dict.fromkeys(sessions, _SESSION_EVENTS), **dict.fromkeys(tasks, _TASK_EVENTS),
                 **dict.fromkeys(traces, _TRACE_TASKS)}
    for owner, query in sorted(timelines.items()):
        await (await tx.run(_LOCK, workspace=workspace, owner=owner)).consume()
        await _reconcile(tx, query, workspace, owner)
