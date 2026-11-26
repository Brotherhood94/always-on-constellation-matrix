"""Isolated API acceptance check using only synthetic identities and events."""

import asyncio
import hashlib
import json
from datetime import datetime, timezone
import sys
import time
import urllib.request


def post(endpoint, payload, key):
    request = urllib.request.Request(
        'http://context:8000/' + endpoint, method='POST', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


writers = {name: f'fixture-writer-{name}' for name in ('first', 'second', 'manager', 'analyst', 'vps-manager', 'vps-manager-ops')}
reader = 'fixture-reader'
workspace = 'agents-team'


def event(name, pos, writer, session, trace=None, task=None):
    data = {'session_id': session, 'timestamp': f'2026-11-24T15:00:{pos:02d}Z'}
    if trace:
        data['execution'] = {'trace_id': trace, 'task_id': task}
    return {'event': f'smoke:{name}', 'workspace': workspace, 'idempotency_key': name,
            'data': data, 'created_by': 'spoofed'}


async def write(name, pos, actor, session, trace=None, task=None):
    result = await asyncio.to_thread(post, 'events', event(name, pos, actor, session, trace, task), writers[actor])
    assert result['status'] == 'queued', result


def query(cypher, scope=workspace):
    return post('cypher', {'workspace': scope, 'query': cypher}, reader)['results']


def wait_for(cypher, condition, scope=workspace):
    for _ in range(40):
        rows = query(cypher, scope)
        if condition(rows):
            return rows
        time.sleep(0.4)
    raise AssertionError(rows)


async def verify():
    await write('last', 4, 'second', 'session-second', 'flow', 'second-task')
    await write('start', 1, 'first', 'session-first', 'flow', 'first-task')
    await write('end', 3, 'first', 'session-first', 'flow', 'first-task')
    await asyncio.to_thread(wait_for,
        'MATCH (:Event {workspace:$workspace,event_name:"smoke:start"})-[:NEXT_EVENT]->'
        '(:Event {workspace:$workspace,event_name:"smoke:end"}) RETURN count(*) AS links',
        lambda rows: rows == [{'links': 1}])
    await asyncio.gather(write('middle', 2, 'second', 'session-second', 'flow', 'first-task'),
                         write('native-first', 5, 'first', 'session-native'),
                         write('isolated', 6, 'second', 'session-isolated', 'other-flow', 'unrelated'))
    await write('native-second', 7, 'first', 'session-native')
    duplicate = post('events', event('middle', 2, 'second', 'session-second', 'flow', 'first-task'), writers['second'])
    assert duplicate['status'] == 'duplicate'
    stale = event('stale', 0, 'first', 'session-stale')
    stale['data']['timestamp'] = '2020-01-01T00:00:00Z'
    assert post('events', stale, writers['first'])['status'] == 'duplicate'


def office(kind, writer, stamp, request, assignment, **fields):
    room, thread = '!office:fixture.test', '$thread'
    actor = fields.pop('actor', f'@{writer}:fixture.test')
    digest = lambda *items: hashlib.sha256(json.dumps(items, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    trace = 'agents-thread-' + digest(room, thread)
    data = {'session_id': trace + '-' + writer, 'timestamp': f'2026-11-24T16:00:{stamp:02d}Z',
            'execution': {'trace_id': trace, 'task_id': 'agents-request-' + digest(room, request), 'sequence': stamp},
            'evidence': {'room': room, 'thread': thread, 'request': request, 'actor': actor,
                         'assignment': assignment, **fields}}
    return post('events', {'event': 'agents:' + kind, 'workspace': workspace, 'data': data,
                           'idempotency_key': f'office-{writer}-{stamp}'}, writers[writer])


def verify_office():
    # A result can arrive before dispatch or acceptance without losing the links.
    office('execution', 'vps-manager', 5, '$first', '$delegate-vps', reply='$vps-result', to='@manager:fixture.test',
           step='returned', operation='a' * 32)
    post('events', {'event': 'root:finished', 'workspace': 'root-operations', 'idempotency_key': 'fixture-vps-audit',
                    'data': {'session_id': 'fixture-vps-audit', 'timestamp': '2026-11-24T16:00:05Z',
                             'operation': {'id': 'a' * 32}}}, writers['vps-manager-ops'])
    office('execution', 'manager', 7, '$first', '$vps-result', reply='$verified', outcome='completed',
           references=['$vps-result'])
    office('execution', 'analyst', 1, '$first', '$first', reply='$spoof', actor='@manager:fixture.test', outcome='blocked')
    office('assignment_accepted', 'manager', 2, '$first', '$first')
    office('execution', 'manager', 3, '$first', '$first', reply='$delegate-analyst', to='@analyst:fixture.test')
    office('assignment_accepted', 'analyst', 4, '$first', '$delegate-analyst', **{'from': '@manager:fixture.test'})
    office('execution', 'analyst', 8, '$first', '$delegate-analyst', reply='$delegate-vps', to='@vps-manager:fixture.test',
           step='returned')
    office('assignment_accepted', 'vps-manager', 9, '$first', '$delegate-vps', **{'from': '@analyst:fixture.test'})
    office('assignment_accepted', 'manager', 6, '$first', '$vps-result', **{'from': '@vps-manager:fixture.test'})
    office('assignment_accepted', 'manager', 10, '$second', '$second')
    office('execution', 'manager', 11, '$second', '$second', reply='$unverified', outcome='completed')
    statuses = ('MATCH (t:ExecutionTask {workspace:$workspace,office_thread:"$thread"}) '
                'RETURN t.office_request AS request,t.office_status AS status')
    wait_for(statuses, lambda rows: {(r['request'], r['status']) for r in rows} ==
             {('$first', 'verified'), ('$second', 'in_progress')})
    office('execution', 'manager', 12, '$second', '$second', reply='$blocked', outcome='blocked',
           need={'kind':'tool', 'topic':'GitHub CLI','capability':'coordination'})
    assert office('execution', 'manager', 12, '$second', '$second', reply='$blocked', outcome='blocked',
                  need={'kind':'tool','topic':'GitHub CLI','capability':'coordination'})['status'] == 'duplicate'
    wait_for(statuses, lambda rows: {(r['request'], r['status']) for r in rows} ==
             {('$first', 'verified'), ('$second', 'blocked')})
    handoffs = ('MATCH (:ExecutionTask {workspace:$workspace,office_request:"$first"})-[:HAS_HANDOFF]->(h:OfficeHandoff) '
                'RETURN h.source AS source,h.target AS target,h.status AS status')
    wait_for(handoffs, lambda rows: {(r['source'], r['target'], r['status']) for r in rows} == {
        ('@manager:fixture.test', '@analyst:fixture.test', 'returned'),
        ('@analyst:fixture.test', '@vps-manager:fixture.test', 'returned'),
        ('@vps-manager:fixture.test', '@manager:fixture.test', 'returned'),
    })
    assert query('MATCH (t:ExecutionTask {workspace:$workspace,office_request:"$first"})-[:HAS_OUTCOME]->(e) '
                 'RETURN e.office_reply AS reply,e.office_references AS refs') == [{'reply': '$verified', 'refs': ['$vps-result']}]
    office_receipt = query('MATCH (e:Event {workspace:$workspace,office_reply:"$vps-result"}) '
                           'RETURN e.office_operation AS id')
    wait_for('MATCH (e:Event {workspace:$workspace,event_name:"root:finished"}) RETURN e.vps_operation_id AS id',
             lambda rows: rows == office_receipt == [{'id': 'a' * 32}], 'root-operations')
    print('Office handoffs, recipient acceptance, VPS receipt and authoritative task outcomes verified.')
    records = post('office/review/recent', {'room':'!office:fixture.test'}, 'fixture-desk-reader')['results']
    observed = next(row for row in records if json.loads(row['data'])['evidence']['reply'] == '$blocked')
    assert observed['status'] == 'blocked' and json.loads(observed['data'])['evidence']['need']['kind'] == 'tool'
    change = 'b' * 64
    session = 'tool-check-' + change
    post('events', {'event':'office:tool_check','workspace':workspace,'idempotency_key':session,
         'data':{'session_id':session,'timestamp':'2026-11-24T16:01:00Z','change':change,'status':'verified'}}, writers['analyst'])
    check = ('MATCH (e:OfficeToolCheckEvent {workspace:$workspace,created_by:"analyst"}) '
             'WHERE e.node_id STARTS WITH "' + session + '__" RETURN e.data AS data')
    wait_for(check, lambda rows: bool(rows) and json.loads(rows[0]['data'])['status']=='verified')
    assert post('office/review/tool-check',{'actor':'analyst','prefix':session+'__'},'fixture-desk-reader')['results']
    for path, payload, token in [('cypher',{'workspace':workspace,'query':check},'fixture-desk-reader'),
                                 ('office/review/recent',{'room':'!office:fixture.test'},writers['analyst'])]:
        try:
            post(path, payload, token)
            raise AssertionError('Unexpected read permission')
        except urllib.error.HTTPError as error:
            assert error.code == 403
    print('A scoped team desk read the reported blocker and attributed target verification to its authenticated sender.')


if __name__ == '__main__':
    if sys.argv[1:] == ['config']:
        ids = {**{token: f'writer-{name}' if name in ('first', 'second') else name
                  for name, token in writers.items()}, reader: 'reader', 'fixture-desk-reader':'scouter'}
        print(json.dumps({'auth_mode': 'static', 'allow_unauthenticated': False,
                          'access_control_mode': 'scoped', 'neo4j_url': 'bolt://neo4j:7687',
                          'api_keys_store_path': '/data/identity/api-keys.json',
                          'api_keys': {hashlib.sha256(token.encode()).hexdigest(): {'id': identity}
                                       for token, identity in ids.items()},
                          'contributor_grants': {identity: ({'all_workspaces': True, 'capabilities': ['data:read']}
                              if identity == 'reader' else {'workspaces': ['root-operations' if identity == 'vps-manager-ops'
                                                                         else workspace], 'capabilities': ['data:read','live:write']
                                                                         if identity == 'scouter' else ['live:write']})
                              for identity in ids.values()}}))
        raise SystemExit(0)
    asyncio.run(verify())
    actor_rows = wait_for('MATCH (e:Event {workspace:$workspace}) RETURN e.event_name AS name,e.created_by AS actor',
                          lambda rows: len(rows) == 7)
    actors = {row['name']: row['actor'] for row in actor_rows}
    assert actors['smoke:start'] == 'writer-first' and actors['smoke:middle'] == 'writer-second', actors
    assert 'smoke:stale' not in actors
    event_query = ('MATCH (a:Event {workspace:$workspace})-[:NEXT_EVENT]->(b:Event {workspace:$workspace}) '
                   'RETURN a.event_name AS left,b.event_name AS right')
    expected = {('smoke:start', 'smoke:middle'), ('smoke:middle', 'smoke:end'), ('smoke:native-first', 'smoke:native-second')}
    wait_for(event_query, lambda rows: {(row['left'], row['right']) for row in rows} == expected and len(rows) == 3)
    task_query = ('MATCH (a:ExecutionTask {workspace:$workspace})-[:NEXT_TASK]->(b:ExecutionTask {workspace:$workspace}) '
                  'MATCH (a)-[:HAS_STEP]->(:Event {event_name:"smoke:start"}), '
                  '(b)-[:HAS_STEP]->(:Event {event_name:"smoke:last"}) RETURN count(*) AS links')
    wait_for(task_query, lambda rows: rows == [{'links': 1}])
    assert query('MATCH (:ExecutionTask {workspace:$workspace})-[r:NEXT_TASK]->() RETURN count(r) AS links') == [{'links': 1}]
    print('Chronological steps, ordered tasks, independent traces, authenticated authors and cutoff verified.')
    verify_office()
