"""Materialize chronological links while preserving the native event graph."""

import hashlib
import json
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


_MATRIX_EVENT = re.compile(r"\$[^\s/#?%]{1,254}\Z")
_MATRIX_USER = re.compile(r"@([a-z0-9][a-z0-9_.=-]{0,63}):([^\s/#?%]{1,253})\Z")
_MATRIX_ROOM = re.compile(r"![^\s/#?%]{1,254}\Z")


def _office_digest(*values: str) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def office_step(event: str, evidence: Any, scope: tuple[str, str] | None, graph: Any) -> dict[str, Any]:
    if (not scope or event not in {"agents:assignment_accepted", "agents:execution"}
            or graph.workspace != "agents-team" or not isinstance(evidence, dict)):
        return {}
    room, thread, request, actor, assignment = (evidence.get(key) for key in
        ("room", "thread", "request", "actor", "assignment"))
    if not all(isinstance(value, str) and pattern.fullmatch(value) for value, pattern in
               ((room, _MATRIX_ROOM), (thread, _MATRIX_EVENT), (request, _MATRIX_EVENT),
                (actor, _MATRIX_USER), (assignment, _MATRIX_EVENT))):
        return {}
    match = _MATRIX_USER.fullmatch(actor)
    if graph.created_by != match[1] or scope != execution_ids({
        "trace_id": "agents-thread-" + _office_digest(room, thread),
        "task_id": "agents-request-" + _office_digest(room, request),
    }):
        return {}
    fields = {"office_room": room, "office_thread": thread, "office_request": request,
              "office_actor": actor, "office_assignment": assignment}
    peer = evidence.get("from" if event == "agents:assignment_accepted" else "to")
    if peer is not None and (not isinstance(peer, str) or not _MATRIX_USER.fullmatch(peer)
                             or peer == actor or not peer.endswith(":" + match[2])):
        return {}
    if event == "agents:assignment_accepted":
        if (assignment == request) != (peer is None):
            return {}
        return {**fields, "office_kind": "accepted", **({"office_from": peer} if peer else {})}
    reply = evidence.get("reply")
    if not isinstance(reply, str) or not _MATRIX_EVENT.fullmatch(reply):
        return {}
    fields.update(office_kind="reply", office_reply=reply)
    flow = evidence.get("flow")
    if flow is not None and (flow not in ("dispatch", "return") or not peer):
        return {}
    if flow:
        fields["office_flow"] = flow
    revision = evidence.get("policyRevision")
    if flow == "dispatch" and isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{32}", revision):
        fields["office_policy_revision"] = revision
    if peer:
        fields["office_to"] = peer
        if flow != "return":
            fields["office_handoff"] = "office-handoff:" + _office_digest(room, reply)
    if evidence.get("step") in ("returned", "blocked"):
        fields["office_step"] = evidence["step"]
    references = evidence.get("references", [])
    if not isinstance(references, list) or len(references) > 16 or not all(
            isinstance(ref, str) and _MATRIX_EVENT.fullmatch(ref) and ref not in {request, reply} for ref in references):
        return {}
    outcome = evidence.get("outcome")
    if not peer and outcome in ("completed", "blocked"):
        fields["office_outcome"] = "verified" if outcome == "completed" and references else (
            "blocked" if outcome == "blocked" else "in_progress")
    if references:
        fields["office_references"] = references
    receipt = evidence.get("operation")
    if match[1] == "the-architect" and isinstance(receipt, str) and re.fullmatch(r"[a-f0-9]{32}", receipt):
        fields["office_operation"] = receipt
    return fields


async def attach_office_step(graph: Any, task: str, event_id: str, office: dict[str, Any]) -> None:
    if handoff := office.get("office_handoff"):
        await graph.upsert_node(handoff, {"labels": ["OfficeHandoff"], "source": office["office_actor"],
                                         "target": office["office_to"], "dispatch_event": office["office_reply"],
                                         **({"classification": "dispatch"} if office.get("office_flow") == "dispatch" else {}),
                                         **({"policy_revision": office["office_policy_revision"]}
                                            if "office_policy_revision" in office else {})})
        await graph.upsert_edge(task, handoff, {"type": "HAS_HANDOFF"})
        await graph.upsert_edge(handoff, event_id, {"type": "DISPATCHED_IN"})


