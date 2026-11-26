"""Operations authorized for the Coach and Scouter bridges. No host commands come from a model."""
from functools import partial
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

from agent_catalog import KEY, clean, contact_keys, contacts_for, persona_for
from installer import compose, directory, load, publish_inventory, read_password_file, save
from persona_management import api, TARGETS
from team_workshop import active_claims, apply_style, save_style


def tool_spec(value):
    if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not KEY.fullmatch(value['id']):
        raise ValueError('A tool proposal requires a stable ID')
    common = {'kind', 'id', 'source', 'version', 'permissions', 'verification'}
    kinds = {'github': {'repository'}, 'skill': {'sha256'}, 'mcp': {'endpoint', 'methods'}}
    kind = value.get('kind')
    if not isinstance(kind, str) or kind not in kinds or set(value) != common | kinds[kind]:
        raise ValueError('Invalid tool manifest')
    for name, limit in [('version', 100), ('permissions', 260), ('verification', 360), ('source', 1024)]:
        if not clean(value[name], limit):
            raise ValueError('Describe the source, version, permissions and verification')
    def public(url):
        parts = urlsplit(url)
        return (parts.scheme == 'https' and parts.hostname and '.' in parts.hostname and not parts.username
                and not parts.password and not parts.fragment and not parts.query and parts.port in (None, 443))
    if not public(value['source']):
        raise ValueError('Use a public publisher HTTPS URL')
    if kind == 'github' and (value['version'] != '1' or value['source'] != 'https://github.com/cli/cli' or
                            not re.fullmatch(r'[\w.-]+/[\w.-]+', value['repository']) or '..' in value['repository']):
        raise ValueError('Choose the GitHub reader and one authorized repository')
    if kind == 'skill' and not re.fullmatch('[a-f0-9]{64}', value['sha256']):
        raise ValueError('A skill needs its exact content digest')
    if kind == 'mcp' and (not public(value['endpoint']) or not isinstance(value['methods'], list) or
                          not 1 <= len(value['methods']) <= 20 or not all(isinstance(m, str) and
                          re.fullmatch('[a-zA-Z0-9_.-]{1,100}', m) for m in value['methods'])):
        raise ValueError('An MCP grant needs an HTTPS endpoint and exact permitted methods')
    return value


def graph(data, reader, report, params):
    # Query through the scoped Context API. The public edge deliberately exposes only event ingestion.
    token = read_password_file(data / 'context' / (reader + '.token'), 'Office evidence reader')
    program = """import json, sys, urllib.request
token, report, params = json.load(sys.stdin)
request = urllib.request.Request('http://localhost:8000/office/review/' + report, data=json.dumps(params).encode(),
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=12) as result: print(result.read().decode())
"""
    result = compose(data, 'exec', '-T', 'context', '/app/.venv/bin/python', '-c', program,
                     input=json.dumps([token, report, params]),
                     text=True, capture_output=True)
    return json.loads(result.stdout)['results']


def apply_tool(data, settings, registry, operation, change):
    entry = registry['agents'][operation['agent']]
    if not entry.get('active') or entry.get('retirement'):
        raise ValueError('Choose an active agent')
    tool = tool_spec(operation['tool'])
    checks = entry.setdefault('tool_checks', {})
    grants = entry.setdefault('enabled_tools', {})
    receipts = entry.setdefault('tool_receipts', {})
    if change in receipts:
        return receipts[change]
    if grants.get(tool['id']) == tool:
        return 'enabled'
    candidate = {'change': change, 'tool': tool}
    if tool['id'] not in checks:
        checks[tool['id']] = candidate
        save(data / 'agents.json', registry)
        publish_inventory(settings, data, registry)
        return 'checking'
    if checks[tool['id']] != candidate:
        raise ValueError('Another approval is checking this tool')
    rows = graph(data, 'scouter', 'tool-check',
                 {'actor': operation['agent'], 'prefix': 'tool-check-' + change + '__'})
    if not rows:
        return 'checking'
    report = json.loads(rows[0]['data'])
    if report.get('change') != change or report.get('status') not in {'verified', 'failed', 'credentials'}:
        raise ValueError('Unexpected target verification receipt')
    status = report['status']
    if status == 'verified':
        grants[tool['id']] = tool
    del checks[tool['id']]
    receipts[change] = {'verified': 'enabled', 'failed': 'failed', 'credentials': 'credentials'}[status]
    save(data / 'agents.json', registry)
    publish_inventory(settings, data, registry)
    return receipts[change]


