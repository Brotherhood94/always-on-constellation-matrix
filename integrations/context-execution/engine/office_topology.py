"""Store authenticated contact revisions and report directed Office handoffs."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from neo4j import READ_ACCESS
from pydantic import BaseModel, ConfigDict, Field

from context_intelligence_server.authz import require_read, require_workspace_access, require_write

router = APIRouter(prefix='/office/topology')
WORKSPACE = 'agents-team'
PUBLISHER = 'office-topology@deployment'
KEY = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
ROOM = re.compile(r'![^\s/#?%]{1,511}')
EVENT = re.compile(r'\$[^\s/#?%]{1,254}')


class Revision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    sequence: int = Field(ge=1)
    kind: str = Field(pattern=r'^(baseline|change)$')
    at: datetime
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=80)
    enforced: bool
    previous_enforced: bool
    added: list[tuple[str, str]] = Field(max_length=16256)
    removed: list[tuple[str, str]] = Field(max_length=16256)
    joined: list[str] = Field(max_length=128)
    left: list[str] = Field(max_length=128)
    renamed: dict[str, str] = Field(max_length=128)
    names: dict[str, str] = Field(max_length=256)
    evidence: str | None = None
    approval: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    def verify(self):
        required_names = set(self.joined + self.left + list(self.renamed))
        for source, target in self.added + self.removed:
            if source == target:
                raise HTTPException(422, 'A contact requires two agents')
            required_names.update((source, target))
        if (required_names != self.names.keys() or not all(KEY.fullmatch(key) for key in required_names)
                or not all(0 < len(name) <= 64 and name.isprintable()
                           for name in [*self.names.values(), *self.renamed.values()])
                or self.at.utcoffset() is None or (self.kind == 'baseline') != (self.sequence == 1)
                or (self.evidence is not None and not EVENT.fullmatch(self.evidence))):
            raise HTTPException(422, 'Invalid contact revision')


class Office(BaseModel):
    model_config = ConfigDict(extra='forbid')
    room: str

    def head(self):
        if not ROOM.fullmatch(self.room):
            raise HTTPException(422, 'Invalid Office room')
        return 'office-topology:' + sha256(self.room.encode()).hexdigest()


class PublishedRevision(Office):
    revision: Revision


async def append_revision(tx, head, revision):
    params = {'workspace': WORKSPACE, 'head': head, 'id': head + ':' + revision.id}
    result = await tx.run(
        'MERGE (head:OfficeTopology:Node {workspace:$workspace,node_id:$head}) '
        'SET head.write_lock=coalesce(head.write_lock,0)+1 '
        'WITH head '
        'OPTIONAL MATCH (head)-[:HAS_CONTACT_REVISION]->(old:OfficeContactRevision {node_id:$id}) '
        'RETURN coalesce(head.sequence,0) AS sequence, old.payload AS payload', params)
    current = await result.single(strict=True)
    payload = json.dumps(revision.model_dump(mode='json', exclude_none=True), ensure_ascii=False, sort_keys=True)
    if current['payload'] == payload:
        return
    if current['payload'] is not None or current['sequence'] + 1 != revision.sequence:
        raise HTTPException(409, 'Contact revision conflicts with tracked history')
    result = await tx.run(
        'MATCH (head:OfficeTopology:Node {workspace:$workspace,node_id:$head}) '
        'CREATE (revision:OfficeContactRevision:Node {workspace:$workspace,node_id:$id, '
        'sequence:$sequence, payload:$payload, created_by:$publisher}) '
        'CREATE (head)-[:HAS_CONTACT_REVISION]->(revision) '
        'SET head.sequence=$sequence, head.coverage=coalesce(head.coverage,$recorded)', {
            **params, 'sequence': revision.sequence, 'payload': payload, 'recorded': revision.at.isoformat(),
            'publisher': PUBLISHER,
        })
    await result.consume()


@router.post('/revision', dependencies=[Depends(require_write)])
async def publish_revision(body: PublishedRevision, request: Request):
    require_workspace_access(request, WORKSPACE, 'live:write')
    if request.scope.get('state', {}).get('contributor_id') != PUBLISHER:
        raise HTTPException(403, 'Only the deployment can publish contact revisions')
    body.revision.verify()
    async with request.app.state.neo4j_driver.session() as session:
        await session.execute_write(append_revision, body.head(), body.revision)
    return {'id': body.revision.id}


_OBSERVED = (
    'MATCH (t:ExecutionTask {workspace:$workspace,office_room:$room})-[:HAS_HANDOFF]->'
    '(h:OfficeHandoff {workspace:$workspace})-[:DISPATCHED_IN]->(dispatch:Event {workspace:$workspace}) '
    'WHERE dispatch.occurred_at >= datetime($since) '
)


@router.post('/report', dependencies=[Depends(require_read)])
async def connection_report(body: Office, request: Request):
    require_workspace_access(request, WORKSPACE, 'data:read')
    params = {'workspace': WORKSPACE, 'head': body.head(), 'room': body.room,
              'since': (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()}
    async with request.app.state.neo4j_query_driver.session(default_access_mode=READ_ACCESS) as session:
        result = await session.run(
            'MATCH (head:OfficeTopology:Node {workspace:$workspace,node_id:$head})'
            '-[:HAS_CONTACT_REVISION]->(revision:OfficeContactRevision) '
            'RETURN head.coverage AS coverage, head.sequence AS latest, revision.payload AS payload '
            'ORDER BY revision.sequence DESC LIMIT 101', params)
        revisions = [dict(row) async for row in result]
        result = await session.run(
            _OBSERVED + "AND h.classification='dispatch' "
            'OPTIONAL MATCH (h)-[:ACCEPTED_IN]->(accepted:Event {workspace:$workspace}) '
            'WITH t,h,dispatch,min(accepted.occurred_at) AS accepted_at '
            'OPTIONAL MATCH (h)-[:RETURNED_IN]->(returned:Event {workspace:$workspace}) '
            'WITH t,h,dispatch,accepted_at,max(returned.occurred_at) AS returned_at '
            'RETURN h.source AS sender,h.target AS recipient,h.dispatch_event AS event, toString(dispatch.occurred_at) AS at, '
            'toString(accepted_at) AS acceptedAt,toString(returned_at) AS returnedAt, '
            't.office_thread AS thread,t.office_request AS request,h.status AS status, '
            't.office_status AS outcome,h.policy_revision AS revision '
            'ORDER BY dispatch.occurred_at DESC,dispatch.node_id DESC LIMIT 501', params)
        handoffs = [dict(row) async for row in result]
        result = await session.run(_OBSERVED + 'AND h.classification IS NULL RETURN h.node_id LIMIT 1', params)
        legacy = await result.single()
    return {
        'coverage': revisions[0]['coverage'] if revisions else None,
        'latest': revisions[0]['latest'] if revisions else None,
        'changes': [json.loads(row['payload']) for row in revisions[:100]],
        'handoffs': handoffs[:500], 'unclassified': legacy is not None,
        'changesLimited': len(revisions) > 100, 'handoffsLimited': len(handoffs) > 500,
    }
