"""Provision and reconcile one operator-approved managed Office hire."""
from contextlib import contextmanager
import fcntl
from http.client import HTTPConnection
import json
import socket
import secrets
from subprocess import SubprocessError
import time

from installer import (RUNTIME_UID, commit_registry, compose, compose_config, directory, load, open_existing,
                       operator_lock, prepare_agent_state, publish_inventory, read_password_file,
                       refresh_context, register_entry, save, sync_workshop, up)

MODES = {'chatgpt_codex', 'chatgpt_plan'}
PENDING = {'pending', 'activating', 'provider_waiting', 'tools_waiting'}
MAX_REPLY = 1_800_000


@contextmanager
def gateway(data, payload, *, timeout=45):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(data / 'hiring/admin.sock'))
        http = HTTPConnection('localhost', timeout=timeout)
        http.sock = connection
        try:
            http.request('POST', '/', json.dumps(payload).encode(), {'Content-Type': 'application/json'})
            yield http.getresponse()
        finally:
            http.close()


def control(data, payload):
    with gateway(data, payload) as response:
        content = response.read(MAX_REPLY + 1)
        if len(content) > MAX_REPLY:
            raise ValueError('Oversized provider response')
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError('Invalid provider response')
        return result


def ensure_coordinator(data):
    try:
        return control(data, {'action': 'accounts'})
    except OSError:
        with (data / 'roster/hiring-start.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return control(data, {'action': 'accounts'})
            except OSError:
                up(data, 'hiring-provider')
                return control(data, {'action': 'accounts'})


def tools(data):
    from team_management import tool_spec
    path = data / 'roster/hiring-tools.json'
    manifests = load(path) if path.is_file() else []
    return [tool_spec(item) for item in manifests]


def configure_tools(data, manifest):
    from team_management import tool_spec

    values = load(manifest)
    if not isinstance(values, list) or len(values) > 25:
        raise ValueError('Supply a JSON list of up to 25 approved tool manifests.')
    grants = [tool_spec(item) for item in values]
    if len({item['id'] for item in grants}) != len(grants):
        raise ValueError('Each hiring tool must have a distinct ID.')
    with operator_lock(data):
        open_existing(data)
        directory(data / 'roster')
        save(data / 'roster/hiring-tools.json', grants)
    print(f'Saved {len(grants)} approved hiring tools.')


def control_request(data, config):
    if config['action'] == 'tools':
        return {'tools': tools(data)}
    if config['action'] == 'accounts':
        return ensure_coordinator(data)
    ensure_coordinator(data)
    result = control(data, config)
    if config['action'] == 'catalog' and 'models' in result:
        return {'models': [{field: item[field] for field in ('id', 'efforts')} for item in result['models']]}
    return result


def key_for(data, agent):
    base = data / 'state' / agent
    directory(base)
    path = base / 'hiring-agent'
    directory(path, uid=RUNTIME_UID)
    token = path / 'token'
    if not token.exists():
        save(token, secrets.token_hex(32) + '\n', uid=RUNTIME_UID)
    return read_password_file(token, 'Managed provider')


def planned_grants(data, operation):
    manifests = {item['id']: item for item in tools(data)}
    return all(manifests.get(item['id']) == item for item in operation['tools'])


def advance(data, settings, registry, operation, identifier):
    agent, peer = operation['agent'], operation['provider']
    entry = registry['agents'].get(agent)
    if entry and entry.get('hire_approval') == identifier and entry.get('active'):
        publish_inventory(settings, data, registry)
        return 'active'
    if not planned_grants(data, operation):
        return 'tools_waiting'
    result = ensure_coordinator(data)
    account = next((item for item in result.get('accounts', []) if item['id'] == peer['account']), None)
    if not account or not account['ready'] or account['mode'] != peer['mode']:
        return 'provider_waiting'

    if not entry:
        register_entry(settings, registry, agent, operation['profile'])
        entry = registry['agents'][agent]
        entry.update(hiring_provider={'mode': peer['mode'], 'account': peer['account']}, hire_approval=identifier)
        key_for(data, agent)
        commit_registry(data / 'agents.json', registry, actor='hiring-manager', reason='approved hire registration',
                        approval=identifier)
    elif entry.get('hire_approval') != identifier:
        if not operation.get('resume') or entry.get('active') or entry.get('retirement') or entry.get('mobility_move'):
            return 'rejected'
        if entry['placement'] != 'local' or not entry.get('legacy_hire_approval'):
            return 'rejected'
        entry.update(model=operation['profile']['model'], effort=operation['profile']['effort'],
                     hiring_provider={'mode': peer['mode'], 'account': peer['account']}, hire_approval=identifier)
        key_for(data, agent)
        commit_registry(data / 'agents.json', registry, actor='hiring-manager', reason='approved existing hire activation',
                        approval=identifier)

    selection = {'agent': agent, 'mode': peer['mode'], 'account': peer['account'], 'model': entry['model'],
                 'effort': entry['effort'], 'credential': key_for(data, agent)}
    profile = control(data, {'action': 'grant', **selection})
    if profile.get('status') != 'granted':
        return 'provider_waiting' if profile.get('error') == 'authorization_required' else 'activating'
    milestone = data / 'state' / agent / 'hire-registered'
    if not milestone.exists():
        compose_config(settings, data, registry)
        refresh_context(data)
        token = read_password_file(data / 'roster/inviter-token', 'Office invitation')
        prepare_agent_state(data, agent, entry, load(data / 'rooms.json'), token, settings)
        save(milestone, identifier + '\n')
    up(data, 'engine-' + agent)
    try:
        compose(data, '--profile', 'agents', 'run', '--rm', '--no-deps', '-T', agent,
                'node', '/app/move-probe.mjs', capture_output=True, timeout=220)
    except SubprocessError:
        account = next((item for item in control(data, {'action': 'accounts'}).get('accounts', [])
                        if item['id'] == peer['account']), {})
        return 'provider_waiting' if not account.get('ready') else 'activating'
    if operation['tools']:
        try:
            compose(data, '--profile', 'agents', 'exec', '-T', 'engine-' + agent, 'node', '--input-type=module', '-e',
                    """import { useTool } from '/opt/workspace-tools/team-tools.mjs';
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const tools = JSON.parse(Buffer.concat(chunks));
for (const tool of tools) await useTool(tool);
""", input=json.dumps(operation['tools']), text=True, capture_output=True, timeout=40)
        except SubprocessError:
            return 'tools_waiting'
    compose(data, '--profile', 'agents', 'run', '--rm', '--no-deps', '-T', agent,
            'node', '--input-type=module', '-e', """
import { writeExecutionEvidence } from '/app/context.mjs';
const id = 'hire-check-' + process.argv[1];
await writeExecutionEvidence({event:'office:hire_check', workspace:'agents-team', idempotency_key:id,
  data:{session_id:id, timestamp:new Date(Number(process.argv[2])).toISOString()}});
""", identifier, str(milestone.stat().st_mtime_ns // 1_000_000), capture_output=True, timeout=30)
    if not sync_workshop(settings, data, registry).get(agent, {}).get('available'):
        return 'activating'
    up(data, agent)
    entry['enabled_tools'] = {item['id']: item for item in operation['tools']}
    entry['amplifier'] = {'provider': 'provider-constellation-hiring', 'model': entry['model'],
                          'effort': entry['effort'], 'mode': peer['mode']}
    entry['active'] = True
    commit_registry(data / 'agents.json', registry, actor='hiring-manager', reason='verified hire ready',
                    approval=identifier)
    publish_inventory(settings, data, registry)
    return 'active'


def reconcile(data):
    from provision import MatrixError

    requests = data / 'roster/requests'
    for path in sorted(requests.glob('*.json')):
        if not path.is_file():
            continue
        record = load(path)
        if not record.get('managed') or record['status'] not in PENDING or record.get('next', 0) > time.time():
            continue
        with operator_lock(data):
            settings, registry = open_existing(data)
            record = load(path)
            if record['status'] not in PENDING or record.get('next', 0) > time.time():
                continue
            record['next'] = time.time() + 30
            save(path, record)
            try:
                operation = json.loads(record['receipt']['operation'])
                status = advance(data, settings, registry, operation, path.stem)
            except (OSError, ValueError, KeyError, SubprocessError, MatrixError) as error:
                print('Approved hire will retry (' + type(error).__name__ + ').', flush=True)
                status = 'activating'
            save(path, {**record, 'status': status, 'next': time.time() + 30})