def coach_change(data, settings, registry, operation, *, approved=False):
    key = operation['agent']
    entry = registry['agents'].get(key)
    if not entry or entry.get('retirement'):
        raise ValueError('Choose a current Workshop target')
    kind = operation['kind']
    reference = operation.get('evidence')
    if not isinstance(reference, str) or not re.fullmatch(r'\$[^\s/#?%]{1,254}', reference):
        raise ValueError('Cite an Office evidence event')
    if kind not in {'coach', 'undo'}:
        raise ValueError('Unknown Coach operation')
    operation_id = sha256(json.dumps(operation, sort_keys=True).encode()).hexdigest()
    journal = data / 'team-changes'
    directory(journal)
    path = journal / (operation_id + '.json')
    record = load(path) if path.exists() else {}
    if record.get('done'):
        return 'applied'
    old_contacts = contacts_for(key, registry['agents'])
    if kind == 'undo':
        old_id = operation.get('change', '')
        if not re.fullmatch(r'[a-f0-9]{64}', old_id) or entry.get('last_coaching') != old_id:
            raise ValueError('Only the current revision can be undone')
        old = load(journal / (old_id + '.json'))
        style, contacts = old['before_style'], old['before_contacts']
    else:
        style, contacts = operation.get('style'), sorted(contact_keys(operation.get('contacts')))
    if (not clean(style, 1000) or key in contacts or any(k not in registry['agents'] or
            registry['agents'][k].get('retirement') for k in contacts)):
        raise ValueError('Invalid working style or recipients')
    if not approved and (('vps-manager' in set(old_contacts).symmetric_difference(contacts)) or
                         (key == 'vps-manager' and old_contacts != contacts)):
        return 'needs_approval'
    workshop = compose(data, '--profile', 'agents', 'ps', '-q', 'persona-workshop', capture_output=True, text=True).stdout.strip()
    if not workshop:
        raise ValueError('Persona Workshop is unavailable')
    request = partial(api, workshop)
    binding = load(data / 'workshop-management/targets.json')[key]
    target = str(UUID(binding['target']))
    live = request('GET', TARGETS + '/' + target)
    if live['target']['agent_id'] != binding['instance']:
        raise ValueError('Workshop target identity changed')
    if not record:
        rows = graph(data, 'coach', 'participation',
                     {'event': reference, 'room': load(data / 'rooms.json')['office'], 'user': '@' + key + ':' + settings['server_name']})
        if not rows:
            raise ValueError('The target did not participate in the cited Office evidence')
        if operation.get('generation') != live['installation']['revision']:
            raise ValueError('The applied persona changed since review')
        claims = active_claims(request, live['installation'])
        previous_style = ' '.join(c['statement'] for c in claims if c['collection'] == 'working' and
                                  c['section'] == 'collaboration') or entry.get('applied_style', persona_for(entry)['working_style'])
        previous = live['installation'].get('active')
        revision = None if kind == 'undo' else save_style(request, 'Office ' + key + ' ' + operation_id[:18],
                    persona_for(entry)['purpose'], 'Describe the result and supporting evidence in the originating thread.', style, claims)
        record = {'operation': operation, 'target': target, 'generation': live['installation']['revision'],
                  'before_style': previous_style, 'before_contacts': old_contacts, 'revision': revision,
                  'before_override':entry.get('office_contacts'), 'before_snapshot': previous and previous['id'],
                  'restore':old['before_snapshot'] if kind == 'undo' else None}
        save(path, record)
    if record['target'] != target:
        raise ValueError('Target changed before publication')
    installed = live['installation']
    if installed['revision'] == record['generation']:
        if record['restore']:
            installed = request('POST', TARGETS + '/' + target + '/restore',
                                {'id':record['restore'], 'expected_revision':installed['revision']})['installation']
        else:
            installed = apply_style(request, target, record['revision'], installed['revision'])
    elif installed['revision'] != record['generation'] + 1 or (kind == 'coach' and
            ((installed.get('active') or {}).get('source') or {}).get('revision') != record['revision'].split('/')[-1]):
        raise ValueError('Another profile was installed. Review the current revision.')
    if not record['before_snapshot'] and installed.get('previous'):
        record['before_snapshot'] = installed['previous'][0]['id']
    entry.update(office_contacts=contacts, applied_style=style, persona_revision=installed['revision'], last_coaching=operation_id)
    if kind == 'undo' and old.get('before_override') is None:
        entry.pop('office_contacts', None)
    save(data / 'agents.json', registry)
    publish_inventory(settings, data, registry)
    save(path, {**record, 'done': True})
    return 'applied'
