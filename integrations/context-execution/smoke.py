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


writers = {'first': 'fixture-writer-first', 'second': 'fixture-writer-second'}
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


def query(cypher):
    return post('cypher', {'workspace': workspace, 'query': cypher}, reader)['results']


def wait_for(cypher, condition):
    for _ in range(40):
        rows = query(cypher)
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


if __name__ == '__main__':
    if sys.argv[1:] == ['config']:
        ids = {**{token: f'writer-{name}' for name, token in writers.items()}, reader: 'reader'}
        print(json.dumps({'auth_mode': 'static', 'allow_unauthenticated': False,
                          'access_control_mode': 'scoped', 'neo4j_url': 'bolt://neo4j:7687',
                          'api_keys_store_path': '/data/identity/api-keys.json',
                          'api_keys': {hashlib.sha256(token.encode()).hexdigest(): {'id': identity}
                                       for token, identity in ids.items()},
                          'contributor_grants': {identity: ({'all_workspaces': True, 'capabilities': ['data:read']}
                              if identity == 'reader' else {'workspaces': [workspace], 'capabilities': ['live:write']})
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
