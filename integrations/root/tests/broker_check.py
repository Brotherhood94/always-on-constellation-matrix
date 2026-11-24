import json
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from broker import WINDOW, database, execute_script, handle, sha, verify_matrix_receipt


now = 5_000_000
config = {
    'agents': '!agents:fixture.test', 'audit': '!audit:fixture.test',
    'root': '@root:fixture.test', 'owner': '@operator:fixture.test',
}
owner = config['owner']
executions = []


def payload_for(action, nonce):
    script = 'printf "%s\\n" "fixture"\nexit 0\n'
    request = f'$request-{nonce}'
    payload = {
        'operation': 'reviewed-script', 'script': script, 'source_room': config['agents'], 'review_room': config['audit'],
        'request': request, 'decision': f'$decision-{nonce}', 'proposal': f'$proposal-{nonce}', 'poll': f'$poll-{nonce}',
        'id': sha(config['agents'] + ':' + request)[:32], 'approver': owner,
        'hash': sha(script), 'action': action, 'expires': now + 5000, 'decided': now - 5,
    }
    payload['choice'] = choice(payload)
    return payload


def choice(payload):
    return sha(json.dumps([payload[key] for key in ('id', 'hash', 'proposal', 'action')], separators=(',', ':')))


def events(payload):
    return {
        (config['agents'], payload['request']): {'origin_server_ts': now - 2000, 'sender': '@manager:fixture.test'},
        (config['audit'], payload['proposal']): {'origin_server_ts': now - 1000, 'sender': config['root']},
        (config['audit'], payload['poll']): {'origin_server_ts': now - 500, 'sender': config['root']},
        (config['audit'], payload['decision']): {
            'origin_server_ts': now - 5, 'sender': owner,
            'content': {'m.relates_to': {'rel_type': 'm.reference', 'event_id': payload['poll']}},
        },
    }


def record_execution(script):
    executions.append(script)
    return 'completed', 0


def submit(db, payload, *, overrides=None, verified=True):
    receipts = events(payload)
    if overrides is not None:
        receipts[(config['audit'], payload['decision'])].update(overrides)
    verify = (lambda request: verify_matrix_receipt(config, request, lambda room, event: receipts[room, event])) if verified else None
    return handle(db, payload, {owner}, {}, now=now, script_runner=record_execution, verify_review=verify)


def rejects(fn, expected):
    try:
        fn()
    except ValueError as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError('Expected rejection: ' + expected)


with TemporaryDirectory() as directory:
    path = Path(directory) / 'test.sqlite'
    db = database(path)
    accept = payload_for('approve', 1)
    rejects(lambda: submit(db, accept, verified=False), 'disabled')
    rejects(lambda: submit(db, dict(accept, approver='@other:fixture.test')), 'authority')
    changed = dict(accept, script=accept['script'] + ' ')
    rejects(lambda: submit(db, changed), 'bind')
    changed['hash'] = sha(changed['script'])
    rejects(lambda: submit(db, changed), 'bind')
    rejects(lambda: submit(db, accept, overrides={'sender': '@other:fixture.test'}), 'account')
    rejects(lambda: submit(db, accept, overrides={'content': {}}), 'different poll')
    rejects(lambda: submit(db, accept, overrides={'origin_server_ts': now - 3000}), 'chronology')
    assert not executions
    assert submit(db, accept) == {'outcome': 'completed', 'exit_code': 0}
    assert executions == [accept['script']]
    assert submit(db, accept) == {'outcome': 'completed', 'exit_code': 0}
    other = dict(payload_for('approve', 2), decision=accept['decision'])
    rejects(lambda: submit(db, other), 'already been used')
    reject = payload_for('reject', 3)
    assert submit(db, reject) == {'outcome': 'denied', 'exit_code': None}
    wrong_action = dict(reject, action='approve')
    wrong_action['choice'] = choice(wrong_action)
    rejects(lambda: submit(db, wrong_action), 'existing receipt')
    expired = dict(payload_for('approve', 4), expires=now - 1)
    assert submit(db, expired) == {'outcome': 'expired', 'exit_code': None}
    crashed = payload_for('approve', 5)
    with db:
        db.execute('INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)',
                   tuple(crashed[key] for key in ('id', 'hash', 'decision', 'request', 'approver', 'action', 'decided')) + ('running',))
    db.close()
    db = database(path)
    assert submit(db, crashed) == {'outcome': 'unknown', 'exit_code': None}
    assert executions == [accept['script']]
    db.close()

print('Broker verified fresh owner approval, exact bytes, replay, rejection, expiry and crash recovery.')
if sys.platform == 'linux':
    assert execute_script('test -r "$0"\nexit $?\n') == ('completed', 0)
    assert execute_script('exit 7\n') == ('failed', 7)
    print('Linux executed the approved script from the sealed descriptor and preserved the exit status.')