_OFFICE_OUTCOME: LiteralString = """
MATCH (t:ExecutionTask:Node {workspace:$workspace,node_id:$owner})-[:HAS_STEP]->(event:Event)
WHERE event.office_kind IS NOT NULL
WITH t, head(collect(event.office_request)) AS request,
     head(collect(event.office_room)) AS room, head(collect(event.office_thread)) AS thread
OPTIONAL MATCH (t)-[:HAS_STEP]->(first:Event {office_kind:'accepted'})
WHERE first.office_assignment = request AND first.office_from IS NULL
WITH t,request,room,thread,first ORDER BY first.occurred_at,first.node_id
WITH t,request,room,thread,head(collect(first)) AS assigned
OPTIONAL MATCH (t)-[:HAS_STEP]->(outcome:Event)
WHERE outcome.office_actor = assigned.office_actor AND outcome.office_outcome IN ['verified','blocked']
WITH t,request,room,thread,assigned,outcome
ORDER BY outcome.execution_sequence,outcome.occurred_at,outcome.node_id
WITH t,request,room,thread,assigned,head(collect(outcome)) AS final
SET t.office_request = request, t.office_room = room, t.office_thread = thread,
    t.office_assignee = assigned.office_actor, t.office_status = coalesce(final.office_outcome,'in_progress')
WITH t,final
OPTIONAL MATCH (t)-[old:HAS_OUTCOME]->(previous:Event)
WHERE final IS NULL OR previous <> final
DELETE old
WITH DISTINCT t,final
FOREACH (_ IN CASE WHEN final IS NULL THEN [] ELSE [1] END | MERGE (t)-[:HAS_OUTCOME]->(final))
"""

_OFFICE_HANDOFFS: LiteralString = """
MATCH (t:ExecutionTask:Node {workspace:$workspace,node_id:$owner})-[:HAS_HANDOFF]->(h:OfficeHandoff)
OPTIONAL MATCH (t)-[:HAS_STEP]->(ack:Event {office_kind:'accepted'})
WHERE ack.office_assignment=h.dispatch_event AND ack.office_actor=h.target AND ack.office_from=h.source
WITH t,h,head(collect(ack)) AS accepted
OPTIONAL MATCH (t)-[:HAS_STEP]->(result:Event {office_kind:'reply'})
WHERE result.office_assignment=h.dispatch_event AND result.office_actor=h.target
      AND coalesce(result.office_flow,'legacy') <> 'dispatch'
      AND (h.classification IS NULL OR result.office_flow='return' OR result.office_to IS NULL)
WITH h,accepted,result ORDER BY result.occurred_at,result.node_id
WITH h,accepted,head(collect(result)) AS returned
SET h.status = CASE WHEN accepted IS NULL THEN 'dispatched' WHEN returned IS NULL THEN 'accepted'
                    ELSE coalesce(returned.office_step,'returned') END
FOREACH (_ IN CASE WHEN accepted IS NULL THEN [] ELSE [1] END | MERGE (h)-[:ACCEPTED_IN]->(accepted))
FOREACH (_ IN CASE WHEN accepted IS NULL OR returned IS NULL THEN [] ELSE [1] END | MERGE (h)-[:RETURNED_IN]->(returned))
"""


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
        if workspace == "agents-team" and query == _TASK_EVENTS:
            for projection in (_OFFICE_OUTCOME, _OFFICE_HANDOFFS):
                await (await tx.run(projection, workspace=workspace, owner=owner)).consume()


def timing_fields(value, graph):
    if not isinstance(value, dict) or not _KEY.fullmatch(str(value.get('id') or '')):
        return {}
    kind = value.get('kind')
    if not isinstance(kind, str) or not re.fullmatch('[a-z][a-z0-9-]{0,31}', kind):
        return {}
    outcome = value.get('outcome') or 'recorded'
    if not isinstance(outcome, str) or not re.fullmatch('[a-z][a-z0-9_-]{0,31}', outcome):
        return {}
    phases = {name: value[name] for name in ('total_ms', 'work_ms', 'queue_ms', 'approval_ms')
              if type(value.get(name)) is int and 0 <= value[name] <= 90 * 86400 * 1000}
    actor = value.get('actor')
    attributed = graph.created_by == 'office-topology@deployment' and isinstance(actor, str) and re.fullmatch(r'[a-z0-9_-]{1,64}', actor)
    environment = value.get('environment')
    return {'timing_environment': 'twin' if (isinstance(environment, str) and re.fullmatch(r'[a-f0-9]{32}', environment)) or str(actor).startswith('tw-') else 'workspace',
            'timing_id': value['id'], 'timing_kind': kind, 'timing_actor': actor if attributed else graph.created_by,
            'timing_outcome': outcome, **{'timing_' + name: value for name, value in phases.items()}}
