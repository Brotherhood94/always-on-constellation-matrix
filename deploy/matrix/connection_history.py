"""Durable Office contact revisions and a read-only dashboard export."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
from tempfile import NamedTemporaryFile
from uuid import uuid4

from agent_catalog import contacts_for

PUBLISHER = 'office-topology@deployment'
PUBLISHER_TOKEN = 'context/.topology-publisher.token'
HISTORY = 'connection_history'
ACK = 'connection-history-ack.json'


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def replace_json(path, value, mode):
    with NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            temporary.replace(path)
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            temporary.unlink(missing_ok=True)


def graph_state(registry):
    entries = {k: v for k, v in registry['agents'].items() if 'profile' in v}
    names = {k: v['name'] for k, v in entries.items() if v['profile']['role'] != 'observer'}
    enforced = registry.get('team_version') == 2 and registry.get('dynamic_handoffs') is True
    edges = {(source, target) for source in names for target in contacts_for(source, entries) if target in names} if enforced else set()
    return names, edges, enforced


def add_revision(history, before, after, actor, reason, evidence, approval):
    old_names, old_edges, old_enforced = before
    names, edges, enforced = after
    if before == after and history['sequence']:
        return
    added = sorted(edges - old_edges)
    removed = sorted(old_edges - edges)
    affected = set(old_names) ^ set(names)
    affected.update(k for k in names.keys() & old_names.keys() if names[k] != old_names[k])
    affected.update(k for edge in added + removed for k in edge)
    revision = {
        'id': uuid4().hex, 'sequence': history['sequence'] + 1,
        'kind': 'change' if history['sequence'] else 'baseline',
        'at': datetime.now(timezone.utc).isoformat(),
        'actor': actor, 'reason': reason, 'enforced': enforced, 'previous_enforced': old_enforced,
        'added': added, 'removed': removed,
        'joined': sorted(names.keys() - old_names.keys()), 'left': sorted(old_names.keys() - names.keys()),
        'renamed': {key: old_names[key] for key in names.keys() & old_names.keys() if names[key] != old_names[key]},
        'names': {key: names.get(key, old_names.get(key)) for key in sorted(affected)},
        **({'evidence': evidence} if evidence else {}),
        **({'approval': approval} if approval else {}),
    }
    history.update(sequence=revision['sequence'], revision=revision['id'])
    history['pending'].append(revision)


def unacknowledged(pending, ack):
    position = next((index for index, record in enumerate(pending) if record['id'] == ack), -1)
    return pending[position + 1:]


def commit_registry(path, registry, *, actor='operator', reason='registry update', evidence=None, approval=None, mode=0o600):
    """The caller holds the deployment's operator lock for the whole mutation."""
    previous = read_json(path)
    history = (previous or {}).get(HISTORY)
    if history is None:
        history = {'sequence': 0, 'pending': []}
        add_revision(history, ({}, set(), False), graph_state(previous or registry),
                     'deployment', 'tracking enabled', None, None)
    if previous is not None:
        add_revision(history, graph_state(previous), graph_state(registry), actor, reason, evidence, approval)
    history['pending'] = unacknowledged(history['pending'], read_json(path.parent / ACK))
    registry[HISTORY] = history
    replace_json(path, registry, mode)


_CONTEXT_REQUEST = """import json, sys, urllib.request
token, endpoint, payload = json.load(sys.stdin)
request = urllib.request.Request('http://127.0.0.1:8000' + endpoint, data=json.dumps(payload).encode(),
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=5) as result:
    print(result.read().decode())
"""


def context_request(data, endpoint, payload):
    credential = (data / PUBLISHER_TOKEN).read_text().strip()
    result = subprocess.run(
        ['docker', 'compose', '--project-directory', str(data), 'exec', '-T', 'context',
         '/app/.venv/bin/python', '-c', _CONTEXT_REQUEST],
        input=json.dumps([credential, endpoint, payload]), text=True, capture_output=True, check=True, timeout=9,
    )
    return json.loads(result.stdout)


def publish_connections(data, graph=None):
    if graph is None:
        if not (data / PUBLISHER_TOKEN).is_file():
            return

        def graph(endpoint, payload):
            return context_request(data, endpoint, payload)

    with (data / '.connection-export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        room = (read_json(data / 'rooms.json') or {}).get('office')
        history = (read_json(data / 'agents.json') or {}).get(HISTORY)
        if not room or not history:
            return
        pending = unacknowledged(history['pending'], read_json(data / ACK))
        for revision in pending[:16]:
            receipt = graph('/office/topology/revision', {'room': room, 'revision': revision})
            if receipt.get('id') != revision['id']:
                raise ValueError('Unexpected topology revision receipt')
            replace_json(data / ACK, revision['id'], 0o600)
        report = graph('/office/topology/report', {'room': room})
        now = datetime.now(timezone.utc)
        replace_json(data / 'dashboard/connections.json', {
            **report, 'version': 1, 'time': int(now.timestamp()), 'room': room, 'pending': len(pending) > 16,
        }, 0o644)


def refresh_connections(data):
    try:
        publish_connections(data)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        print('Connection history is temporarily unavailable. The last published snapshot was preserved.', flush=True)
