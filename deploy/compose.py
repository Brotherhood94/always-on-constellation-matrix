"""Compose topology generated from the operator's configuration and registry."""
from pathlib import Path
from shlex import quote

SOURCE = Path(__file__).resolve().parent.parent
CONNECTOR = 'constellation-office-connector:0.22.17'
ENGINE = 'constellation-office-engine:0.22.17'
BASE = 'constellation-amplifier-base:0.22.17'
SYNAPSE = 'ghcr.io/element-hq/synapse:v1.165.0'


def volume(source, target, read_only=False):
    return {'type': 'bind', 'source': str(source), 'target': target, 'read_only': read_only}


def port(local, target):
    return f'127.0.0.1:{local}:{target}'


def telegram_office_agent(registry):
    agents = registry['agents']
    for key in ('task-manager', 'manager'):
        if agents.get(key, {}).get('placement') == 'local':
            return key
    return None


def definition(settings, data, registry, secrets, *, workshop_internal=True):
    engine_image = ENGINE
    slice_name = {'cgroup_parent': 'matrix-workload.slice'} if settings['systemd'] else {}
    common = {'restart': 'unless-stopped', **slice_name,
              'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}}
    restricted = {**common, 'init': True, 'user': '991:991', 'read_only': True, 'tmpfs': ['/tmp'],
                  'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true']}
    network = {'clients': {}, 'database': {'internal': True}, 'memory': {'internal': True}, 'edge': {}}
    server = settings['server_name']
    matrix = {'MATRIX_URL': 'http://synapse:8008', 'MATRIX_SERVER_NAME': server,
              'MATRIX_OWNER': f"@{settings['owner']}:{server}"}
    context = {'CONTEXT_INTELLIGENCE_URL': 'http://context:8000'}
    catalog = {'MATRIX_CATALOG_PATH': '/catalog/agents.json'}
    state = data / 'state'
    dashboard = data / 'dashboard'
    services = {
        'postgres': {**common, 'image': 'postgres:17.12-bookworm', 'networks': ['database'],
                     'environment': {'POSTGRES_USER': 'synapse', 'POSTGRES_DB': 'synapse',
                                     'POSTGRES_PASSWORD': secrets['POSTGRES_PASSWORD'],
                                     'POSTGRES_INITDB_ARGS': '--encoding=UTF8 --locale=C'},
                     'volumes': [volume(data / 'postgres', '/var/lib/postgresql/data')],
                     'healthcheck': health('pg_isready', '-U', 'synapse', '-d', 'synapse')},
        'synapse': {**common, 'image': SYNAPSE, 'networks': ['database', 'clients'],
                    'environment': {'SYNAPSE_SERVER_NAME': server, 'SYNAPSE_REPORT_STATS': 'no'},
                    'ports': [port(settings['matrix_port'], 8008)],
                    'depends_on': {'postgres': {'condition': 'service_healthy'}},
                    'volumes': [volume(data / 'synapse', '/data')],
                    'healthcheck': health('curl', '-fsS', 'http://127.0.0.1:8008/_matrix/client/versions')},
        'monitor': {**restricted, 'build': str(SOURCE / 'deploy/matrix/bot'), 'networks': ['clients'],
                    'environment': {**matrix, 'STATE_DIR': '/state', 'DATA_DIR': '/monitor',
                                    'WORKSPACE_LINKS': '/dashboard/links.json',
                                    'GUIDE_COMMAND': 'sudo ./constellation_matrix --data-dir ' + quote(str(data)),
                                    'GRAPH_PASSWORD_COMMAND': 'sudo cat -- ' + quote(str(data / 'context/neo4j-password')),
                                    'DOT_ENABLED': str(settings['dot']).lower(),
                                    'SYSTEMD_ENABLED': str(settings['systemd']).lower()},
                    'volumes': [volume(data / 'bot', '/state'), volume(data / 'monitor/outbox', '/monitor/outbox', True),
                                volume(data / 'monitor/acks', '/monitor/acks'), volume(dashboard, '/dashboard', True)]},
        'context': {**restricted, 'image': 'constellation-context:6.13.0-1622b48-topology1',
                    'build': {'context': str(SOURCE), 'dockerfile': 'deploy/context.Dockerfile'},
                    'networks': ['memory'], 'depends_on': {'neo4j': {'condition': 'service_healthy'}},
                    'environment': {'AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_CONFIG_FILE': '/etc/context/server.json'},
                    'volumes': [volume(data / 'context/data', '/data'), volume(data / 'context/config', '/etc/context', True)],
                    'healthcheck': health('python', '-c', 'import json,urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8000/status",timeout=5)); assert s["neo4j_connected"] and s["neo4j_query_connected"]')},
        'neo4j': {**common, 'image': 'neo4j:5.26.22-community', 'networks': ['memory'],
                  'environment': {'NEO4J_AUTH_FILE': '/run/secrets/neo4j-auth',
                                  'NEO4J_server_memory_heap_initial__size': '256m',
                                  'NEO4J_server_memory_heap_max__size': '512m',
                                  'NEO4J_server_memory_pagecache_size': '256m',
                                  'NEO4J_server_bolt_advertised__address': f"{server}:{settings['bolt_https_port']}"},
                  'volumes': [volume(data / 'context/graph', '/data'),
                              volume(data / 'context/neo4j-auth', '/run/secrets/neo4j-auth', True)],
                  'healthcheck': {'test': ['CMD-SHELL', 'auth=$$(cat /run/secrets/neo4j-auth) && '
                                           'NEO4J_PASSWORD="$${auth#neo4j/}" cypher-shell -u neo4j "RETURN 1" >/dev/null'],
                                  'interval': '8s', 'timeout': '7s', 'retries': 20}},
    }
    services['private-edge'] = {
        **restricted,
        'image': 'haproxy:3.2-alpine@sha256:7816a9801a6846b912bc37e48529808e2fa87c3c47992441940f68e393ebcfeb',
        'networks': ['memory', 'edge'],
        'ports': [port(settings['neo4j_port'], 7474), port(settings['bolt_port'], 7687),
                  port(settings['context_port'], 8000)],
        'volumes': [volume(SOURCE / 'deploy/private-edge.cfg', '/usr/local/etc/haproxy/haproxy.cfg', True)],
        'depends_on': {'context': {'condition': 'service_healthy'}, 'neo4j': {'condition': 'service_healthy'}},
    }
    if settings['dot']:
        services['dot'] = {**restricted, 'image': CONNECTOR, 'networks': ['clients'],
                           'command': ['node', '/app/dot-bridge.mjs'],
                           'environment': {**matrix, **catalog, 'DOT_STATE': '/state'},
                           'volumes': [volume(state / 'dot/matrix', '/state'), volume(dashboard, '/catalog', True)]}
        if settings['telegram']:
            services['dot']['volumes'].append(volume(data / 'telegram/dot-requests', '/telegram-dot', True))
    telegram_coordinator = telegram_office_agent(registry) if settings['telegram'] else None
    for key, entry in sorted(registry['agents'].items()):
        if entry.get('placement') != 'local':
            continue
        instance = f'engine-{key}'
        private = f'acp-{key}'
        network[private] = {'internal': True}
        network['provider'] = {}
        directory = state / key
        workspace = [volume(directory / 'work', '/workspace')]
        office_worker = key not in {'vps-manager', 'chief-of-staff', 'helper'}
        if office_worker:
            workspace.append(volume(data / 'desktop', '/workspace/desktop'))
        metadata = {'AGENT_USERNAME': key, 'AGENT_NAME': entry['name'], 'AGENT_MODEL': entry['model'],
                    'AGENT_REASONING': entry['effort']}
        services[instance] = {
            **restricted, 'image': engine_image, 'networks': [private, 'provider'], 'profiles': ['agents'],
            'environment': {**metadata, 'AGENT_WORKSPACE_TOOLS': str(office_worker).lower(),
                            'AGENT_INSTANCE_CERTIFICATE_NAMES': f'["{instance}"]'},
            'volumes': [*workspace, volume(directory / 'instance', '/instance'),
                        volume(directory / 'exchange', '/exchange')],
            'healthcheck': health('curl', '-fsS', '--cacert', '/exchange/ca.pem', f'https://{instance}:19443/'),
        }
        worker = {
            **restricted, 'image': CONNECTOR, 'profiles': ['agents'], 'networks': ['clients', 'memory', private],
            'environment': {**matrix, **metadata, **context, **catalog, 'AGENT_STATE': '/state',
                            'AGENT_WORKSPACE': '/workspace', 'AGENT_INSTRUCTIONS': entry['profile']['limits'],
                            'MATRIX_PLANNER': f'@dot:{server}' if settings['dot'] else '',
                            'INSTANCE_ACP_URL': f'wss://{instance}:19443/api/v1/acp'},
            'volumes': [volume(directory / 'matrix', '/state'), volume(directory / 'exchange', '/acp', True),
                        volume(dashboard, '/catalog', True),
                        volume(data / 'context' / f'{key}.token', '/context-token', True)],
            'depends_on': {instance: {'condition': 'service_healthy'}},
        }
        if key == telegram_coordinator:
            worker['environment']['TELEGRAM_OFFICE_SOCKET'] = '/telegram-office/actions.sock'
            worker['environment']['TELEGRAM_HANDOFF_SOCKET'] = '/telegram-handoffs/requests.sock'
            mount = volume(data / 'telegram/office', '/telegram-office', True)
            mount['bind'] = {'create_host_path': False}
            worker['volumes'].append(mount)
            handoffs = volume(data / 'telegram/handoffs', '/telegram-handoffs', True)
            handoffs['bind'] = {'create_host_path': False}
            worker['volumes'].append(handoffs)
        if key == 'chief-of-staff':
            worker.update(command=['node', '/app/chief-of-staff/direct/bridge.mjs'],
                          healthcheck=health('test', '-s', '/tmp/direct-ready'))
            worker['volumes'] += [
                volume(SOURCE / 'integrations/chief-of-staff', '/app/chief-of-staff', True),
                volume(directory / 'matrix', '/publisher'), volume(directory / 'observer', '/direct'),
                volume(data / 'context' / f'{key}.token', '/writer-token', True),
                volume(data / 'context/observation-reader.token', '/reader-token', True),
            ]
            if settings.get('roster_management') and 'hiring-manager' not in registry['agents']:
                socket = volume(f"/run/{settings['project']}-roster.sock", '/run/office-roster.sock', True)
                socket['bind'] = {'create_host_path': False}
                worker['volumes'] += [socket, volume(data / 'roster/bridge-key', '/roster-key', True)]
                worker['environment']['ROSTER_MANAGEMENT'] = 'true'
                worker['environment']['ROSTER_CONFIGURE_COMMAND'] = (
                    f'sudo ./constellation_matrix --data-dir {quote(str(data))} agent configure')
        elif key == 'vps-manager':
            worker.update(user='992:992', command=['node', '/app/root/agent.mjs'],
                          healthcheck=health('test', '-s', '/state/synced'))
            worker['environment']['ROOT_HOST'] = server
            worker['volumes'] = [item for item in worker['volumes'] if item['target'] != '/context-token']
            socket = volume(f"/run/{settings['project']}-root.sock", '/run/executor.sock', True)
            socket['bind'] = {'create_host_path': False}
            worker['volumes'] += [
                volume(SOURCE / 'integrations/root', '/app/root', True),
                volume(directory / 'config.json', '/config.json', True),
                volume(directory / 'operations.json', '/operations.json', True),
                volume(directory / 'gateway-credentials', '/credentials', True),
                volume(data / 'context/vps-manager-ops.token', '/context-token', True),
                volume(data / 'context/vps-manager.token', '/office-token', True),
                socket,
            ]
        else:
            worker.update(healthcheck=health('test', '-s', '/tmp/matrix-agent-ready'))
            worker['volumes'] += workspace
        if key in {'coach', 'hiring-manager', 'scouter'} and entry.get('starter_role') == key:
            worker['environment']['TEAM_ROLE'] = key
            worker['environment']['SCOUT_WEEKLY_UTC'] = settings.get('scout_weekly_utc', 'Mon 09:00')
            worker['environment']['ROSTER_CONFIGURE_COMMAND'] = './constellation_matrix --data-dir ' + quote(str(data)) + ' agent configure'
            worker['volumes'].append(volume(SOURCE / 'integrations/chief-of-staff', '/app/chief-of-staff', True))
            credential = data / 'roster' / (key + '.key')
            if settings.get('roster_management') and credential.is_file():
                socket = volume(f"/run/{settings['project']}-roster.sock", '/run/office-roster.sock', True)
                socket['bind'] = {'create_host_path': False}
                worker['volumes'] += [socket, volume(credential, '/roster-key', True)]
                worker['environment']['ROSTER_MANAGEMENT'] = 'true'
        if key == 'scouter' and entry.get('starter_role') == key:
            worker['networks'].append('provider')
            services[instance]['environment']['TEAM_ROLE'] = 'scouter'
        if key == 'helper':
            support = volume(data / 'support', '/support', True)
            worker['volumes'].append(support)
            dm_volumes = [volume(directory / 'dm', '/state') if item['target'] == '/state' else item
                          for item in worker['volumes'] if item['target'] != '/context-token']
            services['helper-dm'] = {**worker, 'command': ['node', '/app/helper-dm.mjs'],
                                     'volumes': dm_volumes, 'networks': ['clients', private]}
        if registry.get('team_version') == 2 and key != 'helper':
            grant_mount = volume(directory / 'tool-credentials', '/tool-credentials', True)
            services[instance]['environment'].update(catalog, MATRIX_SERVER_NAME=server)
            services[instance]['volumes'] += [volume(dashboard, '/catalog', True), grant_mount]
            worker['volumes'].append(grant_mount)
        services[key] = worker
    if registry['agents'] or settings['telegram']:
        network['workshop-targets'] = {'internal': workshop_internal}
        workshop_networks = ['workshop-targets', *(name for name in network if name.startswith('acp-'))]
        if workshop_internal and any(entry['placement'] == 'remote' for entry in registry['agents'].values()):
            # Telegram may already be attached to workshop-targets when a remote worker is selected.
            network['workshop-egress'] = {}
            workshop_networks.append('workshop-egress')
        services['persona-workshop'] = {
            **restricted, 'image': 'constellation-persona-workshop:0.7.4',
            'build': str(SOURCE / 'integrations/chief-of-staff/persona'),
            'networks': workshop_networks,
            'profiles': ['agents'], 'volumes': [volume(data / ('persona-workshop' if registry.get('team_version') == 2 else 'state/chief-of-staff/persona'), '/data')],
            'healthcheck': health('persona-workshop', '--home', '/data', 'api', 'GET', '/api/v1/status'),
        }
    return {'name': settings['project'], 'services': services, 'networks': network,
            'volumes': {}}


def health(*command):
    return {'test': ['CMD', *command], 'interval': '5s', 'timeout': '4s', 'retries': 24}
